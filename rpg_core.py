
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

from database_helper import (
    load_core_data,
    save_core_data,
    _with_retry,
    MAX_RETRIES as _MAX_RETRIES,
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
    migrate_weapon_instance_fields,
    decrease_durability,
    fmt_instance_info,
    QUALITY_TIERS,
    DURABILITY_BY_RARITY,
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
    # ── Weapon Identity Layer ───────────────────────────────────────────────────
    "get_base_id",                          # canonical ID resolver
    "WeaponID", "WeaponEntity", "get_weapon_entity",
    "ensure_weapon_uid",                    # promote base weapon → UID (lazy, no upgrade data)
    "ensure_upgrade_entry",                 # create upgraded_weapons entry ONLY when upgrading
    "get_user_lock",
    # ── integrity ───────────────────────────────────────────────────────────────
    "_validate_data_integrity",
    "ITEMS", "WEAPONS", "CRATES", "RARITY_COLOR", "RARITY_LABEL",
    "DARK_CRATE_WEAPON",
]


# ══════════════════════════════════════════════════════════════════════════════
#  CANONICAL ID RESOLVER — THE ONLY PERMITTED CALL SITE FOR .split("-")
# ══════════════════════════════════════════════════════════════════════════════

def get_base_id(wid: str) -> str:
    """
    Extract the base weapon ID from any weapon identifier.

    This is THE ONLY function in the entire project allowed to call .split('-').
    All other modules MUST call get_base_id() instead of doing str.split('-') directly.

    Examples:
        get_base_id("467")            → "467"   (bare base ID — passthrough)
        get_base_id("467-A3B2C1")     → "467"   (Unique ID)
        get_base_id("467-A3B2C1_fix") → "467"   (repaired UID — still correct)
    """
    return str(wid).split("-")[0]


# ─── v5.5: Deterministic stat seed secret ───────────────────────────────────────
# Override via environment: export RPG_WEAPON_SECRET="your-secret"
# MUST be changed from default in production!
GLOBAL_SECRET: bytes = os.environb.get(
    b"RPG_WEAPON_SECRET",
    b"v5.5-arch-default-CHANGE-IN-PROD",
)


# ══════════════════════════════════════════════════════════════════════════════
#  CONCURRENCY — per-user lock + global save lock
# ══════════════════════════════════════════════════════════════════════════════

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

    Usage:
        async with get_user_lock(uid):
            data = load_data(uid)
            user = get_user(uid, data)
            # ... modify user ...
            await save_data(data, uid)
    """
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]


# ══════════════════════════════════════════════════════════════════════════════
#  WEAPON IDENTITY LAYER — WeaponID · WeaponEntity · get_weapon_entity
# ══════════════════════════════════════════════════════════════════════════════

class WeaponID:
    """
    SINGLE SOURCE OF TRUTH for UID string processing.

    THIS IS THE ONLY PLACE in the entire project allowed to call .split("-").
    All other modules MUST use WeaponID.parse() / WeaponID.is_unique().

    UID format:
        Base ID    : "467"              (stack weapon — valid, not legacy)
        Unique ID  : "467-A3B2C1"      (instance of the same weapon type)
        Repaired   : "467-A3B2C1_fix_xxxxxx"  (duplicate collision repair)

    Both forms are first-class citizens.  UID is an EXTENSION of base_id,
    not a separate weapon type.  Use get_base_id() to resolve either form.
    """

    @staticmethod
    def parse(uid: str) -> tuple[str, bool]:
        """
        Parse UID → (base_id, is_unique).

        Delegates to get_base_id() — no direct .split('-') here.

        Examples:
            "467-A3B2C1"          → ("467", True)
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


