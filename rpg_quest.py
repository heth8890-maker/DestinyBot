"""
===== FILE: rpg_quest.py =====
Hệ thống Daily Quest: mỗi người nhận 1–2 nhiệm vụ ngẫu nhiên,
reset mỗi 24h.

Lưu trữ: MongoDB (collection "quest_data") qua database_helper.

Public API (không đổi):
  should_reset_quest / reset_quest
  add_quest_progress       ← trả list quest_type vừa hoàn thành
  get_current_quests / get_current_quest  (compat)
  claim_quest_reward
  QUEST_TYPES / QUEST_PROGRESS_KEYS
"""

import random
import time
import logging

import pymongo

from database_helper import _get_client, _with_retry, DB_NAME

log = logging.getLogger("rpg_quest")

# ── Collection helper ──
def _col():
    return _get_client()[DB_NAME]["quest_data"]


# ── 8 loại quest ──
QUEST_TYPES = {
    "hunt_times": {
        "name": "Săn liên tiếp",
        "description": "Hoàn thành {target} lần hunt",
        "target": 50,
        "reward": 8000,
        "progress_key": "hunts",
    },
    "sell_items": {
        "name": "Nhà buôn",
        "description": "Bán {target} vật phẩm",
        "target": 1000,
        "reward": 6000,
        "progress_key": "items_sold",
    },
    "equip_weapon": {
        "name": "Trang bị vũ khí",
        "description": "Trang bị {target} vũ khí (có thể khác nhau)",
        "target": 3,
        "reward": 5000,
        "progress_key": "weapons_equipped",
    },
    "collect_items": {
        "name": "Collector",
        "description": "Thu thập {target} vật phẩm",
        "target": 300,
        "reward": 7000,
        "progress_key": "items_collected",
    },
    "sell_weapons": {
        "name": "Tháo vũ khí",
        "description": "Bán {target} vũ khí",
        "target": 3,
        "reward": 4500,
        "progress_key": "weapons_sold",
    },
    "open_crates": {
        "name": "Mở kho báu",
        "description": "Mở {target} crate",
        "target": 5,
        "reward": 8000,
        "progress_key": "crates_opened",
    },
    "high_rarity": {
        "name": "Săn hiếm",
        "description": "Thu thập {target} vật phẩm rare+",
        "target": 30,
        "reward": 12000,
        "progress_key": "rare_collected",
    },
    "trade_success": {
        "name": "Buôn bán",
        "description": "Giao dịch thành công {target} lần",
        "target": 2,
        "reward": 11500,
        "progress_key": "trades_done",
    },
}

QUEST_PROGRESS_KEYS = {k: v["progress_key"] for k, v in QUEST_TYPES.items()}
ALL_PROGRESS_KEYS   = list({v["progress_key"] for v in QUEST_TYPES.values()})


# ═══════════════════════════════════════════════════════════
# PROFILE HELPERS
# ═══════════════════════════════════════════════════════════

def _blank_progress() -> dict:
    return {k: 0 for k in ALL_PROGRESS_KEYS}


def _ensure_profile(uid_str: str) -> dict:
    """Lấy profile từ MongoDB, tạo mới nếu chưa có."""
    col     = _col()
    profile = _with_retry(col.find_one, {"_id": uid_str})

    if not profile:
        profile = {"_id": uid_str, "last_reset": 0, "quests": [], **_blank_progress()}
        try:
            _with_retry(col.insert_one, profile.copy())
        except pymongo.errors.DuplicateKeyError:
            profile = _with_retry(col.find_one, {"_id": uid_str})
    else:
        # Đảm bảo các progress key tồn tại (khi thêm quest type mới)
        missing = {k: 0 for k in ALL_PROGRESS_KEYS if k not in profile}
        if missing:
            _with_retry(col.update_one, {"_id": uid_str}, {"$set": missing})
            profile.update(missing)

    return profile


# ═══════════════════════════════════════════════════════════
# RESET
# ═══════════════════════════════════════════════════════════

