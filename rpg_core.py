"""
===== FILE: rpg_core.py (REFACTORED v1.7 — UNIFIED HYBRID WEAPON IDENTITY) =====

ARCHITECTURE CHANGELOG v1.7 — HYBRID MODEL ENFORCEMENT:
────────────────────────────────────────────────────────────────────────
ROOT CAUSE FIX:
  Previous versions conflated "has a UID" with "has upgrade data", creating
  a hidden second weapon type.  This broke equip, sell, and upgrade because
  commands saw two incompatible code paths for the same weapon.

UNIFIED IDENTITY RULES (strict):
  • base_id  = canonical weapon type  ("467")
  • unique_id = instance of that type ("467-ABC12")
  • Both forms are fully valid in user["weapons"] and user["equipped"].
  • ALL gameplay logic resolves through get_base_id(weapon_id) first.
  • UID existence does NOT imply upgrade data exists.

WHAT CHANGED vs v5.6:
  1. add_weapon(make_unique=True)
       — no longer creates an upgraded_weapons entry on weapon acquisition.
       — UID is stored in bag; upgrade data is created ONLY on first upgrade.

  2. ensure_weapon_uid()
       — now ONLY promotes base_id → UID in-place (lazy, on demand).
       — REMOVED: implicit _ensure_upgraded_entry() call.
       — Upgrade commands must call ensure_upgrade_entry() separately.

  3. ensure_upgrade_entry()  [was: _ensure_upgraded_entry, now exported]
       — public helper for the upgrade command to call explicitly.
       — creates upgraded_weapons entry ONLY when an upgrade is about to happen.

  4. _migrate_legacy_weapons()
       — no longer creates upgraded_weapons entries during UID promotion.

  5. _validate_data_integrity() Phase 2 REMOVED
       — UIDs without upgraded_weapons entries are now VALID (not an error).
       — Phase 1 (global duplicate UID detection) is retained.

UPGRADE FLOW (correct call sequence for rpg_addon):
  uid = ensure_weapon_uid(user, weapon_id)     # 1. guarantee UID exists
  ensure_upgrade_entry(user, uid)              # 2. guarantee upgrade record exists
  # ... read/write upgrade data ...            # 3. perform upgrade

UNCHANGED:
  • TRUE ATOMIC SAVE      — asyncio.Lock(_data_lock) guards every save_data() call.
  • RAM SNAPSHOT ROLLBACK — _good_snapshots[] (max 3).
  • equip_weapon()        — ID-aware bag lookup; equips actual stored ID.
  • remove_weapon_from_bag() — ID-aware; no ghost items.
  • WeaponEntity V5       — GLOBAL_SECRET + HMAC-SHA256 deterministic stat seeds.
  • parse_effects()       — resolves via get_base_id(); scales if UID found.

PHILOSOPHY: "SALVAGE FIRST, RESET LAST"
"""

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