# ─── v5.5: Deterministic per-stat seed ─────────────────────────────────────────

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

    Attributes:
        uid          — Stored weapon identifier: "467-A3B2C1" (UID) or "467" (base_id)
        base_data    — Weapon dict from WEAPONS / RARE_CRATE_WEAPONS / SPECIAL_WEAPONS
        upgrade_data — Entry in user["upgraded_weapons"], or None if not yet upgraded.
                       None is the normal state for unupgraded weapons — it is NOT an error.
    """

    def __init__(self, uid: str, base_data: dict,
                 upgrade_data: dict | None = None,
                 instance_data: dict | None = None):
        self.uid           = uid
        self.base_data     = base_data
        self.upgrade_data  = upgrade_data   # backward compat, sẽ bỏ dần
        self.instance_data = instance_data  # hệ thống mới

    # ── Display helpers ────────────────────────────────────────────────────────

    def fmt_name(self) -> str:
        """Name + emoji + [<:Upgradeeffect:1498218616376524912> if upgraded]. Used in all lists."""
        name  = self.base_data.get("name", self.uid)
        emoji = self.base_data.get("emoji", "")
        has_data = self.upgrade_data is not None or self.instance_data is not None
        tag      = " <:Upgradeeffect:1498218616376524912>" if has_data else ""
        return f"{emoji} **{name}**{tag}"

    def fmt_stats(self) -> str:
        """
        Unified effects string — shows scaled values if upgrade exists.
        Returns a single str (do NOT iterate over this; use it as a field value).
        Used by: inv, status panel, upgrade panel, givew embed.
        """
        effects = self.base_data.get("effects", {})
        wi      = self.instance_data

        scaled = build_weapon_effects(effects, wi)

        lines = []
        for k, v in scaled.items():
            if k == "extra_slot":
                lines.append(f"• `{k}`: +{int(v)} ô")
            elif isinstance(v, float):
                lines.append(f"• `{k}`: **{v:+.1%}**")
            elif isinstance(v, (int, float)):
                lines.append(f"• `{k}`: **{v:+}**")

        if not lines:
            lines.append("Không có hiệu ứng.")

        # Quality, durability, passive — dùng fmt_instance_info từ rpg_instance
        if wi:
            lines.append(fmt_instance_info(wi))

        return "\n".join(lines)

    def get_price(self) -> int:
        """Random sell price within [min, max] from base weapon data."""
        return random.randint(
            self.base_data.get("min", 100),
            self.base_data.get("max", 1000),
        )

    # ── v5.5: Deterministic stat rolls ────────────────────────────────────────

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
            rng  = random.Random(seed)

            # ±10% uniform variance
            factor = rng.uniform(0.90, 1.10)

            if isinstance(base_val, float):
                rolled[stat_key] = round(base_val * factor, 6)
            elif isinstance(base_val, int):
                rolled[stat_key] = max(1, round(base_val * factor))
            else:
                rolled[stat_key] = base_val   # passthrough for non-numeric

        return rolled

    def build_embed(self):
        """
        Build a full discord.Embed for this weapon — SINGLE SOURCE for UI.
        Used by: dtn status, dtn inv, dtn weapon <id>.
        """
        import discord as _discord

        rarity = self.base_data.get("rarity", "common")
        color  = RARITY_COLOR.get(rarity, 0x5865F2)
        label  = RARITY_LABEL.get(rarity, rarity)
        name   = self.base_data.get("name", self.uid)
        emoji  = self.base_data.get("emoji", "")

        if self.upgrade_data:
            eff_lvs = self.upgrade_data.get("effect_levels", {})
            max_lv  = max(eff_lvs.values()) if eff_lvs else 1
            title   = (
                f"<:Effect:1495466103047061679> {emoji} **{name}** <:Upgradeeffect:1498218616376524912>"
                f" _(max lv{max_lv}/30)_"
            )
            desc = f"Unique ID: `{self.uid}` | {label}"
        else:
            title = f"{emoji} **{name}**"
            desc  = f"{label}  |  ID: `{self.uid}`"

        wi_level = 1
        if self.instance_data:
            wi_level = max(1, min(50, self.instance_data.get("level", 1)))

        if wi_level > 1:
            title = f"{title} <:Effect:1495466103047061679> _(Lv{wi_level}/50)_"

        _COIN = "<:Coin:1495831576397742241>"
        embed = _discord.Embed(title=title, description=desc, color=color)
        embed.add_field(
            name="<:Effect:1495466103047061679> | Hiệu ứng",
            value=self.fmt_stats(),
            inline=False,
        )
        embed.add_field(
            name="📖 Mô tả",
            value=self.base_data.get("description", "—"),
            inline=False,
        )
        embed.add_field(
            name=f"{_COIN} Giá bán",
            value=f"**{self.base_data.get('min', 0):,}** {_COIN}",
            inline=True,
        )
        return embed


def get_weapon_entity(user: dict, uid: str) -> "WeaponEntity | None":
    """
    SINGLE ENTRY POINT for weapon data — used by all UI commands.

    Rules:
    - Never raises — returns None instead of crashing.
    - O(1) upgraded_weapons lookup via dict comprehension.
    - Works with both base ID ("467") and unique ID ("467-A3B2C1").

    Args:
        user: dict from get_user()
        uid:  base ID ("467") or unique ID ("467-A3B2C1")

    Returns:
        WeaponEntity if base_data found, None otherwise.
    """
    if not isinstance(uid, str) or not uid:
        return None

    base_id, _ = WeaponID.parse(uid)
    if not base_id:
        return None

    base_data = get_weapon_by_id(base_id)
    if base_data is None:
        logger.debug(f"get_weapon_entity: no base_data for base_id='{base_id}'")
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


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS — emergency dump
# ══════════════════════════════════════════════════════════════════════════════

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
        ts       = int(time.time())
        emg_path = f"rpg_data.EMERGENCY_{ts}.json"
        with open(emg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.critical(
            f"🚨 EMERGENCY DUMP: {len(data)} keys → {emg_path} | "
            f"MongoDB unreachable — check MONGO_URI and restore manually!"
        )
    except Exception as e:
        logger.critical(f"🚨 EMERGENCY DUMP ALSO FAILED: {e} — DATA MAY BE LOST!")


# ══════════════════════════════════════════════════════════════════════════════
#  v5.5: SELF-HEALING INTEGRITY VALIDATOR
# ══════════════════════════════════════════════════════════════════════════════

def _validate_data_integrity(data: dict) -> tuple[bool, list[str]]:
    """
    Scan structural integrity across ALL users.  DETECTION ONLY — never mutates data.

    Phase 1 — Global duplicate UID detection:
        Builds a global uid → "user_id:source" map.
        Any UID that appears more than once across the entire dataset is flagged.

    NOTE: UIDs that have NO entry in user["upgraded_weapons"] are VALID.
    A UID is simply an instance identifier; upgrade data is created lazily,
    only when the weapon is actually upgraded.  Flagging missing upgrade
    entries would produce false positives and is NOT performed here.

    NOTE (v1.8): "upgraded_weapons" is a reserved key in the data dict holding
    the global weapons dict from MongoDB.  It is automatically skipped so it
    is never mistaken for a user record.

    Returns:
        (is_valid, audit_log) — is_valid is True only if zero issues were found.
    """
    audit:  list[str] = []
    issues: int       = 0

    # ── Phase 1: Collect all Unique IDs globally; detect duplicates ───────────
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
                    f"DUPLICATE UID '{uid}' found on user={user_id} ({source}); "
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


# ══════════════════════════════════════════════════════════════════════════════
#  SALVAGE HELPERS (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

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
        logger.warning(f"User {uid}: '{key}' = {val!r} not convertible → default={default}")
        user[key] = default


def _salvage_list_of_str(user: dict, key: str, uid: str) -> None:
    """
    Ensure user[key] is list[str].
    Non-list → reset [].
    List     → keep only valid str elements.

    ⚠️  Use ONLY for list[str] fields (e.g. "weapons").
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
    dropped  = len(raw) - len(cleaned)
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
    Ensure user["inv"] is dict{str: int >= 0} — salvage per-entry, never wipe the whole inv.

    Per-entry strategy:
      value None      → 1   (item exists, unknown qty → assume 1)
      value float     → int(round(value))
      value str-digit → int(value)
      value negative  → 0
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

    fixed     = {}
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

    Rules:
      Non-list         → [None, None, None]
      Too few (< 3)    → pad with None (do NOT drop existing weapons)
      Too many (> 3)   → slice [:3], return valid str elements at 3+ to weapons bag
      Wrong-type slot  → set None at that slot
    """
    raw     = user.get("equipped")
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
    user["weapons"]  = weapons


# ══════════════════════════════════════════════════════════════════════════════
#  v5.5: LEGACY WEAPON MIGRATION (called from get_user)
# ══════════════════════════════════════════════════════════════════════════════

def _migrate_legacy_weapons(user: dict, uid_str: str) -> None:
    """
    OPT-IN UTILITY — convert bare base IDs in user["weapons"] to UIDs.

    NOT called automatically from get_user().  Invoke explicitly only when
    you want to bulk-promote a user's entire bag (e.g. a one-off admin command).
    For on-demand promotion during upgrade, use ensure_weapon_uid() instead.

    Promotes base_id → UID in-place.  Does NOT create upgraded_weapons entries;
    upgrade data is created lazily when the player upgrades via ensure_upgrade_entry().

    Technique:
    - Clone the list before iterating (spec requirement).
    - Track already-assigned UIDs to prevent collisions within this user.
    - Deduplicate the final list (list-to-set-to-list, order-preserving).
    - Runs AFTER _salvage_list_of_str so only valid str entries exist.
    """
    raw_weapons = list(user.get("weapons", []))   # ← CLONE: do NOT iterate original in-place

    migrated:  list[str] = []
    changed:   bool      = False
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

        # ── Legacy bare base ID detected → generate UID ──────────────────────
        suffix  = uuid.uuid4().hex[:6].upper()
        new_uid = f"{wid}-{suffix}"

        # Collision guard (extremely rare, but correct)
        while new_uid in assigned:
            suffix  = uuid.uuid4().hex[:6].upper()
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
        seen:   set[str]  = set()
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


# ══════════════════════════════════════════════════════════════════════════════
#  v1.8: VALIDATE USER — chạy sau khi load từ MongoDB
# ══════════════════════════════════════════════════════════════════════════════

def _validate_user(user_data: dict) -> dict:
    """
    Kiểm tra và sửa chữa từng field của user_data sau khi load từ MongoDB.

    Strategy: Salvage-First — chỉ reset field nào thực sự sai,
    không wipe toàn bộ user trừ khi user_data không phải dict.

    Fields được validate:
        inv              → dict
        weapons          → list[str]
        upgraded_weapons → list[dict] (mỗi entry có 'uid' + 'base_id')
        equipped         → list, len == 3, mỗi slot là None | str
        hunt_cd          → numeric (int/float)
        crate_cd         → numeric (int/float)
        passives         → dict
        hunt_log         → list

    Returns:
        user_data đã được sửa chữa in-place (cùng object).
        Nếu user_data không phải dict → trả về _make_default_user().
    """
    if not isinstance(user_data, dict):
        logger.warning("[VALIDATE] user_data không phải dict → fallback default_user")
        return _make_default_user()

    uid = user_data.get("_id", "<unknown>")

    # ── inventory → dict ──────────────────────────────────────────────────────
    if not isinstance(user_data.get("inv"), dict):
        logger.warning("[VALIDATE] uid=%s: 'inv' không hợp lệ → reset {}", uid)
        user_data["inv"] = {}

    # ── weapons → list[str] ───────────────────────────────────────────────────
    raw_weapons = user_data.get("weapons")
    if not isinstance(raw_weapons, list):
        logger.warning("[VALIDATE] uid=%s: 'weapons' không phải list → reset []", uid)
        user_data["weapons"] = []
    else:
        cleaned = [w for w in raw_weapons if isinstance(w, str) and w]
        if len(cleaned) != len(raw_weapons):
            logger.warning(
                "[VALIDATE] uid=%s: 'weapons' lọc %d/%d phần tử không hợp lệ",
                uid, len(raw_weapons) - len(cleaned), len(raw_weapons),
            )
        user_data["weapons"] = cleaned

    # ── upgraded_weapons → list[dict] với 'uid' + 'base_id' ──────────────────
    raw_uw = user_data.get("upgraded_weapons")
    if not isinstance(raw_uw, list):
        logger.warning("[VALIDATE] uid=%s: 'upgraded_weapons' không phải list → reset []", uid)
        user_data["upgraded_weapons"] = []
    else:
        valid_uw = [
            e for e in raw_uw
            if isinstance(e, dict) and "uid" in e and "base_id" in e
        ]
        if len(valid_uw) != len(raw_uw):
            logger.warning(
                "[VALIDATE] uid=%s: 'upgraded_weapons' loại bỏ %d entry thiếu 'uid'/'base_id'",
                uid, len(raw_uw) - len(valid_uw),
            )
        user_data["upgraded_weapons"] = valid_uw

    # ── equipped → list, độ dài 3, mỗi slot None | str ───────────────────────
    raw_eq = user_data.get("equipped")
    if not isinstance(raw_eq, list):
        logger.warning("[VALIDATE] uid=%s: 'equipped' không phải list → reset [None,None,None]", uid)
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
                logger.warning("[VALIDATE] uid=%s: equipped > 3 slots, trả %s về bag", uid, overflow)
            eq = eq[:3]
        # Sửa từng slot
        for i, slot in enumerate(eq):
            if slot is None or (isinstance(slot, str) and slot):
                continue
            logger.warning("[VALIDATE] uid=%s: equipped[%d]=%r không hợp lệ → None", uid, i, slot)
            eq[i] = None
        user_data["equipped"] = eq

    # ── cooldowns → numeric ───────────────────────────────────────────────────
    for cd_key in ("hunt_cd", "crate_cd"):
        val = user_data.get(cd_key)
        if not isinstance(val, (int, float)):
            logger.warning("[VALIDATE] uid=%s: '%s'=%r không hợp lệ → 0", uid, cd_key, val)
            user_data[cd_key] = 0

    # ── passives → dict ───────────────────────────────────────────────────────
    if not isinstance(user_data.get("passives"), dict):
        user_data["passives"] = {}

    # ── hunt_log → list ───────────────────────────────────────────────────────
    if not isinstance(user_data.get("hunt_log"), list):
        user_data["hunt_log"] = []

    # ── weapon_instances → list[dict] với 'uid' + 'base_id' ──────────────────
    raw_wi = user_data.get("weapon_instances")
    if not isinstance(raw_wi, list):
        logger.warning(
            "[VALIDATE] uid=%s: 'weapon_instances' không phải list → reset []", uid
        )
        user_data["weapon_instances"] = []
    else:
        valid_wi = [
            e for e in raw_wi
            if isinstance(e, dict) and "uid" in e and "base_id" in e
        ]
        if len(valid_wi) != len(raw_wi):
            logger.warning(
                "[VALIDATE] uid=%s: loại bỏ %d weapon_instance không hợp lệ",
                uid, len(raw_wi) - len(valid_wi),
            )
        user_data["weapon_instances"] = valid_wi

    return user_data


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD — MongoDB (thay thế JSON multi-tier recovery)
# ══════════════════════════════════════════════════════════════════════════════

def load_data(user_id=None) -> dict:
    """
    Tải dữ liệu user + global upgraded_weapons từ MongoDB.

    Không bao giờ raise — trả về {} nếu DB lỗi hoàn toàn.

    Args:
        user_id: Discord user ID (int hoặc str).
                 Nếu None → trả về {} ngay (no-op).

    Returns:
        {
            uid_str:            user_dict,        # dùng bởi get_user(uid, data)
            "upgraded_weapons": global_uw_dict,   # global weapons index (tương thích cũ)
        }

    Lưu ý về hai loại "upgraded_weapons":
        • data["upgraded_weapons"]         — GLOBAL dict {uid: wdata, ...} từ
                                             global_metadata collection (dùng để
                                             cross-user UID lookup và global write).
        • user["upgraded_weapons"]         — PER-USER list[dict] bên trong user_dict,
                                             lưu trong economy collection (game logic
                                             dùng cái này qua get_user()).
        save_data() phân biệt và lưu đúng chỗ cho cả hai.

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
            "[LOAD] Không thể tải uid=%s sau %d lần thử: %s",
            uid_str, _MAX_RETRIES, exc, exc_info=True,
        )
        return {}

    if not isinstance(result, dict):
        logger.warning(
            "[LOAD] load_core_data trả về %s cho uid=%s → dict rỗng.",
            type(result).__name__, uid_str,
        )
        return {}

    # load_core_data trả về {"user": {...}, "upgraded_weapons": {...}}
    user_doc       = result.get("user") or {}
    global_weapons = result.get("upgraded_weapons") or {}

    if not isinstance(user_doc, dict):
        logger.warning("[LOAD] 'user' không phải dict cho uid=%s → dict rỗng.", uid_str)
        user_doc = {}

    # Validate + salvage trước khi trả về — sửa field lỗi, không crash
    _validate_user(user_doc)

    logger.debug("[LOAD] ✅ uid=%s loaded (%d keys)", uid_str, len(user_doc))

    return {
        uid_str:            user_doc,
        "upgraded_weapons": global_weapons,   # key tương thích với JSON cũ
    }


