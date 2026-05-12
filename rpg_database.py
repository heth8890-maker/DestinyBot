"""
rpg_database.py
---------------
Định nghĩa item/weapon catalog và các hàm truy cập dữ liệu user.

Đã thay toàn bộ logic JSON (load_data / save_data) bằng các hàm
từ database_helper (MongoDB).  Phần còn lại của hệ thống chỉ cần
gọi get_user() và save_user() — không cần biết gì về DB.
"""

import copy
import uuid as _uuid

from database_helper import load_core_data, save_core_data
from typing import Optional

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


def migrate_upgraded_weapons(user: dict) -> bool:
    """
    Chuyển đổi user["upgraded_weapons"] (format cũ) sang user["weapon_instances"].
    Chỉ chạy nếu upgraded_weapons không rỗng.
    Trả về True nếu đã migrate, False nếu không cần.
    """
    old_list = user.get("upgraded_weapons", [])
    if not old_list:
        return False

    existing_uids = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    for entry in old_list:
        if not isinstance(entry, dict):
            continue
        uid     = entry.get("uid")
        base_id = entry.get("base_id")
        if not uid or not base_id:
            continue
        if uid in existing_uids:
            continue
        eff_levels = entry.get("effect_levels", {})
        raw_values = [v for v in eff_levels.values() if isinstance(v, (int, float))]
        level = int(max(raw_values)) if raw_values else 1
        level = min(max(1, level), WEAPON_LEVEL_CAP)
        user.setdefault("weapon_instances", []).append(
            make_weapon_instance(base_id, uid, level)
        )
        existing_uids.add(uid)

    user["upgraded_weapons"] = []
    return True


def migrate_all_weapons_to_uid(user: dict) -> bool:
    """
    Đảm bảo toàn bộ weapons[] và equipped[] đều là UID (có dấu "-").
    Với mỗi bare base_id tìm thấy:
      - Tạo UID mới (format: base_id-XXXXX)
      - Tạo weapon_instance tương ứng
      - Replace in-place
    Trả về True nếu có thay đổi, False nếu không cần.
    """
    changed = False

    # Thu thập tất cả UID đang tồn tại để tránh trùng
    existing_uids: set[str] = set()
    for w in user.get("weapons", []):
        if isinstance(w, str) and "-" in w:
            existing_uids.add(w)
    for w in user.get("equipped", []):
        if isinstance(w, str) and w and "-" in w:
            existing_uids.add(w)
    for wi in user.get("weapon_instances", []):
        if isinstance(wi, dict) and "uid" in wi:
            existing_uids.add(wi["uid"])

    existing_wi_uids: set[str] = {
        wi["uid"] for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    def _make_uid(base_id: str) -> str:
        suffix = _uuid.uuid4().hex[:5].upper()
        uid = f"{base_id}-{suffix}"
        while uid in existing_uids:
            suffix = _uuid.uuid4().hex[:5].upper()
            uid = f"{base_id}-{suffix}"
        existing_uids.add(uid)
        return uid

    def _ensure_instance(uid: str, base_id: str) -> None:
        if uid not in existing_wi_uids:
            user.setdefault("weapon_instances", []).append(
                make_weapon_instance(base_id, uid, level=1)
            )
            existing_wi_uids.add(uid)

    # Convert weapons[] (bag)
    for i, wid in enumerate(user.get("weapons", [])):
        if not isinstance(wid, str) or "-" in wid:
            continue
        new_uid = _make_uid(wid)
        user["weapons"][i] = new_uid
        _ensure_instance(new_uid, wid)
        changed = True

    # Convert equipped[]
    for i, wid in enumerate(user.get("equipped", [])):
        if not isinstance(wid, str) or not wid or "-" in wid:
            continue
        new_uid = _make_uid(wid)
        user["equipped"][i] = new_uid
        _ensure_instance(new_uid, wid)
        changed = True

    return changed


# ─────────────────────────────────────────────
#  USER ACCESS
# ─────────────────────────────────────────────

# Các field bắt buộc phải có trong user doc — dùng để "fix cứng" user cũ
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
    - upgraded_weapons là list (không phải dict).

    Dùng:
        user, upgraded = get_user(ctx.author.id)
        # ... chỉnh sửa user ...
        save_user(ctx.author.id, user)
    """
    core = load_core_data(user_id)          # gọi helper thay vì đọc JSON

    user             = core["user"]
    upgraded_weapons = core["upgraded_weapons"]

    # Vá key còn thiếu (user cũ migrate từ JSON hoặc schema thay đổi)
    for key, default in _USER_DEFAULTS.items():
        if key not in user:
            user[key] = copy.deepcopy(default)

    dirty  = migrate_upgraded_weapons(user)
    dirty |= migrate_all_weapons_to_uid(user)
    if dirty:
        save_user(user_id, user)

    return user, upgraded_weapons


def save_user(user_id, user_data: dict, upgraded_weapons=None) -> bool:
    """
    Lưu dữ liệu user lên MongoDB.
    upgraded_weapons đã nằm trong user_data nên không cần truyền riêng.
    Param upgraded_weapons giữ lại để không break caller cũ (ignored).

    Trả về True nếu thành công, False nếu có lỗi (helper đã log).

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
