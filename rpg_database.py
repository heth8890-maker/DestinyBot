"""
rpg_database.py
---------------
Định nghĩa item/weapon catalog và các hàm truy cập dữ liệu user.

Đã thay toàn bộ logic JSON (load_data / save_data) bằng các hàm
từ database_helper (MongoDB).  Phần còn lại của hệ thống chỉ cần
gọi get_user() và save_user() — không cần biết gì về DB.

PATCH NOTES (migration fixes):
  - Added migrate_weapon_instance_fields() to normalize existing instances
    and create stubs for orphan UIDs in weapons[]/equipped[].
  - Fixed migrate_upgraded_weapons() to only remove successfully migrated
    entries instead of unconditionally clearing the whole list.
  - Fixed migrate_all_weapons_to_uid() to reuse orphan instances (created
    by migrate_upgraded_weapons) rather than always generating new level-1
    instances — prevents progression loss.
  - get_user() now calls all three migrations in dependency order.
  - Dirty auto-save wrapped in try/except so failures are surfaced, not swallowed.

KNOWN ARCHITECTURAL RISKS (require changes outside this file):
  - save_user() calls save_core_data() (MongoDB). If any caller still calls
    rpg_core.save_data() (JSON), writes will diverge silently. Audit all
    call-sites of rpg_core.save_data() and remove them.
  - _USER_DEFAULTS here may differ from rpg_core defaults. Consolidate into
    one authoritative source (recommend: here, imported by rpg_core).
  - Dirty auto-save in get_user() has no concurrency guard. Two simultaneous
    commands for the same user can produce stale overwrites. A full fix
    requires a per-user asyncio.Lock (or a DB-level CAS/version field) in
    database_helper. This is out of scope for this file alone.
"""

import copy
import logging
import uuid as _uuid

from database_helper import load_core_data, save_core_data
from typing import Optional
from rpg_instance import migrate_weapon_instance_fields as _migrate_instance_fields

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  CATALOGS (không đổi)
# ─────────────────────────────────────────────

ITEMS = [
    {"id": "001", "name": "Cành cây",  "emoji": "<:2849:1495250166352183347>", "min": 2,  "max": 7,  "chance": 0.80},
    {"id": "002", "name": "Dừa",       "emoji": "<:2857:1495250150334005390>", "min": 5,  "max": 9,  "chance": 0.40},
    {"id": "003", "name": "Da thú",    "emoji": "<:2851:1495250164116492469>", "min": 10, "max": 18, "chance": 0.12},
    {"id": "004", "name": "Ổ trứng",   "emoji": "<:2853:1495250161184800920>", "min": 0,  "max": 0,  "chance": 0.05},
]

WEAPONS = [
    {"id": "463", "name": "Gỗ cổ thụ",            "emoji": "<:2850:1495250168340156467>", "chance": 60},
    {"id": "464", "name": "Ngôi sao may mắn",      "emoji": "<:2860:1495250148295446540>", "chance": 5},
    {"id": "465", "name": "Tách trà thư giãn",     "emoji": "<:2863:1495250142364700883>", "chance": 25},
    {"id": "466", "name": "Chiếc kéo của Apolo",   "emoji": "<:2856:1495250154696081540>", "chance": 2},
    {"id": "467", "name": "Đuôi tắc kè hoa",       "emoji": "<:2861:1495250140326396034>", "chance": 8},
]


# ─────────────────────────────────────────────
#  WEAPON LEVELING CONSTANTS & HELPERS
# ─────────────────────────────────────────────

WEAPON_LEVEL_CAP = 50

RARITY_EXP_WEIGHT = {
    "common":    1,
    "uncommon":  3,
    "rare":      6,
    "epic":      15,
    "legendary": 30,
}


def exp_to_next(level: int) -> int:
    """EXP cần để lên level tiếp theo. Công thức: level * 80 + 40."""
    level = min(max(1, level), WEAPON_LEVEL_CAP)
    return level * 40 + 40