# ══════════════════════════════════════════════════════════════════════════════
#  v1.8: SAVE — MongoDB (thay thế JSON atomic save)
# ══════════════════════════════════════════════════════════════════════════════

async def save_data(data: dict, user_id=None) -> bool:
    """
    Lưu dữ liệu user + global upgraded_weapons vào MongoDB.

    Pipeline (inside _data_lock):
        1. Validate tham số đầu vào
        2. Tách user_data (per-user) và global_uw (global index) từ data dict
        3. Gọi save_core_data qua _with_retry
           • user_data["upgraded_weapons"] (list)  → economy collection ($set)
           • global_uw dict                        → global_metadata ($set dot-notation)
        4. Commit RAM snapshot sau khi DB xác nhận thành công
        5. Rollback RAM từ snapshot + emergency dump nếu tất cả retry thất bại

    Args:
        data:    dict trả về từ load_data() — đã được modify bởi caller.
        user_id: Discord user ID (int hoặc str).
                 Nếu None → trả về False ngay (no-op).

    Returns:
        True  — lưu thành công.
        False — lưu thất bại (đã log, KHÔNG crash bot).

    Concurrency:
        get_user_lock(uid) ở caller đảm bảo một user không bị modify song song.
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

    uid_str   = str(user_id)
    user_data = data.get(uid_str)

    if not isinstance(user_data, dict) or not user_data:
        logger.warning(
            "[SAVE] Không tìm thấy user_data hợp lệ cho uid=%s — bỏ qua.", uid_str
        )
        return False

    # ── Integrity validation (detect only — no mutations) ────────────────────
    is_valid, audit_log = _validate_data_integrity(data)
    if audit_log:
        for entry in audit_log:
            logger.info(f"[Integrity Audit] {entry}")

    # ── Tách global upgraded_weapons ─────────────────────────────────────────
    # data["upgraded_weapons"] = global dict {uid: wdata, ...} từ global_metadata.
    # user_data["upgraded_weapons"] = per-user list[dict] → lưu qua payload $set.
    # Hai cái này KHÁC NHAU — save_core_data xử lý đúng chỗ cho từng cái.
    global_uw = data.get("upgraded_weapons")
    if not isinstance(global_uw, dict):
        global_uw = {}

    async with _data_lock:   # Một save tại một thời điểm — tránh torn writes
        try:
            _with_retry(save_core_data, uid_str, user_data, global_uw)
            logger.debug("[SAVE] ✅ uid=%s lưu thành công.", uid_str)

            # Commit RAM snapshot sau khi DB xác nhận
            snapshot = copy.deepcopy(data)
            _good_snapshots.append(snapshot)
            if len(_good_snapshots) > 3:
                _good_snapshots.pop(0)   # evict the oldest snapshot

            return True

        except Exception as exc:
            logger.error(
                "[SAVE] ❌ Không thể lưu uid=%s sau %d lần thử: %s",
                uid_str, _MAX_RETRIES, exc, exc_info=True,
            )

            # ── RAM Rollback — khôi phục về snapshot cuối cùng tốt nhất ─────
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


# ══════════════════════════════════════════════════════════════════════════════
#  GET USER — Salvage-First + v5.5 Legacy Migration
# ══════════════════════════════════════════════════════════════════════════════

def _make_default_user() -> dict:
    """Return a fresh default user dict (avoid mutating a shared constant)."""
    return {
        "inv":              {},
        "weapons":          [],
        "weapon_instances": [],
        "equipped":         [None, None, None],
        "coins":            0,
        "passives":         {},
        "hunt_cd":          0,
        "crate_cd":         0,
        "hunt_log":         [],
    }


def get_user(uid, data: dict) -> dict:
    """
    Retrieve or create a user safely — Salvage-First philosophy.

    Principles:
    - NEVER delete player data if it can be recovered.
    - Each field has its own salvage strategy (convert, filter, patch).
    - Reset a field only when truly irrecoverable (logged explicitly).
    - Reset the whole user only when it is not a dict at all (extremely rare).

    Note (v1.8): user doc từ MongoDB có field '_id' (= uid_str).
    Field này được giữ nguyên — save_core_data tự strip trước khi $set.

    Note: bare base_id weapons ("467") are valid stack weapons and are left
    as-is.  Call ensure_weapon_uid() from the upgrade command when a UID is
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
    if "coins" not in user:
        user["coins"] = 0

    # ── Salvage each field in priority order ──────────────────────────────────
    _salvage_numeric(user, "coins",    0, uid)
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

    # Gọi migration để đảm bảo weapon_instances nhất quán
    # (phòng trường hợp file cũ vẫn dùng load_data → get_user pattern)
    from rpg_database import (
        migrate_upgraded_weapons,
        migrate_all_weapons_to_uid,
    )
    migrate_upgraded_weapons(user)
    migrate_all_weapons_to_uid(user)
    migrate_weapon_instance_fields(user)

    return user