from rpg_item import (
    ITEMS,
    BASE_RARITY_RATES,
    get_item_by_id,
    _pick_item_from_rarity,
)
from rpg_weapon import (
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
"DARK_CRATE_WEAPON",   # ← THÊM DÒNG NÀY
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

# ─── File paths ─────────────────────────────────────────────────────────────────
DATA_FILE    = "rpg_data.json"
TEMP_FILE    = DATA_FILE + ".tmp"
BACKUP_FILES = [
    "rpg_data.backup_0.json",   # newest
    "rpg_data.backup_1.json",
    "rpg_data.backup_2.json",   # oldest
]
LEGACY_BACKUP = "rpg_data.backup.json"

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

# v5.5: Single global lock — only ONE save_data() runs at a time.
# Prevents torn writes when two commands race to persist state simultaneously.
_data_lock: asyncio.Lock = asyncio.Lock()

# v5.5: Rolling RAM snapshots. Written ONLY after a successful disk verify.
# On catastrophic save failure, the last good snapshot is used to roll back RAM.
_good_snapshots: list[dict] = []   # max 3; pop(0) when full


def get_user_lock(uid: str) -> asyncio.Lock:
    """
    Returns the asyncio.Lock for user_id — creates one if absent.
    Thread-safe within a single-threaded asyncio event loop.

    Usage:
        async with get_user_lock(uid):
            data = load_data()
            user = get_user(uid, data)
            # ... modify user ...
            await save_data(data)
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

    def __init__(self, uid: str, base_data: dict, upgrade_data: dict | None):
        self.uid          = uid
        self.base_data    = base_data
        self.upgrade_data = upgrade_data

    # ── Display helpers ────────────────────────────────────────────────────────

    def fmt_name(self) -> str:
        """Name + emoji + [<:Upgradeeffect:1498218616376524912> if upgraded]. Used in all lists."""
        name  = self.base_data.get("name", self.uid)
        emoji = self.base_data.get("emoji", "")
        tag   = " <:Upgradeeffect:1498218616376524912>" if self.upgrade_data is not None else ""
        return f"{emoji} **{name}**{tag}"

    def fmt_stats(self) -> str:
        """
        Unified effects string — shows scaled values if upgrade exists.
        Returns a single str (do NOT iterate over this; use it as a field value).
        Used by: inv, status panel, upgrade panel, givew embed.
        """
        effects = self.base_data.get("effects", {})
        if not effects:
            return "Không có hiệu ứng."

        eff_levels: dict = {}
        if self.upgrade_data:
            eff_levels = self.upgrade_data.get("effect_levels", {})

        lines = []
        for k, bv in effects.items():
            lv = eff_levels.get(k, 1)

            if self.upgrade_data and lv > 1:
                try:
                    from rpg_addon import effect_value_at_level
                    v = effect_value_at_level(bv, lv, k)
                except Exception:
                    v = bv
            else:
                v = bv

            lv_tag = f" _(lv{lv})_" if lv > 1 else ""

            if k == "extra_slot":
                lines.append(f"• `{k}`: +{int(v)} ô{lv_tag}")
            elif isinstance(v, float):
                lines.append(f"• `{k}`: +{round(v * 100, 1)}%{lv_tag}")
            else:
                lines.append(f"• `{k}`: {v}{lv_tag}")

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
        state is stored in JSON. Changing GLOBAL_SECRET rotates all rolls.

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


def get_weapon_entity(user: dict, uid: str) -> WeaponEntity | None:
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

    return WeaponEntity(uid, base_data, upgrade_data)


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS — file I/O, rotation, emergency dump
# ══════════════════════════════════════════════════════════════════════════════

def _try_load_json_file(path: str) -> dict | None:
    """
    Safely read a JSON file.
    Returns dict on success, None on any failure (missing, empty, corrupt).
    """
    try:
        if not os.path.exists(path):
            return None
        if os.path.getsize(path) == 0:
            logger.warning(f"⚠️  {path} is empty (0 bytes)")
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"⚠️  {path}: root is not dict ({type(data).__name__})")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"❌ {path} JSON corrupt: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Cannot read {path}: {e}")
        return None


def _rotate_backups(valid_data: dict) -> None:
    """
    Rotate 3 disk backups: backup_1 → backup_2, backup_0 → backup_1, write new backup_0.
    Only runs if valid_data is non-empty.
    """
    if not valid_data:
        return
    try:
        if os.path.exists(BACKUP_FILES[1]):
            os.replace(BACKUP_FILES[1], BACKUP_FILES[2])
        if os.path.exists(BACKUP_FILES[0]):
            os.replace(BACKUP_FILES[0], BACKUP_FILES[1])
        with open(BACKUP_FILES[0], "w", encoding="utf-8") as f:
            json.dump(valid_data, f, indent=4, ensure_ascii=False)
        logger.debug(f"📦 Backup rotated: {len(valid_data)} users → {BACKUP_FILES[0]}")
    except Exception as e:
        logger.warning(f"⚠️  Backup rotation failed: {e}")


def _emergency_dump(data: dict) -> None:
    """
    Last resort when save_data() fails completely — writes a timestamped emergency file.
    Admin must restore manually. Data is never silently lost.
    """
    try:
        ts       = int(time.time())
        emg_path = f"rpg_data.EMERGENCY_{ts}.json"
        with open(emg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.critical(
            f"🚨 EMERGENCY DUMP: {len(data)} users → {emg_path} | "
            f"Check disk/permissions and restore manually!"
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

    Returns:
        (is_valid, audit_log) — is_valid is True only if zero issues were found.
    """
    audit:  list[str] = []
    issues: int       = 0

    # ── Phase 1: Collect all Unique IDs globally; detect duplicates ───────────
    global_uids: dict[str, str] = {}   # uid → "user_id:source" (first occurrence)

    for user_id, user in data.items():
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
#  SALVAGE HELPERS (unchanged from v3)
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
#  LOAD — Multi-tier recovery (unchanged from v3)
# ══════════════════════════════════════════════════════════════════════════════

def load_data() -> dict:
    """
    Load JSON data safely — tries sources in priority order until one succeeds.

    Order:
        1. rpg_data.json           (primary)
        2. rpg_data.json.tmp       (leftover from interrupted save)
        3. rpg_data.backup_0.json  (newest rotating backup)
        4. rpg_data.backup_1.json
        5. rpg_data.backup_2.json  (oldest rotating backup)
        6. rpg_data.backup.json    (legacy compatibility)

    Never crashes the bot on startup.
    """
    candidates = [
        (DATA_FILE,       "primary file"),
        (TEMP_FILE,       "temp file (.tmp)"),
        (BACKUP_FILES[0], "backup_0 (newest)"),
        (BACKUP_FILES[1], "backup_1"),
        (BACKUP_FILES[2], "backup_2 (oldest)"),
        (LEGACY_BACKUP,   "legacy backup"),
    ]

    for path, label in candidates:
        data = _try_load_json_file(path)
        if data is not None:
            if path == DATA_FILE:
                logger.info(f"✅ Loaded {label}: {len(data)} users")
            else:
                logger.warning(
                    f"⚠️  Primary failed — recovered from {label} ({path}): {len(data)} users"
                )
            return data

    logger.error(
        "🚨 All sources failed / do not exist — returning empty dict. "
        "Bot is still alive; new users will be created on demand."
    )
    return {}


# ══════════════════════════════════════════════════════════════════════════════
#  v5.5: TRUE ATOMIC SAVE — Lock + Validator + Snapshot + Rollback
# ══════════════════════════════════════════════════════════════════════════════

async def save_data(data: dict) -> None:
    """
    Persist data safely.  ALL callers MUST await this coroutine.

    Pipeline (inside _data_lock):
    ┌─────────────────────────────────────────────────────────────────────────┐
    │ 0. _validate_data_integrity(data) — detect corruption before disk write  │
    │ 1. Rotate disk backups from the current live file                       │
    │ 2. Write → TEMP_FILE                                                    │
    │ 3. Verify TEMP_FILE exists + size > 0                                   │
    │ 4. Re-parse TEMP_FILE (post-write JSON verify)                          │
    │ 5. os.replace(TEMP_FILE, DATA_FILE)  — atomic on most OS/filesystems    │
    │ 6. Commit deep-copy snapshot to _good_snapshots[] (max 3)               │
    └─────────────────────────────────────────────────────────────────────────┘

    On any exception:
      - TEMP_FILE is deleted (primary file untouched on disk).
      - RAM is rolled back to last good snapshot (data dict modified in-place).
      - _emergency_dump() fires to guarantee data is not silently lost.
    """
    global _good_snapshots

    if not isinstance(data, dict):
        logger.error(f"❌ save_data() got {type(data).__name__} instead of dict — aborting")
        return

    async with _data_lock:   # Only one save_data() runs at a time — prevents torn writes

        # ── Step 0: Integrity validation (detect only — no mutations) ────────
        is_valid, audit_log = _validate_data_integrity(data)
        if audit_log:
            for entry in audit_log:
                logger.info(f"[Integrity Audit] {entry}")

        try:
            # ── Step 1: Rotate disk backup from live file ─────────────────────
            existing = _try_load_json_file(DATA_FILE)
            if existing:
                _rotate_backups(existing)

            # ── Step 2: Write to temp file ────────────────────────────────────
            with open(TEMP_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            # ── Step 3: Verify TEMP_FILE exists + non-zero size ───────────────
            if not os.path.exists(TEMP_FILE):
                raise IOError(f"{TEMP_FILE} does not exist after write")
            file_size = os.path.getsize(TEMP_FILE)
            if file_size == 0:
                raise IOError(f"{TEMP_FILE} is 0 bytes after write — possible disk full")

            # ── Step 4: Post-write JSON verification ──────────────────────────
            verified = _try_load_json_file(TEMP_FILE)
            if verified is None:
                raise IOError(
                    f"{TEMP_FILE} written but re-parse failed — file may be corrupt"
                )
            if len(verified) != len(data):
                logger.warning(
                    f"⚠️  Post-verify user count mismatch "
                    f"(input={len(data)}, verified={len(verified)}) — proceeding anyway"
                )

            # ── Step 5: Atomic replace ────────────────────────────────────────
            os.replace(TEMP_FILE, DATA_FILE)
            logger.info(f"✅ save_data(): {len(data)} users → {DATA_FILE} ({file_size:,} bytes)")

            # ── Step 6: Commit RAM snapshot (ONLY after successful disk write) ─
            # deep-copy so future mutations to `data` don't corrupt the snapshot.
            snapshot = copy.deepcopy(data)
            _good_snapshots.append(snapshot)
            if len(_good_snapshots) > 3:
                _good_snapshots.pop(0)   # evict the oldest snapshot
            logger.debug(
                f"📸 Snapshot committed ({len(_good_snapshots)}/3 slots used)"
            )

        except Exception as e:
            logger.error(f"❌ save_data() failed: {e}")

            # Clean up temp file so the next load doesn't read it accidentally
            try:
                if os.path.exists(TEMP_FILE):
                    os.remove(TEMP_FILE)
            except Exception:
                pass

            # ── RAM Rollback — restore in-memory dict to last known-good state ─
            if _good_snapshots:
                last_good = _good_snapshots[-1]
                data.clear()
                data.update(last_good)   # mutates the dict in-place (caller sees the rollback)
                logger.warning(
                    f"🔄 RAM ROLLBACK: restored from snapshot "
                    f"({len(last_good)} users). Disk state is unchanged."
                )
            else:
                logger.critical(
                    "🚨 No snapshots available for rollback — "
                    "in-memory state may be inconsistent. Restart recommended."
                )

            # Emergency dump — data must never be silently lost
            _emergency_dump(data)


# ══════════════════════════════════════════════════════════════════════════════
#  GET USER — Salvage-First + v5.5 Legacy Migration
# ══════════════════════════════════════════════════════════════════════════════

def _make_default_user() -> dict:
    """Return a fresh default user dict (avoid mutating a shared constant)."""
    return {
        "inv":              {},
        "weapons":          [],
        "upgraded_weapons": [],
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

# Trong rpg_core.py

def add_weapon(user: dict, base_id: str, make_unique: bool = False) -> str:
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
    """
    Lazily create an upgraded_weapons entry for uid if one does not exist.
    Idempotent — safe to call multiple times on the same uid.

    WHEN TO CALL:
        Call this from the upgrade command, AFTER ensure_weapon_uid() has
        guaranteed the UID exists in the bag.  Never call it during weapon
        acquisition or equip — upgrade data must only be created when an
        upgrade is actually about to happen.

    Args:
        user     : user dict from get_user()
        uid      : the weapon's UID (must already be in the bag)
        base_id  : optional — if omitted, resolved automatically via get_base_id()
    """
    # FIX: guard against bare base_id ("467") being written as an upgrade key.
    # upgrade entries keyed on base_id are invisible to parse_effects (needs "-")
    # and will never match a UID lookup.  The caller must call ensure_weapon_uid()
    # first to obtain a proper UID before passing it here.
    if "-" not in uid:
        logger.warning(
            f"ensure_upgrade_entry: called with bare base_id '{uid}' — "
            f"a UID (containing '-') is required.  "
            f"Call ensure_weapon_uid() first.  Skipping."
        )
        return

    resolved_base = base_id if base_id else get_base_id(uid)

    uw_list: list = user.setdefault("upgraded_weapons", [])
    for entry in uw_list:
        if isinstance(entry, dict) and entry.get("uid") == uid:
            return   # already exists — idempotent

    base_data = get_weapon_by_id(resolved_base) or {}
    effects   = base_data.get("effects") or {}
    uw_list.append({
        "uid":           uid,
        "base_id":       resolved_base,
        "effect_levels": {k: 1 for k in effects},
    })
    logger.debug(f"ensure_upgrade_entry: created entry for UID '{uid}'")


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

    logger.info(
        f"ensure_weapon_uid: promoted '{stored_id}' → '{new_uid}'"
    )
    return new_uid


# ══════════════════════════════════════════════════════════════════════════════
#  EQUIP / UNEQUIP (unchanged from v3)
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
        idx_slot     = slot - 1
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
#  SAFE PARSE EFFECTS (unchanged from v3)
# ══════════════════════════════════════════════════════════════════════════════

def parse_effects(equipped: list, user: dict | None = None) -> dict:
    """
    Aggregate weapon effects from the equipped list.

    Algorithm (per slot wid):
      1. base_id = get_base_id(wid)          — canonical resolver, no raw .split
      2. base_data = get_weapon_by_id(base_id) — stats from WEAPON_DATABASE
      3. If wid is a UID ("-" present) AND user supplied:
           • Look up user["upgraded_weapons"] for this exact UID.
           • Scale each numeric effect with rpg_addon.effect_value_at_level(base, lv, key).
      4. Accumulate scaled_effects into aggregate totals.

    Safety:
      - equipped not a list  → return {} immediately.
      - None slots           → silently skip.
      - Unknown weapon IDs   → skip + log.
      - effect_value_at_level failures → fall back to base stats for that weapon.
      - Any per-weapon exception       → skip that weapon, never crash the whole call.
    """
    if not isinstance(equipped, list):
        logger.warning(
            f"parse_effects: equipped is {type(equipped).__name__} instead of list → return {{}}"
        )
        return {}

    agg: dict = {
        "extra_slot":      0,
        "luck_up":         0.0,
        "rare_bias":       0.0,
        "reduce_fail":     0.0,
        "double_drop":     0.0,
        "reduce_cooldown": 0.0,
        "sell_bonus":      0.0,
        "sell_boost":      0.0,
        "reduce_uncommon": 0.0,
        "double_value":    0.0,
        "passive_oneiroi": 0.0,
    }

    # Build O(1) upgrade map once — avoids repeated list scans inside the loop
    uw_map: dict[str, dict] = {}
    if user is not None:
        uw_map = {
            uw["uid"]: uw
            for uw in user.get("upgraded_weapons", [])
            if isinstance(uw, dict) and "uid" in uw
        }

    for wid in equipped:
        try:
            if wid is None:
                continue
            if not isinstance(wid, str) or not wid:
                logger.warning(
                    f"parse_effects: weapon_id is {type(wid).__name__}"
                    f"({wid!r}) — skipping"
                )
                continue

            # ── Step 1: canonical base-ID resolution ──────────────────────────
            base_id = get_base_id(wid)
            if not base_id:
                logger.warning(f"parse_effects: empty base_id from '{wid}' — skipping")
                continue

            # ── Step 2: base stats from WEAPON_DATABASE ───────────────────────
            base_data = get_weapon_by_id(base_id)
            if not base_data:
                logger.warning(f"parse_effects: no weapon data for base_id='{base_id}' — skipping")
                continue

            effects: dict = dict(base_data.get("effects", {}))

            # ── Step 3: apply upgrade scaling when wid is a UID ───────────────
            is_uid = "-" in wid
            if is_uid and uw_map:
                uw = uw_map.get(wid)
                if uw:
                    eff_levels: dict = uw.get("effect_levels", {})
                    try:
                        from rpg_addon import effect_value_at_level
                        for k in list(effects.keys()):
                            if isinstance(effects[k], (int, float)):
                                lv = max(1, eff_levels.get(k, 1))
                                effects[k] = effect_value_at_level(effects[k], lv, k)
                    except ImportError:
                        logger.warning(
                            "parse_effects: rpg_addon unavailable — using base stats"
                        )
                    except Exception as scale_err:
                        logger.error(
                            f"parse_effects: effect_value_at_level failed for '{wid}': {scale_err}"
                            " — using base stats for this weapon"
                        )

            # ── Step 4: accumulate into aggregate ─────────────────────────────
            for key in agg:
                val = effects.get(key)
                if val is None:
                    continue
                try:
                    agg[key] += val
                except (TypeError, ValueError) as add_err:
                    logger.warning(
                        f"parse_effects: cannot add '{key}'={val!r} from '{wid}': {add_err}"
                    )

        except Exception as e:
            logger.error(f"parse_effects: unexpected error for weapon_id={wid!r}: {e}")
            continue

    return agg


# ══════════════════════════════════════════════════════════════════════════════
#  HUNT ROLL ENGINE (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def _build_rarity_table(effects: dict) -> dict:
    """Build rarity probability table from weapon effects."""
    rates           = dict(BASE_RARITY_RATES)
    rare_bias       = effects.get("rare_bias",       0.0)
    luck_up         = effects.get("luck_up",         0.0)
    reduce_uncommon = effects.get("reduce_uncommon", 0.0)

    common_shift       = min(luck_up * 100, rates["common"])
    rates["common"]   -= common_shift
    rates["uncommon"] += common_shift

    if reduce_uncommon > 0:
        cut = min(reduce_uncommon * 100, rates["uncommon"])
        rates["uncommon"] -= cut
        rates["rare"]     += cut

    if rare_bias > 0:
        extra          = rare_bias * 100
        rare_gain      = extra * 0.60
        epic_gain      = extra * 0.28
        legendary_gain = extra * 0.12 * 0.30

        taken = 0
        for tier in ("uncommon", "common"):
            can_take = min(rates[tier], extra - taken)
            rates[tier] -= can_take
            taken       += can_take
            if taken >= extra:
                break

        rates["rare"]      += rare_gain
        rates["epic"]      += epic_gain
        rates["legendary"] += legendary_gain
    else:
        rates["epic"]      = 0.0
        rates["legendary"] = 0.0

    total = sum(rates.values())
    if total > 0:
        for k in rates:
            rates[k] = rates[k] / total * 100
    return rates


def roll_hunt_items(equipped: list, user: dict | None = None) -> list[dict]:
    """Roll hunt items. Returns list of item dicts."""
    effects     = parse_effects(equipped, user)
    base_slots  = 4
    total_slots = min(base_slots + int(effects.get("extra_slot", 0)), 18)
    fail_rate   = max(0.0, 0.20 - effects.get("reduce_fail", 0.0))

    rarity_table = _build_rarity_table(effects)
    rarities     = list(rarity_table.keys())
    weights      = [rarity_table[r] for r in rarities]

    found: list[dict] = []
    double_drop = effects.get("double_drop", 0.0)

    for _ in range(total_slots):
        if random.random() < fail_rate:
            continue
        chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]
        item = _pick_item_from_rarity(chosen_rarity)
        if item is None:
            continue
        found.append(item)
        if double_drop > 0 and random.random() < double_drop:
            found.append(item)

    return found


# ══════════════════════════════════════════════════════════════════════════════
#  EGG HANDLER (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def handle_egg(user: dict) -> list[dict]:
    """Hatch egg. Returns list of egg item dicts."""
    egg_item = get_item_by_id("004")
    count    = random.randint(1, 4)
    add_item(user, "004", count)
    return [egg_item] * count


# ══════════════════════════════════════════════════════════════════════════════
#  CALCULATORS (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def calc_hunt_cooldown(
    equipped: list, base_cd: float = 15.0, user: dict | None = None
) -> float:
    """Calculate hunt cooldown with weapon bonuses."""
    effects   = parse_effects(equipped, user)
    reduction = effects.get("reduce_cooldown", 0.0)
    cd        = base_cd * (1.0 - reduction)
    return max(5.0, cd)


def calc_sell_value(item: dict, qty: int, effects: dict) -> int:
    """Calculate total sell value for items."""
    sell_mult    = 1.0 + effects.get("sell_bonus", 0.0) + effects.get("sell_boost", 0.0)
    item_mult    = item.get("rare_multiplier", 1.0)
    double_value = effects.get("double_value", 0.0)
    total = 0
    for _ in range(qty):
        base = random.randint(item["min"], item["max"])
        val  = int(base * sell_mult * item_mult)
        if double_value > 0 and random.random() < double_value:
            val *= 2
        total += val
    return total