def calc_hunt_exp(found_items: list) -> int:
    """
    Tính tổng EXP từ danh sách item nhặt được khi hunt.
    Mỗi item có field 'rarity'. Dùng RARITY_EXP_WEIGHT.
    Item không có rarity hoặc không khớp → weight = 1.
    """
    total = 0
    for item in found_items:
        rarity = item.get("rarity", "common") if isinstance(item, dict) else "common"
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
    Cộng exp_amount vào weapon instance có uid trong user["weapon_instances"].
    Tự động level-up nếu exp >= exp_to_next (lặp đến khi hết hoặc đạt cap).
    KHÔNG save DB — chỉ mutate user dict in-place.

    Trả về:
        {"leveled_up": bool, "old_level": int, "new_level": int, "uid": uid}
    Nếu không tìm thấy uid:
        {"leveled_up": False, "old_level": 0, "new_level": 0, "uid": uid}
    """
    wi = next(
        (w for w in user.get("weapon_instances", [])
         if isinstance(w, dict) and w.get("uid") == uid),
        None,
    )
    if wi is None:
        return {"leveled_up": False, "old_level": 0, "new_level": 0, "uid": uid}

    wi.setdefault("level", 1)
    wi.setdefault("exp", 0)
    wi.setdefault("exp_to_next", exp_to_next(wi["level"]))

    old_level = wi.get("level", 1)
    wi["exp"]  = wi.get("exp", 0) + exp_amount

    leveled_up = False
    while wi["level"] < WEAPON_LEVEL_CAP and wi["exp"] >= wi["exp_to_next"]:
        wi["exp"]         -= wi["exp_to_next"]
        wi["level"]       += 1
        wi["exp_to_next"]  = exp_to_next(wi["level"])
        leveled_up = True

    if wi["level"] >= WEAPON_LEVEL_CAP:
        wi["exp"] = 0

    return {
        "leveled_up": leveled_up,
        "old_level":  old_level,
        "new_level":  wi["level"],
        "uid":        uid,
    }


# ─────────────────────────────────────────────
#  MIGRATION FUNCTIONS
#
#  Execution order in get_user() is critical:
#    1. migrate_upgraded_weapons()       — old format → instances (with real level data)
#    2. migrate_all_weapons_to_uid()     — bare ids → uids  (reuses instances from step 1)
#    3. migrate_weapon_instance_fields() — normalize all instances + create orphan stubs
#
#  All three functions are IDEMPOTENT: running them multiple times on the same
#  user dict produces the same result as running them once.
# ─────────────────────────────────────────────


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


# ─────────────────────────────────────────────
#  USER ACCESS
# ─────────────────────────────────────────────

# Các field bắt buộc phải có trong user doc — dùng để "fix cứng" user cũ.
#
# WARNING: If rpg_core.py defines its own defaults for the same keys with
# different values, the two files will silently conflict. Consolidate into
# one authoritative source (recommend: here, imported by rpg_core).
_USER_DEFAULTS = {
    "inv":              {},
    "weapons":          [],
    "equipped":         [None, None, None],
    "cooldown":         0,
    "weapon_instances": [],
}


def get_user(user_id) -> tuple[dict, list]:
    """
    Tải và trả về (user_data, upgraded_weapons) từ MongoDB.

    - Tự tạo user mới nếu chưa tồn tại.
    - Tự vá các key còn thiếu (backward-compat với user cũ).
    - Chạy toàn bộ migration pipeline theo đúng thứ tự phụ thuộc.
    - Auto-save nếu migration thay đổi dữ liệu.

    Migration order (critical — do not reorder):
        1. migrate_upgraded_weapons()       — old format → instances
        2. migrate_all_weapons_to_uid()     — bare ids → uids (reuses step-1 instances)
        3. migrate_weapon_instance_fields() — normalize all + create orphan stubs

    CONCURRENCY WARNING:
        The dirty auto-save below has no lock. Two simultaneous commands for the
        same user_id can both detect dirty=True, both save, then both callers
        overwrite each other's changes. A full fix requires a per-user
        asyncio.Lock (or DB-level version/CAS field) in database_helper.
        This is noted here but cannot be fixed in this file alone.

    Dùng:
        user, upgraded = get_user(ctx.author.id)
        # ... chỉnh sửa user ...
        save_user(ctx.author.id, user)
    """
    core = load_core_data(user_id)  # calls helper instead of reading JSON

    user             = core["user"]
    upgraded_weapons = core["upgraded_weapons"]

    # Patch missing keys (old users migrated from JSON or schema changes).
    for key, default in _USER_DEFAULTS.items():
        if key not in user:
            user[key] = copy.deepcopy(default)

    # Run migration pipeline in dependency order.
    # WHY order matters: migrate_all_weapons_to_uid() reuses instances created
    # by migrate_upgraded_weapons(); migrate_weapon_instance_fields() normalizes
    # whatever both prior passes produced.
    dirty  = migrate_upgraded_weapons(user)
    dirty |= migrate_all_weapons_to_uid(user)
    dirty |= migrate_weapon_instance_fields(user)   # local: normalizes level/exp/exp_to_next
    dirty |= _migrate_instance_fields(user)         # full: adds quality/durability/passive/broken

    if dirty:
        # FIX: Wrap in try/except so a save failure is surfaced, not silently
        # swallowed. The user dict is still usable for this request even if
        # save fails; the migration will simply re-run on the next get_user().
        try:
            ok = save_user(user_id, user)
            if not ok:
                log.error(
                    "get_user: dirty-save returned False for user_id=%s — "
                    "migration changes not persisted", user_id
                )
        except Exception:
            log.exception(
                "get_user: dirty-save raised for user_id=%s — "
                "migration changes not persisted", user_id
            )

    return user, upgraded_weapons


def save_user(user_id, user_data: dict, upgraded_weapons=None) -> bool:
    """
    Lưu dữ liệu user lên MongoDB.
    upgraded_weapons đã nằm trong user_data nên không cần truyền riêng.
    Param upgraded_weapons giữ lại để không break caller cũ (ignored).

    Trả về True nếu thành công, False nếu có lỗi (helper đã log).

    WARNING: This calls save_core_data() (MongoDB). If any other code path
    calls rpg_core.save_data() (likely JSON), the two writes target different
    backends and will diverge. Audit all callers of rpg_core.save_data().

    Dùng:
        ok = save_user(ctx.author.id, user)
        if not ok:
            await ctx.send("⚠️ Lưu dữ liệu thất bại, thử lại sau!")
    """
    return save_core_data(user_id, user_data)


# ─────────────────────────────────────────────
#  CATALOG LOOKUPS  (không đổi, không cần DB)
# ─────────────────────────────────────────────

def get_item_by_id(item_id: str) -> Optional[dict]:
    """Trả về item dict theo id, hoặc None nếu không tìm thấy."""
    return next((item for item in ITEMS if item["id"] == item_id), None)


def get_weapon_by_id(weapon_id: str) -> Optional[dict]:
    """Trả về weapon dict theo id, hoặc None nếu không tìm thấy."""
    return next((w for w in WEAPONS if w["id"] == weapon_id), None)