# ══════════════════════════════════════════════════════════════════════════════
#  INVENTORY HELPERS (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  v5.5: add_weapon — FIXED (was appending raw base ID; now generates proper UID)
# ══════════════════════════════════════════════════════════════════════════════

def add_weapon(user: dict, base_id: str, make_unique: bool = True) -> str:
    """
    Add a weapon to the bag.

    make_unique=False  → append base_id directly (stack weapon, e.g. "467").
    make_unique=True   → generate a UID instance (e.g. "467-ABC12") and append it.

    In BOTH cases NO upgraded_weapons entry is created.
    Upgrade data is created lazily — only when the player actually upgrades
    the weapon — by calling ensure_upgrade_entry() from the upgrade command.

    Returns the stored weapon identifier (base_id or new UID).
    """
    if not isinstance(user.get("weapons"), list):
        user["weapons"] = []

    if not make_unique:
        user["weapons"].append(base_id)
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
    suffix  = uuid.uuid4().hex[:5].upper()
    new_uid = f"{base_id}-{suffix}"
    while new_uid in existing_uids:          # collision guard loop
        suffix  = uuid.uuid4().hex[:5].upper()
        new_uid = f"{base_id}-{suffix}"

    user["weapons"].append(new_uid)

    existing_wi_uids = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    if new_uid not in existing_wi_uids:
        try:
            w_data  = get_weapon_by_id(base_id) or {}
            rarity  = w_data.get("rarity", "common")
            quality = roll_quality(rarity)
            q_multi = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
            dur_max = int(DURABILITY_BY_RARITY.get(rarity, 30) * q_multi)
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
            "exp_to_next":    40,
            "quality":        quality,
            "durability":     dur_max,
            "durability_max": dur_max,
            "passive":        passive,
            "broken":         False,
        })

    return new_uid


