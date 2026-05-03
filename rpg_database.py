"""
rpg_database.py
---------------
Định nghĩa item/weapon catalog và các hàm truy cập dữ liệu user.

Đã thay toàn bộ logic JSON (load_data / save_data) bằng các hàm
từ database_helper (MongoDB).  Phần còn lại của hệ thống chỉ cần
gọi get_user() và save_user() — không cần biết gì về DB.
"""

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
#  USER ACCESS
# ─────────────────────────────────────────────

# Các field bắt buộc phải có trong user doc — dùng để "fix cứng" user cũ
_USER_DEFAULTS = {
    "inv":               {},
    "weapons":           [],
    "equipped":          [],
    "cooldown":          0,
    "upgraded_weapons":  [],
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

    user            = core["user"]
    upgraded_weapons = core["upgraded_weapons"]

    # Vá key còn thiếu (user cũ migrate từ JSON hoặc schema thay đổi)
    for key, default in _USER_DEFAULTS.items():
        if key not in user:
            user[key] = default

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
