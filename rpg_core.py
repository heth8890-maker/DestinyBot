
import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
_uuid = uuid   # alias dùng bởi migration functions (moved from rpg_database.py)

import pymongo.errors

from database_helper import (
    load_core_data,
    save_core_data,
    _with_retry,          # sync — dùng cho load_data()
    MAX_RETRIES as _MAX_RETRIES,
    RETRY_DELAY as _RETRY_DELAY,  # giây giữa các lần retry
)

from rpg_item import (
    ITEMS,
    BASE_RARITY_RATES,
    get_item_by_id,
    _pick_item_from_rarity,
)
from rpg_weapon_data import (
    WEAPONS,
    WEAPON_EFFECTS,
    CRATES,
    RARITY_COLOR,
    RARITY_LABEL,
    get_weapon_by_id,
    get_crate_by_id,
    roll_weapon,
    DARK_CRATE_WEAPON,
)

from rpg_instance import (
    roll_quality,
    roll_passive,
    resolve_passive,
    build_weapon_effects,
    decrease_durability,
    fmt_instance_info,
    quality_label,
    DURABILITY_BY_RARITY,
    quality_color,
)

from rpg_effect import (
    parse_effects,
    roll_hunt_items,
    calc_hunt_cooldown,
    calc_sell_value,
)

logger = logging.getLogger(__name__)

__all__ = [
    "load_data", "save_data", "get_user",
    "get_item_by_id", "get_weapon_by_id", "get_crate_by_id",
    "add_item", "remove_item",
    "add_weapon", "remove_weapon_from_bag",
    "equip_weapon", "unequip_weapon",
    "parse_effects", "roll_hunt_items", "handle_egg",
    "calc_sell_value", "calc_hunt_cooldown",
    "roll_weapon",
    "ITEMS", "WEAPONS", "CRATES", "RARITY_COLOR", "RARITY_LABEL",
    #  Weapon Identity Layer 
    "get_base_id",                          # canonical ID resolver
    "WeaponID", "WeaponEntity", "get_weapon_entity",
    "ensure_weapon_uid",                    # promote base weapon → UID (lazy, no upgrade data)
    "ensure_upgrade_entry",                 # create upgraded_weapons entry ONLY when upgrading
    "get_user_lock",
    #  integrity 
    "_validate_data_integrity",
    "DARK_CRATE_WEAPON",
    #  migration functions (moved from rpg_database.py) 
    "WEAPON_LEVEL_CAP",
    "RARITY_EXP_WEIGHT",
    "exp_to_next",
    "calc_hunt_exp",
    "grant_weapon_exp",
    "make_weapon_instance",
    "migrate_upgraded_weapons",
    "migrate_all_weapons_to_uid",
    "migrate_weapon_instance_fields",
]


# 
#  CANONICAL ID RESOLVER — THE ONLY PERMITTED CALL SITE FOR .split("-")
# 

def get_base_id(wid: str) -> str:
    """
    Extract the base weapon ID from any weapon identifier.

    This is THE ONLY function in the entire project allowed to call .split('-').
    All other modules MUST call get_base_id() instead of doing str.split('-') directly.

    Examples: get_base_id = ("467")            → "467"   (bare base ID — passthrough)
        get_base_id("467-A3B2C1")     → "467"   (Unique ID)
        get_base_id("467-A3B2C1_fix") → "467"   (repaired UID — still correct)
    """
    return str(wid).split("-")[0]


#  v5.5: Deterministic stat seed secret 
# Override via environment: export RPG_WEAPON_SECRET"your-secret"
# MUST be changed from default in production!
GLOBAL_SECRET: bytes = os.environb.get(
    b"RPG_WEAPON_SECRET",
    b"v5.5-arch-default-CHANGE-IN-PROD",
)


# 
#  CONCURRENCY — per-user lock + global save lock
# 

_user_locks: dict[str, asyncio.Lock] = {}

# Single global lock — only ONE save_data() runs at a time.
# Prevents torn writes when two commands race to persist state simultaneously.
_data_lock: asyncio.Lock = asyncio.Lock()

# Rolling RAM snapshots. Written ONLY after a successful DB write.
# On catastrophic save failure, the last good snapshot is used to roll back RAM.
_good_snapshots: list[dict] = []   # max 3; pop(0) when full


def get_user_lock(uid: str) -> asyncio.Lock:
    """
    Returns the asyncio.Lock for user_id — creates one if absent.
    Thread-safe within a single-threaded asyncio event loop.

    Usage: async with get_user_lock = (uid):
            data = load_data(uid)
            user = get_user(uid, data)
            # ... modify user ...
            await save_data(data, uid)
    """
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]


# 
#  WEAPON IDENTITY LAYER — WeaponID · WeaponEntity · get_weapon_entity
# 

class WeaponID:
    """
    SINGLE SOURCE OF TRUTH for UID string processing.

    THIS IS THE ONLY PLACE in the entire project allowed to call .split("-").
    All other modules MUST use WeaponID.parse() / WeaponID.is_unique().

    UID format:
        Base ID    : "467"              (stack weapon — valid, not legacy)
        Unique ID = : "467-A3B2C1"      (instance of the same weapon type)
        Repaired = : "467-A3B2C1_fix_xxxxxx" = (duplicate collision repair)

    Both forms are first-class citizens. = UID is an EXTENSION of base_id,
    not a separate weapon type. = Use get_base_id() to resolve either form.
    """

    @staticmethod
    def parse(uid: str) -> tuple[str, bool]:
        """
        Parse UID → (base_id, is_unique).

        Delegates to get_base_id() — no direct .split('-') here.

        Examples:  = "467-A3B2C1"          → ("467", True)
            "467-A3B2C1_fix_ab"   → ("467", True)
            "467"                 → ("467", False)   ← stack weapon (valid)
            "" / None             → ("",    False)
        """
        if not isinstance(uid, str) or not uid:
            return ("", False)
        if "-" in uid:
            return (get_base_id(uid), True)
        return (uid, False)

    @staticmethod
    def is_unique(uid: str) -> bool:
        """True if uid is a Unique ID (has '-' separator)."""
        return isinstance(uid, str) and "-" in uid


#  v5.5: Deterministic per-stat seed 

def _weapon_stat_seed(uid: str, stat_key: str) -> int:
    """
    HMAC-SHA256(GLOBAL_SECRET, f"{uid}:{stat_key}") → deterministic int seed.

    Same uid+stat_key always yields the same float variance — no storage needed.
    Changing GLOBAL_SECRET rotates all rolls across all weapons.
    """
    mac = hmac.new(
        GLOBAL_SECRET,
        f"{uid}:{stat_key}".encode("utf-8"),
        hashlib.sha256,
    )
    # Take the first 8 bytes of the digest as a big-endian unsigned int
    return int.from_bytes(mac.digest()[:8], "big")


class WeaponEntity:
    """
    SINGLE SOURCE OF TRUTH for the UI layer.

    Read-only — never mutates user data directly.
    All commands MUST use WeaponEntity; no direct access to WEAPONS dict
    or upgraded_weapons from UI code.

    Attributes: uid = — Stored weapon identifier: "467-A3B2C1" (UID) or "467" (base_id)
        base_data = — Weapon dict from WEAPONS / RARE_CRATE_WEAPONS / SPECIAL_WEAPONS
        upgrade_data — Entry in user["upgraded_weapons"], or None if not yet upgraded.
                       None is the normal state for unupgraded weapons — it is NOT an error.
    """

    def __init__(self, uid: str, base_data: dict,
                 upgrade_data: dict | None = None,
                 instance_data: dict | None = None):
        self.uid = uid
        self.base_data = base_data
        self.upgrade_data = upgrade_data   # backward compat, sẽ bỏ dần
        self.instance_data = instance_data  # hệ thống mới

    #  v5.5: Deterministic stat rolls 

    def get_rolled_stats(self) -> dict:
        """
        Apply a ±10% variance to every base effect using a deterministic HMAC seed.

        The same uid + stat_key always produces the same multiplier — no random
        state is stored in DB. Changing GLOBAL_SECRET rotates all rolls.

        Returns a dict of {stat_key: rolled_value} for display / combat use.
        """
        effects = self.base_data.get("effects", {})
        rolled: dict = {}

        for stat_key, base_val in effects.items():
            # Deterministic seed: unique per (weapon-instance, stat)
            seed = _weapon_stat_seed(self.uid, stat_key)
            rng = random.Random(seed)

            # ±10% uniform variance
            factor = rng.uniform(0.90, 1.10)

            if isinstance(base_val, float):
                rolled[stat_key] = round(base_val * factor, 6)
            elif isinstance(base_val, int):
                rolled[stat_key] = max(1, round(base_val * factor))
            else:
                rolled[stat_key] = base_val   # passthrough for non-numeric

        return rolled