def _find_weapon_in_bag(weapons: list, weapon_id: str) -> int | None:
    """
    ID-aware bag search.  Returns the index of the first matching weapon, or None.

    Search priority:
      1. Exact string match  — "467-ABC12" == "467-ABC12"
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
    Remove one weapon from the bag.  Returns True if found and removed.

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


# ══════════════════════════════════════════════════════════════════════════════
#  UPGRADE FLOW HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def ensure_upgrade_entry(user: dict, uid: str, base_id: str | None = None) -> None:
    """Deprecated: upgrade system đã được thay bằng weapon level system."""
    pass


def ensure_weapon_uid(user: dict, weapon_id: str) -> str | None:
    """
    Guarantee that the weapon has a UID in the bag.

    Call this at the START of any upgrade command to obtain the UID before
    reading or writing upgrade data.  After this call, use ensure_upgrade_entry()
    to create the upgraded_weapons record when the actual upgrade happens.

    This function ONLY handles UID identity — it does NOT create upgrade data.
    A UID can and should exist without an upgraded_weapons entry until the
    first upgrade is performed.

    Behaviour:
      • weapon_id is already a UID ("467-ABC12") and in bag:
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
    base_id   = get_base_id(stored_id)

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

    suffix  = uuid.uuid4().hex[:5].upper()
    new_uid = f"{base_id}-{suffix}"
    while new_uid in existing_uids:
        suffix  = uuid.uuid4().hex[:5].upper()
        new_uid = f"{base_id}-{suffix}"

    weapons[idx] = new_uid   # promote in-place (bag entry updated)

    # Tạo weapon_instance cho uid mới nếu chưa tồn tại
    existing_wi_uids = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    if new_uid not in existing_wi_uids:
        try:
            w_data  = get_weapon_by_id(base_id) or {}
            rarity  = w_data.get("rarity", "common")
            quality = roll_quality(rarity)
            q_multi = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
            dur_max = int(DURABILITY_BY_RARITY.get(rarity, 30) * q_multi)
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
            "exp_to_next":    120,
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


# ══════════════════════════════════════════════════════════════════════════════
#  EQUIP / UNEQUIP (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def equip_weapon(user: dict, weapon_id: str, slot: int | None = None) -> tuple[bool, str]:
    """
    Equip a weapon.  Returns (success, message).

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
        return False, "Vũ khí này đã được trang bị ở 1 ô rồi."

    if slot is not None:
        if slot not in (1, 2, 3):
            return False, "Ô trang bị chỉ từ 1 đến 3."
        idx_slot      = slot - 1
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

    weapon_id     = equipped[idx]
    equipped[idx] = None
    user["equipped"] = equipped

    # Return the weapon UID to the bag — the UID keeps its upgraded_weapons entry intact
    user["weapons"].append(weapon_id)
    return True, weapon_id






# ══════════════════════════════════════════════════════════════════════════════
#  EGG HANDLER (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def handle_egg(user: dict) -> list[dict]:
    """Hatch egg. Returns list of egg item dicts."""
    egg_item = get_item_by_id("004")
    count    = random.randint(1, 4)
    add_item(user, "004", count)
    return [egg_item] * count