def should_reset_quest(uid: int | str) -> bool:
    uid_str = str(uid)
    col     = _col()
    doc     = _with_retry(col.find_one, {"_id": uid_str}, {"last_reset": 1})
    if not doc:
        return True
    return (time.time() - doc.get("last_reset", 0)) >= 86400


def reset_quest(uid: int | str) -> list[str]:
    """Reset quest, gán 1–2 nhiệm vụ ngẫu nhiên. Trả list types."""
    uid_str      = str(uid)
    count        = random.randint(1, 2)
    chosen_types = random.sample(list(QUEST_TYPES.keys()), k=count)
    new_quests   = [{"type": t, "completed": False, "claimed": False} for t in chosen_types]

    _with_retry(
        _col().update_one,
        {"_id": uid_str},
        {"$set": {"last_reset": time.time(), "quests": new_quests, **_blank_progress()}},
        upsert=True,
    )
    return chosen_types


# ═══════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════

def add_quest_progress(uid: int | str, progress_key: str, amount: int = 1) -> list[str]:
    """
    Cộng progress. Tự đánh dấu completed nếu đạt target.
    Trả về list quest_type vừa hoàn thành (dùng để gửi thông báo).
    """
    uid_str = str(uid)
    col     = _col()

    profile = _with_retry(col.find_one, {"_id": uid_str})
    if not profile:
        return []

    new_val        = profile.get(progress_key, 0) + amount
    quests         = profile.get("quests", [])
    completed_now  = []

    for q in quests:
        if q.get("claimed") or q.get("completed"):
            continue
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata = QUEST_TYPES[qt]
        if qdata["progress_key"] != progress_key:
            continue
        if new_val >= qdata["target"]:
            q["completed"]  = True
            completed_now.append(qt)

    _with_retry(
        col.update_one,
        {"_id": uid_str},
        {"$set": {progress_key: new_val, "quests": quests}},
        upsert=True,
    )
    return completed_now


# ═══════════════════════════════════════════════════════════
# QUERY
# ═══════════════════════════════════════════════════════════

def get_current_quests(uid: int | str) -> list[dict]:
    """Trả về list thông tin quest hiện tại (đầy đủ)."""
    uid_str = str(uid)
    profile = _ensure_profile(uid_str)

    result = []
    for q in profile.get("quests", []):
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata    = QUEST_TYPES[qt]
        pkey     = qdata["progress_key"]
        progress = profile.get(pkey, 0)
        result.append({
            "type":        qt,
            "name":        qdata["name"],
            "description": qdata["description"].format(target=qdata["target"]),
            "target":      qdata["target"],
            "progress":    min(progress, qdata["target"]),
            "completed":   q.get("completed", False),
            "claimed":     q.get("claimed",   False),
            "reward":      qdata["reward"],
        })
    return result


def get_current_quest(uid: int | str) -> dict | None:
    """Backward-compat: trả về quest đầu tiên."""
    quests = get_current_quests(uid)
    return quests[0] if quests else None


# ═══════════════════════════════════════════════════════════
# CLAIM REWARD
# ═══════════════════════════════════════════════════════════

def claim_quest_reward(uid: int | str) -> tuple[bool, str, int]:
    """
    Claim TẤT CẢ quest completed & chưa claimed.
    Trả về (success, message, total_reward).
    """
    uid_str = str(uid)
    col     = _col()

    profile = _with_retry(col.find_one, {"_id": uid_str})
    if not profile:
        return False, "Không tìm thấy dữ liệu quest.", 0

    total     = 0
    names     = []
    any_found = False
    quests    = profile.get("quests", [])

    for q in quests:
        if q.get("completed") and not q.get("claimed"):
            qt = q.get("type")
            if qt in QUEST_TYPES:
                total      += QUEST_TYPES[qt]["reward"]
                names.append(QUEST_TYPES[qt]["name"])
                q["claimed"] = True
                any_found    = True

    if not any_found:
        return False, "Không có quest hoàn thành nào để nhận.", 0

    _with_retry(col.update_one, {"_id": uid_str}, {"$set": {"quests": quests}})
    joined = "**, **".join(names)
    return True, f"Nhận reward từ **{joined}**!", total