def get_weapon_entity(user: dict, uid: str) -> "WeaponEntity | None":
    """
    SINGLE ENTRY POINT for weapon data — used by all UI commands.

    Rules:  = - Never raises — returns None instead of crashing.
    - O(1) upgraded_weapons lookup via dict comprehension.
    - Works with both base ID ("467") and unique ID ("467-A3B2C1").

    Args: user = : dict from get_user()
        uid: = base ID ("467") or unique ID ("467-A3B2C1")

    Returns: WeaponEntity if base_data found, None otherwis = e.
    """
    if not isinstance(uid, str) or not uid:
        return None

    base_id, _ = WeaponID.parse(uid)
    if not base_id:
        return None

    base_data = get_weapon_by_id(base_id)
    if base_data is None:
        logger.debug(f"get_weapon_entity: no base_data for base_id'{base_id}'")
        return None

    # O(1) lookup — never scan the full list on every call
    upgraded_dict: dict[str, dict] = {
        w["uid"]: w
        for w in user.get("upgraded_weapons", [])
        if isinstance(w, dict) and "uid" in w
    }
    upgrade_data = upgraded_dict.get(uid)

    wi_map = {
        wi["uid"]: wi
        for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    instance_data = wi_map.get(uid)

    return WeaponEntity(uid, base_data, upgrade_data,
                        instance_data=instance_data)


# 
#  INTERNAL HELPERS — emergency dump
# 

async def _async_with_retry(fn, *args, **kwargs):
    """
    Async wrapper cho pymongo operations bên trong save_data().

    Dùng asyncio.sleep thay time.sleep để không block event loop trong lúc
    retry — đặc biệt quan trọng khi gọi từ bên trong _data_lock, vì
    time.sleep ở đây sẽ treo toàn bộ bot trong suốt thời gian retry.

    fn vẫn là hàm sync (pymongo driver dùng blocking I/O) — chạy trực tiếp
    trên event loop thread. Nếu sau này chuyển sang Motor (async pymongo),
    chỉ cần thêm await fn() ở đây.

    Raises: Exception cuối cùng nếu tất cả _MAX_RETRIES lần đều thất bại.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (
            pymongo.errors.AutoReconnect,
            pymongo.errors.ServerSelectionTimeoutError,
            pymongo.errors.NetworkTimeout,
        ) as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                raise
            wait = _RETRY_DELAY * attempt
            logger.warning(
                "[ASYNC_RETRY] attempt %d/%d failed (%s) — retry sau %.1fs",
                attempt, _MAX_RETRIES, exc, wait,
            )
            await asyncio.sleep(wait)   # ← không block event loop
    raise last_exc  # mypy safety (unreachable khi _MAX_RETRIES >= 1)

def _emergency_dump(data: dict) -> None:
    """
    Last resort khi cả save_data() lẫn MongoDB đều thất bại hoàn toàn.
    Ghi một file JSON có timestamp để admin đọc log và restore thủ công.

    LƯU Ý (Render / ephemeral filesystem):
        File này sẽ mất sau khi container restart — đây là hành vi đúng.
        Mục đích là để admin đọc log trước khi restart, KHÔNG phải auto-restore.
        Nếu cần persistent backup, forward log sang external sink (Papertrail v.v.).
    """
    try:
        ts = int(time.time())
        emg_path = f"rpg_data.EMERGENCY_{ts}.json"
        with open(emg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.critical(
            f"🚨 EMERGENCY DUMP: {len(data)} keys → {emg_path} | "
            f"MongoDB unreachable — check MONGO_URI and restore manually!"
        )
    except Exception as e:
        logger.critical(f"🚨 EMERGENCY DUMP ALSO FAILED: {e} — DATA MAY BE LOST!")


# 
#  v5.5: SELF-HEALING INTEGRITY VALIDATOR
# 

def _validate_data_integrity(data: dict) -> tuple[bool, list[str]]:
    """
    Scan structural integrity across ALL users. = DETECTION ONLY — never mutates data.

    Phase 1 — Global duplicate UID detection:
        Builds a global uid → "user_id:source" map.
        Any UID that appears more than once across the entire dataset is flagged.

    NOTE: UIDs that have NO entry in user["upgraded_weapons"] are VALID.
    A UID is simply an instance identifier; upgrade data is created lazily,
    only when the weapon is actually upgraded. = Flagging missing upgrade
    entries would produce false positives and is NOT performed here.

    NOTE (v1.8): "upgraded_weapons" is a reserved key in the data dict holding
    the global weapons dict from MongoDB. = It is automatically skipped so it
    is never mistaken for a user record.

    Returns:  = (is_valid, audit_log) — is_valid is True only if zero issues were found.
    """
    audit: list[str] = []
    issues: int = 0

    #  Phase 1: Collect all Unique IDs globally; detect duplicates 
    global_uids: dict[str, str] = {}   # uid → "user_id:source" (first occurrence)

    for user_id, user in data.items():
        # v1.8: skip the reserved global key — it's a dict but not a user record
        if user_id == "upgraded_weapons":
            continue

        if not isinstance(user, dict):
            continue

        candidates: list[tuple[str, str]] = []
        for uid in user.get("weapons", []):
            if isinstance(uid, str) and WeaponID.is_unique(uid):
                candidates.append(("weapons", uid))
        for uid in user.get("equipped", []):
            if isinstance(uid, str) and WeaponID.is_unique(uid):
                candidates.append(("equipped", uid))

        for source, uid in candidates:
            if uid in global_uids:
                issues += 1
                msg = (
                    f"DUPLICATE UID '{uid}' found on user{user_id} ({source}); "
                    f"first seen on {global_uids[uid]}"
                )
                audit.append(msg)
                logger.warning(f"[Validator] {msg}")
            else:
                global_uids[uid] = f"{user_id}:{source}"

    is_valid = (issues == 0)
    if not is_valid:
        logger.warning(
            f"[Validator] Integrity scan complete — {issues} issue(s) found (no repairs made)."
        )
    else:
        logger.debug("[Validator] Integrity scan clean — no issues found.")

    return is_valid, audit


# 
#  SALVAGE HELPERS (unchanged)
# 

def _salvage_numeric(user: dict, key: str, default: int | float, uid: str) -> None:
    """
    Ensure user[key] is a number.
    Missing/None → assign default.
    str/float    → convert (never reset straight to 0).
    Cannot convert → assign default + log.
    """
    val = user.get(key)
    if val is None or key not in user:
        user[key] = default
        return
    if isinstance(val, (int, float)):
        return
    try:
        converted = int(float(str(val)))
        user[key] = converted
        logger.warning(f"User {uid}: '{key}' = {val!r} ({type(val).__name__}) → converted {converted}")
    except (TypeError, ValueError):
        logger.warning(f"User {uid}: '{key}' = {val!r} not convertible → default{default}")
        user[key] = default


def _salvage_list_of_str(user: dict, key: str, uid: str) -> None:
    """
    Ensure user[key] is list[str].
    Non-list → reset [].
    List = → keep only valid str elements.

    ⚠️ = Use ONLY for list[str] fields (e.g. "weapons").
        Do NOT use for "upgraded_weapons" (list[dict]) — use _salvage_upgraded_weapons.
    """
    raw = user.get(key)
    if raw is None or key not in user:
        user[key] = []
        return
    if not isinstance(raw, list):
        logger.warning(f"User {uid}: '{key}' is {type(raw).__name__} → reset []")
        user[key] = []
        return
    cleaned = [item for item in raw if isinstance(item, str) and item]
    dropped = len(raw) - len(cleaned)
    if dropped:
        logger.warning(
            f"User {uid}: '{key}' filtered {dropped}/{len(raw)} invalid elements "
            f"(kept {len(cleaned)})"
        )
    user[key] = cleaned


def _salvage_upgraded_weapons(user: dict, uid_str: str) -> None:
    """
    Ensure user['upgraded_weapons'] is list[dict].
    Each entry must have at least 'uid' and 'base_id'.

    FIX (v3): _salvage_list_of_str was wrongly called for this field — it wiped all
    dicts since it only keeps str elements. This function replaces that call.
    """
    raw = user.get("upgraded_weapons")
    if raw is None or "upgraded_weapons" not in user:
        user["upgraded_weapons"] = []
        return
    if not isinstance(raw, list):
        logger.warning(
            f"User {uid_str}: 'upgraded_weapons' is {type(raw).__name__} → reset []"
        )
        user["upgraded_weapons"] = []
        return
    cleaned = [
        item for item in raw
        if isinstance(item, dict) and "uid" in item and "base_id" in item
    ]
    dropped = len(raw) - len(cleaned)
    if dropped:
        logger.warning(
            f"User {uid_str}: 'upgraded_weapons' filtered {dropped}/{len(raw)} invalid entries "
            f"(missing 'uid' or 'base_id')"
        )
    user["upgraded_weapons"] = cleaned


def _salvage_inv(user: dict, uid: str) -> None:
    """
    Ensure user["inv"] is dict{str: int > 0} — salvage per-entry, never wipe the whole inv.

    Per-entry strategy:
      value None      → 1   (item exists, unknown qty → assume 1)
      value float     → int(round(value))
      value str-digit → int(value)
      value negative = → 0
      value unrecoverable → drop that entry only
      key non-str     → drop that entry
    """
    raw = user.get("inv")
    if raw is None or "inv" not in user:
        user["inv"] = {}
        return
    if not isinstance(raw, dict):
        logger.warning(
            f"User {uid}: 'inv' is {type(raw).__name__} → reset {{}} "
            f"| Raw (first 120): {str(raw)[:120]}"
        )
        user["inv"] = {}
        return

    fixed = {}
    bad_count = 0

    for item_id, qty in raw.items():
        if not isinstance(item_id, str) or not item_id:
            bad_count += 1
            logger.warning(f"User {uid}: inv key {item_id!r} invalid → drop entry")
            continue
        if qty is None:
            fixed[item_id] = 1
            logger.warning(f"User {uid}: inv['{item_id}'] = None → set 1")
            continue
        if isinstance(qty, int):
            fixed[item_id] = max(0, qty)
            if qty < 0:
                logger.warning(f"User {uid}: inv['{item_id}'] = {qty} < 0 → set 0")
            continue
        if isinstance(qty, float):
            converted = max(0, int(round(qty)))
            fixed[item_id] = converted
            logger.warning(f"User {uid}: inv['{item_id}'] = {qty} (float) → {converted}")
            continue
        if isinstance(qty, str):
            try:
                converted = max(0, int(float(qty)))
                fixed[item_id] = converted
                logger.warning(f"User {uid}: inv['{item_id}'] = '{qty}' (str) → {converted}")
                continue
            except (ValueError, TypeError):
                pass
        bad_count += 1
        logger.warning(f"User {uid}: inv['{item_id}'] = {qty!r} cannot salvage → drop entry")

    if bad_count:
        logger.warning(f"User {uid}: inv salvage — {bad_count} entries dropped, {len(fixed)} kept")
    user["inv"] = fixed


def _salvage_passives(user: dict, uid: str) -> None:
    """Ensure user['passives'] is a dict."""
    raw = user.get("passives")
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(f"User {uid}: 'passives' is {type(raw).__name__} → reset {{}}")
        user["passives"] = {}


def _salvage_hunt_log(user: dict, uid: str) -> None:
    """Ensure user['hunt_log'] is a list."""
    raw = user.get("hunt_log")
    if not isinstance(raw, list):
        if raw is not None:
            logger.warning(f"User {uid}: 'hunt_log' is {type(raw).__name__} → reset []")
        user["hunt_log"] = []


def _salvage_equipped(user: dict, uid: str) -> None:
    """
    Ensure user["equipped"] is list[3] where each element is None or a valid str.

    Rules: Non = -list         → [None, None, None]
      Too few (< 3)    → pad with None (do NOT drop existing weapons)
      Too many (> 3)   → slice [:3], return valid str elements at 3+ to weapons bag
      Wrong-type slot = → set None at that slot
    """
    raw = user.get("equipped")
    weapons = user.get("weapons", [])
    if not isinstance(weapons, list):
        weapons = []

    if not isinstance(raw, list):
        logger.warning(f"User {uid}: 'equipped' is {type(raw).__name__} → reset [None,None,None]")
        user["equipped"] = [None, None, None]
        return

    eq = list(raw)   # clone — do NOT mutate while iterating

    while len(eq) < 3:
        eq.append(None)

    if len(eq) > 3:
        overflow = [e for e in eq[3:] if isinstance(e, str) and e]
        if overflow:
            weapons.extend(overflow)
            logger.warning(
                f"User {uid}: equipped has {len(eq)} slots (> 3), "
                f"trimmed to 3; returned {overflow} to weapons bag"
            )
        eq = eq[:3]

    for i, slot in enumerate(eq):
        if slot is None or (isinstance(slot, str) and slot):
            continue
        logger.warning(f"User {uid}: equipped[{i}] = {slot!r} ({type(slot).__name__}) → None")
        eq[i] = None

    user["equipped"] = eq
    user["weapons"] = weapons


# 
#  v5.5: LEGACY WEAPON MIGRATION (called from get_user)
# 

def _migrate_legacy_weapons(user: dict, uid_str: str) -> None:
    """
    OPT-IN UTILITY — convert bare base IDs in user["weapons"] to UIDs.

    NOT called automatically from get_user(). = Invoke explicitly only when
    you want to bulk-promote a user's entire bag (e.g. a one-off admin command).
    For on-demand promotion during upgrade, use ensure_weapon_uid() instead.

    Promotes base_id → UID in-place. = Does NOT create upgraded_weapons entries;
    upgrade data is created lazily when the player upgrades via ensure_upgrade_entry().

    Technique:  = - Clone the list before iterating (spec requirement).
    - Track already-assigned UIDs to prevent collisions within this user.
    - Deduplicate the final list (list-to-set-to-list, order-preserving).
    - Runs AFTER _salvage_list_of_str so only valid str entries exist.
    """
    raw_weapons = list(user.get("weapons", []))   # ← CLONE: do NOT iterate original in-place

    migrated: list[str] = []
    changed: bool = False
    # Seed the seen set with existing unique IDs + equipped to avoid collisions
    assigned: set[str] = {
        w for w in raw_weapons if WeaponID.is_unique(w)
    } | {
        e for e in user.get("equipped", []) if isinstance(e, str) and WeaponID.is_unique(e)
    }

    for wid in raw_weapons:
        if WeaponID.is_unique(wid):
            # Already a proper UID — keep untouched
            migrated.append(wid)
            continue

        #  Legacy bare base ID detected → generate UID 
        suffix = uuid.uuid4().hex[:6].upper()
        new_uid = f"{wid}-{suffix}"

        # Collision guard (extremely rare, but correct)
        while new_uid in assigned:
            suffix = uuid.uuid4().hex[:6].upper()
            new_uid = f"{wid}-{suffix}"

        assigned.add(new_uid)
        migrated.append(new_uid)
        changed = True

        # NOTE: no upgraded_weapons entry is created here.
        # Upgrade data is created lazily by ensure_upgrade_entry() when the
        # player actually upgrades this weapon.

        logger.info(
            f"_migrate_legacy_weapons: User {uid_str}: "
            f"legacy '{wid}' → UID '{new_uid}'"
        )

    if changed:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for wid in migrated:
            if wid not in seen:
                seen.add(wid)
                unique.append(wid)

        user["weapons"] = unique
        logger.info(
            f"_migrate_legacy_weapons: User {uid_str}: "
            f"migration done — {len(unique)} weapons in bag."
        )


# 
#  v1.8: VALIDATE USER — chạy sau khi load từ MongoDB
# 

def _validate_user(user_data: dict) -> dict:
    """
    Kiểm tra và sửa chữa từng field của user_data sau khi load từ MongoDB.

    Strategy: Salvage = -First — chỉ reset field nào thực sự sai,
    không wipe toàn bộ user trừ khi user_data không phải dict.

    Fields được validate:
        inv = → dict
        weapons = → list[str]
        upgraded_weapons → list[dict] (mỗi entry có 'uid' + 'base_id')
        equipped = → list, len = 3, mỗi slot là None | str
        hunt_cd = → numeric (int/float)
        crate_cd = → numeric (int/float)
        passives = → dict
        hunt_log = → list

    Returns: user_data đã được sửa chữa in = -place (cùng object).
        Nếu user_data không phải dict → trả về _make_default_user().
    """
    if not isinstance(user_data, dict):
        logger.warning("[VALIDATE] user_data không phải dict → fallback default_user")
        return _make_default_user()

    uid = user_data.get("_id", "<unknown>")

    #  inventory → dict 
    if not isinstance(user_data.get("inv"), dict):
        logger.warning("[VALIDATE] uid%s: 'inv' không hợp lệ → reset {}", uid)
        user_data["inv"] = {}

    #  weapons → list[str] 
    raw_weapons = user_data.get("weapons")
    if not isinstance(raw_weapons, list):
        logger.warning("[VALIDATE] uid%s: 'weapons' không phải list → reset []", uid)
        user_data["weapons"] = []
    else:
        cleaned = [w for w in raw_weapons if isinstance(w, str) and w]
        if len(cleaned) != len(raw_weapons):
            logger.warning(
                "[VALIDATE] uid%s: 'weapons' lọc %d/%d phần tử không hợp lệ",
                uid, len(raw_weapons) - len(cleaned), len(raw_weapons),
            )
        user_data["weapons"] = cleaned

    #  upgraded_weapons → list[dict] với 'uid' + 'base_id' 
    raw_uw = user_data.get("upgraded_weapons")
    if not isinstance(raw_uw, list):
        logger.warning("[VALIDATE] uid%s: 'upgraded_weapons' không phải list → reset []", uid)
        user_data["upgraded_weapons"] = []
    else:
        valid_uw = [
            e for e in raw_uw
            if isinstance(e, dict) and "uid" in e and "base_id" in e
        ]
        if len(valid_uw) != len(raw_uw):
            logger.warning(
                "[VALIDATE] uid%s: 'upgraded_weapons' loại bỏ %d entry thiếu 'uid'/'base_id'",
                uid, len(raw_uw) - len(valid_uw),
            )
        user_data["upgraded_weapons"] = valid_uw

    #  equipped → list, độ dài 3, mỗi slot None | str 
    raw_eq = user_data.get("equipped")
    if not isinstance(raw_eq, list):
        logger.warning("[VALIDATE] uid%s: 'equipped' không phải list → reset [None,None,None]", uid)
        user_data["equipped"] = [None, None, None]
    else:
        eq = list(raw_eq)
        # Pad thiếu
        while len(eq) < 3:
            eq.append(None)
        # Cắt dư — trả về weapon hợp lệ về bag
        if len(eq) > 3:
            overflow = [s for s in eq[3:] if isinstance(s, str) and s]
            if overflow:
                user_data.setdefault("weapons", []).extend(overflow)
                logger.warning("[VALIDATE] uid%s: equipped > 3 slots, trả %s về bag", uid, overflow)
            eq = eq[:3]
        # Sửa từng slot
        for i, slot in enumerate(eq):
            if slot is None or (isinstance(slot, str) and slot):
                continue
            logger.warning("[VALIDATE] uid%s: equipped[%d]%r không hợp lệ → None", uid, i, slot)
            eq[i] = None
        user_data["equipped"] = eq

    #  cooldowns → numeric 
    for cd_key in ("hunt_cd", "crate_cd"):
        val = user_data.get(cd_key)
        if not isinstance(val, (int, float)):
            logger.warning("[VALIDATE] uid%s: '%s'%r không hợp lệ → 0", uid, cd_key, val)
            user_data[cd_key] = 0

    #  passives → dict 
    if not isinstance(user_data.get("passives"), dict):
        user_data["passives"] = {}

    #  hunt_log → list 
    if not isinstance(user_data.get("hunt_log"), list):
        user_data["hunt_log"] = []

    #  weapon_instances → list[dict] với 'uid' + 'base_id' 
    raw_wi = user_data.get("weapon_instances")
    if not isinstance(raw_wi, list):
        logger.warning(
            "[VALIDATE] uid%s: 'weapon_instances' không phải list → reset []", uid
        )
        user_data["weapon_instances"] = []
    else:
        valid_wi = [
            e for e in raw_wi
            if isinstance(e, dict) and "uid" in e and "base_id" in e
        ]
        if len(valid_wi) != len(raw_wi):
            logger.warning(
                "[VALIDATE] uid%s: loại bỏ %d weapon_instance không hợp lệ",
                uid, len(raw_wi) - len(valid_wi),
            )
        user_data["weapon_instances"] = valid_wi

    return user_data


# 
#  LOAD — MongoDB (thay thế JSON multi-tier recovery)
# 

def load_data(user_id=None) -> dict:
    """
    Tải dữ liệu user từ MongoDB.

    Không bao giờ raise — trả về {} nếu DB lỗi hoàn toàn.

    Args: user_id = : Discord user ID (int hoặc str).
                 Nếu None → trả về {} ngay (no-op).

    Returns:
        {
            uid_str: user_dict,           # dùng bởi get_user(uid, data)
            "upgraded_weapons": {},       # key tương thích — luôn rỗng (dead code)
        }

    Lưu ý: "upgraded_weapons" global là dead code — global_metadata collection
        không tồn tại, global_uw đã bị xoá khỏi save_data(). Key giữ lại chỉ
        để không break callers cũ tham chiếu data["upgraded_weapons"].

    Callers điển hình:
        async with get_user_lock(uid):
            data = load_data(uid)
            user = get_user(uid, data)
            # ... modify user ...
            await save_data(data, uid)
    """
    if user_id is None:
        logger.debug("[LOAD] user_id=None → trả về dict rỗng.")
        return {}

    uid_str = str(user_id)

    try:
        result = _with_retry(load_core_data, uid_str)
    except Exception as exc:
        logger.error(
            "[LOAD] Không thể tải uid%s sau %d lần thử: %s",
            uid_str, _MAX_RETRIES, exc, exc_info=True,
        )
        return {}

    if not isinstance(result, dict):
        logger.warning(
            "[LOAD] load_core_data trả về %s cho uid%s → dict rỗng.",
            type(result).__name__, uid_str,
        )
        return {}

    # load_core_data trả về {"user": {...}, "upgraded_weapons": {...}}
    user_doc = result.get("user") or {}
    global_weapons = result.get("upgraded_weapons") or {}

    if not isinstance(user_doc, dict):
        logger.warning("[LOAD] 'user' không phải dict cho uid%s → dict rỗng.", uid_str)
        user_doc = {}

    # Validate + salvage trước khi trả về — sửa field lỗi, không crash
    _validate_user(user_doc)

    logger.debug("[LOAD] ✅ uid%s loaded (%d keys)", uid_str, len(user_doc))

    return {
        uid_str:            user_doc,
        "upgraded_weapons": global_weapons,   # key tương thích với JSON cũ
    }


#  v1.8: SAVE — MongoDB (thay thế JSON atomic save)
# 

async def save_data(data: dict, user_id=None) -> bool:
    """
    Lưu dữ liệu user + global upgraded_weapons vào MongoDB.

    Pipeline (inside _data_lock):
        1. Validate tham số đầu vào
        2. Lấy user_data từ data dict
        3. Gọi save_core_data(uid_str, user_data) qua _async_with_retry
           • Dùng asyncio.sleep giữa các retry → không block event loop
           • user_data["upgraded_weapons"] (list[dict]) → economy collection ($set)
        4. Commit RAM snapshot sau khi DB xác nhận thành công
        5. Rollback RAM từ snapshot + emergency dump nếu tất cả retry thất bại

    Args: data = :    dict trả về từ load_data() — đã được modify bởi caller.
        user_id: Discord user ID = (int hoặc str).
                 Nếu None → trả về False ngay (no-op).

    Returns: True = — lưu thành công.
        False — lưu thất bại (đã log, KHÔNG crash bot).

    Concurrency: get_user_lock = (uid) ở caller đảm bảo một user không bị modify song song.
        _data_lock bên trong đảm bảo chỉ một save_data() ghi DB tại một thời điểm.
        save_core_data dùng upsert + $set nên an toàn với race condition còn lại.

    Callers điển hình:
        async with get_user_lock(uid):
            data = load_data(uid)
            user = get_user(uid, data)
            # ... modify user ...
            await save_data(data, uid)
    """
    global _good_snapshots

    if not isinstance(data, dict):
        logger.error("[SAVE] data không phải dict (%s) — bỏ qua.", type(data).__name__)
        return False

    if user_id is None:
        logger.debug("[SAVE] user_id=None → bỏ qua.")
        return False

    uid_str = str(user_id)
    user_data = data.get(uid_str)

    if not isinstance(user_data, dict) or not user_data:
        logger.warning(
            "[SAVE] Không tìm thấy user_data hợp lệ cho uid%s — bỏ qua.", uid_str
        )
        return False

    #  Integrity validation (detect only — no mutations) 
    is_valid, audit_log = _validate_data_integrity(data)
    if audit_log:
        for entry in audit_log:
            logger.info(f"[Integrity Audit] {entry}")

    # user_data["upgraded_weapons"] là per-user list[dict], đã nằm trong user_data,
    # được lưu trực tiếp vào economy collection qua $set trong save_core_data.
    # global_metadata collection không tồn tại — global_uw là dead code, đã xoá.

    async with _data_lock:   # Một save tại một thời điểm — tránh torn writes
        try:
            await _async_with_retry(save_core_data, uid_str, user_data)
            logger.debug("[SAVE] ✅ uid%s lưu thành công.", uid_str)

            # Commit RAM snapshot sau khi DB xác nhận
            snapshot = copy.deepcopy(data)
            _good_snapshots.append(snapshot)
            if len(_good_snapshots) > 3:
                _good_snapshots.pop(0)   # evict the oldest snapshot

            return True

        except Exception as exc:
            logger.error(
                "[SAVE] ❌ Không thể lưu uid%s sau %d lần thử: %s",
                uid_str, _MAX_RETRIES, exc, exc_info=True,
            )

            #  RAM Rollback — khôi phục về snapshot cuối cùng tốt nhất 
            if _good_snapshots:
                last_good = _good_snapshots[-1]
                data.clear()
                data.update(last_good)   # mutates the dict in-place (caller sees the rollback)
                logger.warning(
                    "[SAVE] 🔄 RAM ROLLBACK: khôi phục từ snapshot (%d keys).",
                    len(last_good),
                )
            else:
                logger.critical(
                    "[SAVE] 🚨 Không có snapshot để rollback — "
                    "state có thể không nhất quán. Nên restart bot."
                )

            # Emergency dump — data không được mất hoàn toàn
            _emergency_dump(data)
            return False


# ─────────────────────────────────────────────
# MIGRATION FUNCTIONS (moved from rpg_database.py)
# ─────────────────────────────────────────────
log = logger   # bridge: migration functions dùng 'log', rpg_core dùng 'logger'

WEAPON_LEVEL_CAP = 30

# EXP nhận được khi hunt theo độ hiếm của item nhặt được.
# calc_hunt_exp() dùng dict này để tính tổng EXP cho một lần hunt.
RARITY_EXP_WEIGHT: dict[str, int] = {
    "common":    3,
    "uncommon":  8,
    "rare":      12,
    "epic":      32,
    "legendary": 128,
}


def exp_to_next(level: int) -> int:
    """EXP cần để lên level tiếp theo. Công thức: level * 40 + 40."""
    level = min(max(1, level), WEAPON_LEVEL_CAP)
    return level * 40 + 40


def calc_hunt_exp(found_items: list) -> int:
    """
    Tính tổng EXP từ danh sách item nhặt được khi hunt.

    Mỗi item đóng góp EXP theo độ hiếm của nó — tra cứu qua RARITY_EXP_WEIGHT.
    Item thiếu trường 'rarity' hoặc có rarity không xác định → đóng góp 1 EXP.

    Args:
        found_items: list[dict] trả về từ roll_hunt_items() — mỗi phần tử
                     là một item dict có ít nhất trường 'rarity'.

    Returns:
        Tổng EXP (int >= 0). Trả về 0 nếu found_items rỗng hoặc không hợp lệ.

    Ví dụ:
        found_items = [{"rarity": "rare"}, {"rarity": "common"}, {"rarity": "epic"}]
        calc_hunt_exp(found_items)  →  4 + 1 + 8  =  13
    """
    if not found_items or not isinstance(found_items, list):
        return 0
    total = 0
    for item in found_items:
        if not isinstance(item, dict):
            continue
        rarity = item.get("rarity", "common")
        total += RARITY_EXP_WEIGHT.get(rarity, 1)
    return total


def make_weapon_instance(base_id: str, uid: str, level: int = 1) -> dict:
    """Tạo weapon instance mới với uid, base_id và level cho trước."""
    lvl = min(max(1, level), WEAPON_LEVEL_CAP)
    return {
        "uid":         uid,
        "base_id":     base_id,
        "level":       lvl,
        "exp":         0,
        "exp_to_next": exp_to_next(lvl),
    }


def grant_weapon_exp(user: dict, uid: str, exp_amount: int) -> dict:
    """
    Cộng exp_amount vào weapon instance được chỉ định bởi uid.
    Tự động level-up nếu đủ EXP — có thể level-up nhiều lần liên tiếp
    nếu exp_amount đủ lớn.

    Args:
        user:       dict user (từ get_user())
        uid:        UID của weapon instance (vd: "467-ABC12")
        exp_amount: lượng EXP cần cộng vào (nên >= 0; giá trị âm bị bỏ qua)

    Returns:
        dict với các field:
            leveled_up   (bool) — True nếu có ít nhất 1 lần level-up
            old_level    (int)  — level trước khi cộng EXP
            new_level    (int)  — level sau khi xử lý xong
            levels_gained (int) — số lần level-up đã xảy ra
            uid          (str)  — uid của weapon (passthrough, tiện cho caller)

        Nếu uid không tìm thấy trong weapon_instances → trả về result mặc định
        với leveled_up=False; KHÔNG raise, KHÔNG mutate user.

    Ghi chú:
        - Defensive defaults: instance tạo trước khi hệ thống leveling ra đời
          có thể thiếu các field level/exp/exp_to_next — hàm này tự điền.
        - Hard cap: khi đạt WEAPON_LEVEL_CAP, exp và exp_to_next đặt về 0.
        - Idempotent trên instance đã đạt cap: gọi lại không thay đổi gì.
    """
    result: dict = {
        "found":         False,   # tường minh hơn old_level==0
        "leveled_up":    False,
        "old_level":     0,       # giữ 0 để backward compat (caller dùng == 0 detect not-found)
        "new_level":     0,       # giữ 0 để backward compat
        "levels_gained": 0,
        "uid":           uid,
    }

    if not isinstance(uid, str) or not uid:
        return result
    if not isinstance(exp_amount, (int, float)) or exp_amount <= 0:
        return result

    exp_amount = int(exp_amount)

    # Tìm weapon instance theo uid
    wi: dict | None = None
    for inst in user.get("weapon_instances", []):
        if isinstance(inst, dict) and inst.get("uid") == uid:
            wi = inst
            break

    if wi is None:
        logger.debug("grant_weapon_exp: uid '%s' không có trong weapon_instances", uid)
        return result  # found=False, old_level=0, new_level=0

    # Từ đây trở đi: uid đã tìm thấy
    result["found"] = True

    # Điền defensive defaults — instance cũ có thể thiếu các field này
    wi.setdefault("level",       1)
    wi.setdefault("exp",         0)
    wi.setdefault("exp_to_next", exp_to_next(wi["level"]))

    old_level = wi["level"]
    result["old_level"] = old_level

    # Đã đạt cap — không thay đổi gì, đảm bảo trạng thái nhất quán
    if old_level >= WEAPON_LEVEL_CAP:
        result["new_level"] = WEAPON_LEVEL_CAP
        wi["level"]         = WEAPON_LEVEL_CAP
        wi["exp"]           = 0
        wi["exp_to_next"]   = 0
        return result

    wi["exp"] += exp_amount

    # Level-up loop — có thể chạy nhiều vòng nếu exp_amount lớn
    while wi["level"] < WEAPON_LEVEL_CAP and wi["exp"] >= wi["exp_to_next"]:
        wi["exp"]         -= wi["exp_to_next"]
        wi["level"]       += 1
        wi["exp_to_next"]  = exp_to_next(wi["level"])
        result["levels_gained"] += 1

    # Hard cap tại WEAPON_LEVEL_CAP
    if wi["level"] >= WEAPON_LEVEL_CAP:
        wi["level"]       = WEAPON_LEVEL_CAP
        wi["exp"]         = 0
        wi["exp_to_next"] = 0

    if result["levels_gained"] > 0:
        result["leveled_up"] = True

    result["new_level"] = wi["level"]

    if result["leveled_up"]:
        logger.info(
            "grant_weapon_exp: uid='%s' level %d → %d (+%d levels, +%d exp)",
            uid, old_level, wi["level"], result["levels_gained"], exp_amount,
        )

    return result


def migrate_upgraded_weapons(user: dict) -> bool:
    """
    Chuyển đổi user["upgraded_weapons"] (format cũ) sang user["weapon_instances"].
    Chỉ chạy nếu upgraded_weapons không rỗng.
    Trả về True nếu đã migrate, False nếu không cần.

    FIX — Partial-failure safety:
        Old code: user["upgraded_weapons"] = []  (unconditional wipe)
        New code: only remove entries whose uid was successfully written to
                  weapon_instances. Entries with missing uid/base_id stay in
                  the list so they are not silently discarded.
        WHY: If an exception fires mid-loop, or an entry is malformed, the old
        code would still clear the list on the next clean run — destroying data
        that was never actually migrated.

    FIX — Level preservation:
        We first check if the entry already has a "level" field (set by a
        previous partial migration). Only fall back to the effect_levels
        heuristic when no explicit level is present.
        WHY: The heuristic (max of effect_levels values) is a lossy
        approximation. Trusting an already-computed level is safer.
    """
    old_list = user.get("upgraded_weapons", [])
    if not old_list:
        return False

    existing_uids: set[str] = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    # Track which entries we successfully migrate so we only remove those.
    # WHY: Prevents unconditional wipe from discarding un-migratable entries.
    successfully_migrated: set[str] = set()

    for entry in old_list:
        if not isinstance(entry, dict):
            continue
        uid     = entry.get("uid")
        base_id = entry.get("base_id")
        if not uid or not base_id:
            # Cannot migrate without both fields; leave in list (do not discard).
            continue

        if uid in existing_uids:
            # Already migrated in a previous pass — idempotent skip.
            successfully_migrated.add(uid)
            continue

        # FIX: Prefer an explicit "level" field over the effect_levels heuristic.
        # WHY: effect_levels stores per-effect upgrade counts (e.g. {"atk": 3})
        # which are not the same unit as weapon level. An already-computed level
        # field (from a prior partial migration) is more trustworthy.
        level = entry.get("level")
        if level is None:
            eff_levels = entry.get("effect_levels", {})
            raw_values = [v for v in eff_levels.values() if isinstance(v, (int, float))]
            level = int(max(raw_values)) if raw_values else 1

        level = min(max(1, level), WEAPON_LEVEL_CAP)

        user.setdefault("weapon_instances", []).append(
            make_weapon_instance(base_id, uid, level)
        )
        existing_uids.add(uid)
        successfully_migrated.add(uid)

    if not successfully_migrated:
        return False

    # FIX: Only remove entries that were successfully migrated.
    # WHY: Unconditionally clearing the list (old behaviour) destroys entries
    # that were skipped due to malformed data — they can never be recovered.
    user["upgraded_weapons"] = [
        e for e in old_list
        if not (isinstance(e, dict) and e.get("uid") in successfully_migrated)
    ]
    return True


def migrate_all_weapons_to_uid(user: dict) -> bool:
    """
    Đảm bảo toàn bộ weapons[] và equipped[] đều là UID (có dấu "-").
    Với mỗi bare base_id tìm thấy:
      - Ưu tiên tái sử dụng weapon_instance có cùng base_id mà chưa được link
        (orphan, thường do migrate_upgraded_weapons tạo ra).
      - Nếu không có orphan → mới tạo UID mới và instance level=1.
    Trả về True nếu có thay đổi, False nếu không cần.

    FIX — Orphan instance reuse:
        Old code: always generated a new uid + fresh level=1 instance.
        New code: first checks for an existing weapon_instance whose base_id
                  matches and whose uid is not yet referenced in weapons[]
                  or equipped[] (an "orphan"). If found, reuse that uid.
        WHY: migrate_upgraded_weapons() runs before this function and may
        have already created a leveled instance for the same weapon. Without
        reuse, the player's weapon slot gets a new uid at level 1, and the
        leveled instance becomes an unreachable orphan forever.
    """
    changed = False

    # Build set of all UIDs currently referenced in slots (linked).
    # WHY: "linked" means the uid already appears in weapons[] or equipped[].
    # An instance whose uid is NOT linked is an orphan — eligible for reuse.
    linked_uids: set[str] = set()
    for w in user.get("weapons", []):
        if isinstance(w, str) and "-" in w:
            linked_uids.add(w)
    for w in user.get("equipped", []):
        if isinstance(w, str) and w and "-" in w:
            linked_uids.add(w)

    # All known uid strings (linked + unlinked) — used to guarantee uid uniqueness.
    existing_uids: set[str] = set(linked_uids)
    for wi in user.get("weapon_instances", []):
        if isinstance(wi, dict) and "uid" in wi:
            existing_uids.add(wi["uid"])

    existing_wi_uids: set[str] = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    # FIX: Build a map of base_id → list of orphan uids.
    # WHY: When a bare base_id is encountered in weapons[], we pop one orphan
    # uid from this map to reuse instead of creating a fresh level=1 instance.
    # This preserves progression that migrate_upgraded_weapons() stored.
    orphan_by_base_id: dict[str, list[str]] = {}
    for wi in user.get("weapon_instances", []):
        if not isinstance(wi, dict):
            continue
        uid     = wi.get("uid")
        base_id = wi.get("base_id")
        if uid and base_id and uid not in linked_uids:
            orphan_by_base_id.setdefault(base_id, []).append(uid)

    def _make_uid(base_id: str) -> str:
        """Generate a collision-free uid for a given base_id."""
        suffix = _uuid.uuid4().hex[:5].upper()
        uid = f"{base_id}-{suffix}"
        while uid in existing_uids:
            suffix = _uuid.uuid4().hex[:5].upper()
            uid = f"{base_id}-{suffix}"
        existing_uids.add(uid)
        return uid

    def _ensure_instance(uid: str, base_id: str) -> None:
        """
        Create a minimal level=1 instance only if no instance exists for uid.
        WHY: This path is only reached when no orphan was available, meaning
        the weapon truly had no prior tracking — level=1 is correct.
        """
        if uid not in existing_wi_uids:
            user.setdefault("weapon_instances", []).append(
                make_weapon_instance(base_id, uid, level=1)
            )
            existing_wi_uids.add(uid)

    # Convert weapons[] (bag)
    for i, wid in enumerate(user.get("weapons", [])):
        if not isinstance(wid, str) or "-" in wid:
            continue  # already a uid, skip

        orphans = orphan_by_base_id.get(wid, [])
        if orphans:
            # FIX: Reuse an existing orphan instance instead of creating level=1.
            # WHY: Preserves level/exp that migrate_upgraded_weapons() recovered.
            reuse_uid = orphans.pop(0)
            user["weapons"][i] = reuse_uid
            linked_uids.add(reuse_uid)
            # Instance already in weapon_instances — no creation needed.
        else:
            new_uid = _make_uid(wid)
            user["weapons"][i] = new_uid
            _ensure_instance(new_uid, wid)
            linked_uids.add(new_uid)
        changed = True

    # Convert equipped[]
    for i, wid in enumerate(user.get("equipped", [])):
        if not isinstance(wid, str) or not wid or "-" in wid:
            continue  # None, empty, or already a uid — skip

        orphans = orphan_by_base_id.get(wid, [])
        if orphans:
            reuse_uid = orphans.pop(0)
            user["equipped"][i] = reuse_uid
            linked_uids.add(reuse_uid)
        else:
            new_uid = _make_uid(wid)
            user["equipped"][i] = new_uid
            _ensure_instance(new_uid, wid)
            linked_uids.add(new_uid)
        changed = True

    return changed


def migrate_weapon_instance_fields(user: dict) -> bool:
    """
    Đảm bảo tất cả weapon_instances có đủ các field: level, exp, exp_to_next.
    Chạy sau migrate_upgraded_weapons() và migrate_all_weapons_to_uid().
    Trả về True nếu có thay đổi, False nếu không cần.

    HOÀN TOÀN MỚI — hàm này không tồn tại trước đây.

    Pass 1 — Normalize existing instances:
        WHY: Instances saved before the leveling system was added lack level/exp
        fields entirely. Any direct field access (wi["level"]) causes KeyError.
        grant_weapon_exp() uses setdefault defensively, but not all callers do.
        This pass fills missing fields ADDITIVELY — it never overwrites existing
        level or exp values, so running it on an already-migrated user is safe.

    Pass 2 — Create stubs for orphan UIDs in weapons[]/equipped[]:
        WHY: A uid in weapons[] or equipped[] with no matching weapon_instance
        causes grant_weapon_exp() to silently return zeros (uid not found). The
        weapon appears owned but can never gain EXP or level up. This can happen
        if a save was interrupted after the uid was written to weapons[] but before
        the instance was written. Level=1 is the safe default for unknown history.
    """
    changed = False

    # Pass 1: Normalize existing instances (additive, never destructive).
    for wi in user.get("weapon_instances", []):
        if not isinstance(wi, dict):
            continue

        # Clamp level to valid range without resetting it.
        raw_level = wi.get("level", None)
        if raw_level is None:
            wi["level"] = 1
            changed = True
        else:
            clamped = min(max(1, raw_level), WEAPON_LEVEL_CAP)
            if clamped != raw_level:
                wi["level"] = clamped
                changed = True

        if "exp" not in wi:
            wi["exp"] = 0
            changed = True

        if "exp_to_next" not in wi:
            wi["exp_to_next"] = exp_to_next(wi["level"])
            changed = True

    # Pass 2: Create level=1 stubs for UIDs referenced in slots but missing instances.
    existing_wi_uids: set[str] = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    for slot_list in (user.get("weapons", []), user.get("equipped", [])):
        for wid in slot_list:
            if not isinstance(wid, str) or not wid or "-" not in wid:
                continue  # None, empty, or bare base_id — skip
            if wid in existing_wi_uids:
                continue  # instance already present — skip

            # Parse base_id from "base_id-SUFFIX" format.
            # WHY: We need base_id to construct the instance. rsplit keeps the
            # suffix intact even if base_id itself contains a hyphen.
            base_id = wid.rsplit("-", 1)[0]
            user.setdefault("weapon_instances", []).append(
                make_weapon_instance(base_id, wid, level=1)
            )
            existing_wi_uids.add(wid)
            changed = True
            log.warning(
                "migrate_weapon_instance_fields: created stub for orphan uid=%s "
                "(was in weapons/equipped with no matching instance)", wid
            )

    return changed


# 
#  GET USER — Salvage-First + v5.5 Legacy Migration
# 

def _make_default_user() -> dict:
    """Return a fresh default user dict (avoid mutating a shared constant)."""
    return {
        "inv":              {},
        "weapons":          [],
        "weapon_instances": [],
        "equipped":         [None, None, None],
        "cash":            0,
        "passives":         {},
        "hunt_cd":          0,
        "crate_cd":         0,
        "hunt_log":         [],
    }


def get_user(uid, data: dict) -> dict:
    """
    Retrieve or create a user safely — Salvage-First philosophy.

    Principles:  = - NEVER delete player data if it can be recovered.
    - Each field has its own salvage strategy (convert, filter, patch).
    - Reset a field only when truly irrecoverable (logged explicitly).
    - Reset the whole user only when it is not a dict at all (extremely rare).

    Note (v1.8): user doc từ MongoDB có field '_id' ( uid_str).
    Field này được giữ nguyên — save_core_data tự strip trước khi $set.

    Note: bare base_id weapons = ("467") are valid stack weapons and are left
    as-is. = Call ensure_weapon_uid() from the upgrade command when a UID is
    needed — do NOT force-migrate here.
    """
    uid = str(uid)

    if uid not in data:
        data[uid] = _make_default_user()
        logger.info(f"✅ Created new user: {uid}")
        return data[uid]

    user = data[uid]

    if not isinstance(user, dict):
        logger.critical(
            f"🚨 CRITICAL: User {uid} is {type(user).__name__} instead of dict. "
            f"First 100 chars: {str(user)[:100]}. "
            f"Forced reset — UNRECOVERABLE!"
        )
        data[uid] = _make_default_user()
        return data[uid]

    # Back-fill new fields for existing users
    if "cash" not in user:
        user["cash"] = 0

    #  Salvage each field in priority order 
    _salvage_numeric(user, "cash",    0, uid)
    _salvage_numeric(user, "hunt_cd",  0, uid)
    _salvage_numeric(user, "crate_cd", 0, uid)

    _salvage_inv(user, uid)

    # user["weapons"] must be list[str] before migration runs
    _salvage_list_of_str(user, "weapons", uid)

    # upgraded_weapons is list[dict] — must NOT use _salvage_list_of_str
    _salvage_upgraded_weapons(user, uid)

    _salvage_equipped(user, uid)
    _salvage_passives(user, uid)
    _salvage_hunt_log(user, uid)

    if not isinstance(user.get("weapon_instances"), list):
        user["weapon_instances"] = []
    else:
        user["weapon_instances"] = [
            wi for wi in user["weapon_instances"]
            if isinstance(wi, dict)
            and "uid" in wi
            and "base_id" in wi
        ]

    # Migration chạy tại đây theo pattern chuẩn: load → get_user → [modify] → save.
    # Migration functions defined above in this file (moved from rpg_database.py).
    # Caller chịu trách nhiệm save sau khi modify user.
    migrate_upgraded_weapons(user)
    migrate_all_weapons_to_uid(user)
    migrate_weapon_instance_fields(user)   # defined in MIGRATION FUNCTIONS section above

    return user


# 
#  INVENTORY HELPERS (unchanged)
# 

def add_item(user: dict, item_id: str, amount: int = 1) -> None:
    """Add item to inventory."""
    user["inv"][item_id] = user["inv"].get(item_id, 0) + amount


def remove_item(user: dict, item_id: str, amount: int = 1) -> bool:
    """Remove item from inventory. Returns True on success."""
    if user["inv"].get(item_id, 0) < amount:
        return False
    user["inv"][item_id] -= amount
    if user["inv"][item_id] <= 0:
        del user["inv"][item_id]
    return True


# 
#  v5.5: add_weapon — FIXED (was appending raw base ID; now generates proper UID)
# 

def add_weapon(user: dict, base_id: str, make_unique: bool = True) -> str:
    """
    Add a weapon to the bag.

    make_unique=False = → append base_id directly (stack weapon, e.g. "467").
    make_unique=True = → generate a UID instance (e.g. "467-ABC12") and append it.

    In BOTH cases NO upgraded_weapons entry is created.
    Upgrade data is created lazily — only when the player actually upgrades
    the weapon — by calling ensure_upgrade_entry() from the upgrade command.

    Returns the stored weapon identifier (base_id or new UID).
    """
    if not isinstance(user.get("weapons"), list):
        user["weapons"] = []

    if not make_unique:
        user["weapons"].append(base_id)
        if base_id not in user.setdefault("seen_weapons", []):
            user["seen_weapons"].append(base_id)
        return base_id

    weapon_data = get_weapon_by_id(base_id)
    if not weapon_data:
        raise ValueError(f"[add_weapon] Invalid base_id: {base_id}")

    # Build collision set from bag + equipped to prevent duplicate UIDs.
    # FIX: was missing — a bare uuid4().hex[:5] can collide with existing UIDs.
    existing_uids: set[str] = {
        w for w in user.get("weapons", []) if WeaponID.is_unique(w)
    } | {
        e for e in user.get("equipped", [])
        if isinstance(e, str) and WeaponID.is_unique(e)
    }

    # 5-char suffix keeps UIDs short and readable for trading
    suffix = uuid.uuid4().hex[:5].upper()
    new_uid = f"{base_id}-{suffix}"
    while new_uid in existing_uids:          # collision guard loop
        suffix = uuid.uuid4().hex[:5].upper()
        new_uid = f"{base_id}-{suffix}"

    user["weapons"].append(new_uid)
    if base_id not in user.setdefault("seen_weapons", []):
        user["seen_weapons"].append(base_id)

    existing_wi_uids = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    if new_uid not in existing_wi_uids:
        try:
            w_data = get_weapon_by_id(base_id) or {}
            rarity = w_data.get("rarity", "common")
            quality = roll_quality(rarity)
            dur_max = DURABILITY_BY_RARITY.get(rarity, 30)
            passive = roll_passive(rarity, quality)
        except Exception:
            quality = "medium"
            dur_max = 30
            passive = {}

        user.setdefault("weapon_instances", []).append({
            "uid":            new_uid,
            "base_id":        base_id,
            "level":          1,
            "exp":            0,
            "exp_to_next":    80,  # exp_to_next(1) = level*40+40
            "quality":        quality,
            "durability":     dur_max,
            "durability_max": dur_max,
            "passive":        passive,
            "broken":         False,
        })

    return new_uid


def _find_weapon_in_bag(weapons: list, weapon_id: str) -> int | None:
    """
    ID-aware bag search. = Returns the index of the first matching weapon, or None.

    Search priority:
      1. Exact string match = — "467-ABC12" = "467-ABC12"
      2. Base-ID match       — "467" finds the first "467-XXXXX" or bare "467"

    Pass 2 returns a result ONLY when exactly ONE weapon matches the base_id.
    If multiple entries share the same base_id the caller must pass the full UID.
    Never raises — returns None safely when the weapon is not found.
    """
    # Pass 1: exact match (preferred — unambiguous, O(n))
    for i, wid in enumerate(weapons):
        if wid == weapon_id:
            return i

    # Pass 2: base_id fallback — resolves "467" → "467-XXXXX" (or bare "467")
    # FIX: was `base` (NameError); must be get_base_id(weapon_id)
    target_base = get_base_id(weapon_id)
    matches = [i for i, wid in enumerate(weapons) if get_base_id(wid) == target_base]

    if len(matches) == 1:
        return matches[0]

    # 0 matches → not found; 2+ matches → ambiguous (caller must use full UID)
    return None


def remove_weapon_from_bag(user: dict, weapon_id: str) -> bool:
    """
    Remove one weapon from the bag. = Returns True if found and removed.

    Uses ID-aware lookup so both base_id and UID are accepted.
    For stack weapons (bare base_id) it removes the first matching entry.
    For UID weapons it matches exactly.
    """
    weapons = user.get("weapons", [])
    idx = _find_weapon_in_bag(weapons, weapon_id)
    if idx is None:
        return False
    weapons.pop(idx)
    return True


# 
#  UPGRADE FLOW HELPERS
# 

def ensure_upgrade_entry(user: dict, uid: str, base_id: str | None = None) -> None:
    """Deprecated: upgrade system đã được thay bằng weapon level system."""
    pass


def ensure_weapon_uid(user: dict, weapon_id: str) -> str | None:
    """
    Guarantee that the weapon has a UID in the bag.

    Call this at the START of any upgrade command to obtain the UID before
    reading or writing upgrade data. = After this call, use ensure_upgrade_entry()
    to create the upgraded_weapons record when the actual upgrade happens.

    This function ONLY handles UID identity — it does NOT create upgrade data.
    A UID can and should exist without an upgraded_weapons entry until the
    first upgrade is performed.

    Behaviour:  = • weapon_id is already a UID ("467-ABC12") and in bag:
            Returns the UID unchanged.

      • weapon_id is a bare base_id ("467") and bag entry is already a UID:
            Returns the stored UID (no changes made).

      • weapon_id is a bare base_id ("467") and bag entry is also bare:
            Promotes the entry to a fresh UID in-place.
            Returns the new UID.

      • weapon_id not found in bag:
            Returns None.

    The returned UID is always present in user["weapons"].
    No upgraded_weapons entry is created here.
    """
    if not isinstance(weapon_id, str) or not weapon_id:
        return None

    weapons = user.get("weapons", [])
    if not isinstance(weapons, list):
        return None

    idx = _find_weapon_in_bag(weapons, weapon_id)
    if idx is None:
        logger.debug(f"ensure_weapon_uid: '{weapon_id}' not found in bag")
        return None

    stored_id = weapons[idx]
    base_id = get_base_id(stored_id)

    # Already a UID — return as-is (no upgrade entry creation)
    if WeaponID.is_unique(stored_id):
        return stored_id

    # Bare base_id → promote to UID in-place
    existing_uids: set[str] = {
        w for w in weapons if WeaponID.is_unique(w)
    } | {
        e for e in user.get("equipped", [])
        if isinstance(e, str) and WeaponID.is_unique(e)
    }

    suffix = uuid.uuid4().hex[:5].upper()
    new_uid = f"{base_id}-{suffix}"
    while new_uid in existing_uids:
        suffix = uuid.uuid4().hex[:5].upper()
        new_uid = f"{base_id}-{suffix}"

    weapons[idx] = new_uid   # promote in-place (bag entry updated)

    # Tạo weapon_instance cho uid mới nếu chưa tồn tại
    existing_wi_uids = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    if new_uid not in existing_wi_uids:
        try:
            w_data = get_weapon_by_id(base_id) or {}
            rarity = w_data.get("rarity", "common")
            quality = roll_quality(rarity)
            dur_max = DURABILITY_BY_RARITY.get(rarity, 30)
            passive = roll_passive(rarity, quality)
        except Exception:
            quality = "medium"
            dur_max = 30
            passive = {}

        user.setdefault("weapon_instances", []).append({
            "uid":            new_uid,
            "base_id":        base_id,
            "level":          1,
            "exp":            0,
            "exp_to_next":    80,  # exp_to_next(1) = level*40+40
            "quality":        quality,
            "durability":     dur_max,
            "durability_max": dur_max,
            "passive":        passive,
            "broken":         False,
        })

    logger.info(
        f"ensure_weapon_uid: promoted '{stored_id}' → '{new_uid}'"
    )
    return new_uid


# 
#  EQUIP / UNEQUIP (unchanged)
# 

def equip_weapon(user: dict, weapon_id: str, slot: int | None = None) -> tuple[bool, str]:
    """
    Equip a weapon. = Returns (success, message).

    Accepts either base_id ("467") or UID ("467-ABC12") — uses ID-aware bag lookup.
    Always equips the actual stored identifier so UIDs are preserved in equipped[].
    """
    weapons = user.get("weapons", [])
    idx = _find_weapon_in_bag(weapons, weapon_id)
    if idx is None:
        return False, "Bạn không có vũ khí này trong kho."

    actual_id = weapons[idx]   # real stored ID — may be UID even if caller passed base_id

    equipped = user["equipped"]
    while len(equipped) < 3:
        equipped.append(None)

    if actual_id in equipped:
        return False, "Vũ khí này đã được trang bị ở 1  rồi."

    if slot is not None:
        if slot not in (1, 2, 3):
            return False, "Ô trang bị chỉ từ 1 đến 3."
        idx_slot = slot - 1
        old_weapon_id = equipped[idx_slot]   # capture before overwrite (may be None)

        # FIX: remove new weapon from bag FIRST, then assign to slot, then return old.
        # Previous order (append old → assign → remove new) allowed a crash between
        # the append and the remove to leave both old AND new duplicated in the bag.
        remove_weapon_from_bag(user, actual_id)   # step A: eliminate new from bag
        equipped[idx_slot] = actual_id             # step B: place in slot
        if old_weapon_id is not None:
            user["weapons"].append(old_weapon_id)  # step C: return old to bag (safe)
    else:
        filled = False
        for i in range(3):
            if equipped[i] is None:
                remove_weapon_from_bag(user, actual_id)   # remove BEFORE assigning
                equipped[i] = actual_id
                filled = True
                break
        if not filled:
            return False, (
                "Tất cả 3 ô trang bị đã đầy.\n"
                "Chỉ định ô cụ thể: `dtn weapon equip <id> <slot>`\n"
                "Hoặc bỏ trang bị trước: `dtn weapon unequip <slot>`"
            )

    user["equipped"] = equipped
    # FIX: remove_weapon_from_bag is now called inside each branch above.
    # The previous dangling call here caused a double-remove when the slot path ran.
    return True, "ok"


def unequip_weapon(user: dict, slot: int) -> tuple[bool, str]:
    """Unequip weapon from slot. Returns (success, weapon_id or error message)."""
    equipped = user["equipped"]
    while len(equipped) < 3:
        equipped.append(None)

    if slot not in (1, 2, 3):
        return False, "Ô trang bị chỉ từ 1 đến 3."

    idx = slot - 1
    if equipped[idx] is None:
        return False, f"Ô {slot} đang trống."

    weapon_id = equipped[idx]
    equipped[idx] = None
    user["equipped"] = equipped

    # Return the weapon UID to the bag — the UID keeps its upgraded_weapons entry intact
    user["weapons"].append(weapon_id)
    return True, weapon_id






# 
#  EGG HANDLER (unchanged)
# 

def handle_egg(user: dict) -> list[dict]:
    """
    Hatch 1 egg from inventory.
    Consumes 1 egg (item "004"), returns a list of hatched item dicts.
    Returns [] if the user has no eggs.
    """
    if not remove_item(user, "004", 1):
        return []
    egg_item = get_item_by_id("004")
    count    = random.randint(1, 4)
    return [egg_item] * count


