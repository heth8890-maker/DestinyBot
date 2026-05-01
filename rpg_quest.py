"""
===== FILE: rpg_quest.py =====
Hệ thống Daily Quest: mỗi người nhận 1–2 nhiệm vụ ngẫu nhiên,
reset mỗi 24h. Backward-compat với format cũ (1 quest).

Public API:
  should_reset_quest / reset_quest
  add_quest_progress
  get_current_quests / get_current_quest  (compat)
  claim_quest_reward
  QUEST_TYPES / QUEST_PROGRESS_KEYS
"""

import json
import os
import random
import time

QUEST_FILE = "rpg_quest.json"

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

# Backward compat key
QUEST_PROGRESS_KEYS = {k: v["progress_key"] for k, v in QUEST_TYPES.items()}

ALL_PROGRESS_KEYS = list({v["progress_key"] for v in QUEST_TYPES.values()})


# ═══════════════════════════════════════════════════════════
# I/O
# ═══════════════════════════════════════════════════════════

def _load() -> dict:
    if not os.path.exists(QUEST_FILE):
        return {}
    with open(QUEST_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(data: dict) -> None:
    with open(QUEST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════
# USER PROFILE  (+ migration từ format cũ)
# ═══════════════════════════════════════════════════════════

def _blank_progress() -> dict:
    return {k: 0 for k in ALL_PROGRESS_KEYS}


def _new_user_profile() -> dict:
    profile = {
        "last_reset": 0,
        "quests":     [],   # list[{type, completed, claimed}]
    }
    profile.update(_blank_progress())
    return profile


def _migrate_old_format(profile: dict) -> dict:
    """Chuyển format cũ (current_quest_type / completed) sang format mới (quests list)."""
    old_type = profile.get("current_quest_type")
    if old_type and old_type in QUEST_TYPES:
        completed = profile.get("completed", False)
        profile["quests"] = [{"type": old_type, "completed": completed, "claimed": False}]
    else:
        profile["quests"] = []

    for k in ["current_quest_type", "completed"]:
        profile.pop(k, None)

    # Đảm bảo tất cả progress keys tồn tại
    for k in ALL_PROGRESS_KEYS:
        profile.setdefault(k, 0)

    return profile


def _ensure_profile(uid: str, data: dict) -> dict:
    if uid not in data:
        data[uid] = _new_user_profile()
        _save(data)
    else:
        p = data[uid]
        # Migrate old single-quest format
        if "current_quest_type" in p or "quests" not in p:
            data[uid] = _migrate_old_format(p)
            _save(data)
        # Ensure progress counters exist
        for k in ALL_PROGRESS_KEYS:
            data[uid].setdefault(k, 0)
    return data[uid]


# ═══════════════════════════════════════════════════════════
# RESET
# ═══════════════════════════════════════════════════════════

def should_reset_quest(uid: int | str) -> bool:
    data = _load()
    uid  = str(uid)
    if uid not in data:
        return True
    profile = _ensure_profile(uid, data)
    return (time.time() - profile.get("last_reset", 0)) >= 86400


def reset_quest(uid: int | str) -> list[str]:
    """Reset quest, gán 1–2 nhiệm vụ ngẫu nhiên (không trùng type). Trả list types."""
    uid  = str(uid)
    data = _load()
    _ensure_profile(uid, data)

    count         = random.randint(1, 2)
    chosen_types  = random.sample(list(QUEST_TYPES.keys()), k=count)
    new_quests    = [{"type": t, "completed": False, "claimed": False} for t in chosen_types]

    data[uid]["last_reset"] = time.time()
    data[uid]["quests"]     = new_quests
    # Reset tất cả progress counters
    for k in ALL_PROGRESS_KEYS:
        data[uid][k] = 0

    _save(data)
    return chosen_types


# ═══════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════

def add_quest_progress(uid: int | str, progress_key: str, amount: int = 1) -> list[str]:
    """
    Cộng progress. Tự đánh dấu completed nếu đạt target.
    Trả về list quest_type vừa hoàn thành.
    """
    uid  = str(uid)
    data = _load()
    if uid not in data:
        return []

    _ensure_profile(uid, data)
    profile = data[uid]

    # Cộng counter
    profile[progress_key] = profile.get(progress_key, 0) + amount

    completed_now = []
    for q in profile.get("quests", []):
        if q.get("claimed") or q.get("completed"):
            continue
        qt   = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata = QUEST_TYPES[qt]
        if qdata["progress_key"] != progress_key:
            continue
        if profile[progress_key] >= qdata["target"]:
            q["completed"] = True
            completed_now.append(qt)

    _save(data)
    return completed_now


# ═══════════════════════════════════════════════════════════
# QUERY
# ═══════════════════════════════════════════════════════════

def get_current_quests(uid: int | str) -> list[dict]:
    """Trả về list thông tin quest hiện tại (đầy đủ)."""
    uid  = str(uid)
    data = _load()
    _ensure_profile(uid, data)
    profile = data[uid]

    result = []
    for q in profile.get("quests", []):
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata   = QUEST_TYPES[qt]
        pkey    = qdata["progress_key"]
        progress = profile.get(pkey, 0)
        result.append({
            "type":        qt,
            "name":        qdata["name"],
            "description": qdata["description"].format(target=qdata["target"]),
            "target":      qdata["target"],
            "progress":    min(progress, qdata["target"]),
            "completed":   q.get("completed", False),
            "claimed":     q.get("claimed", False),
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
    uid  = str(uid)
    data = _load()
    _ensure_profile(uid, data)
    profile = data[uid]

    total     = 0
    names     = []
    any_found = False

    for q in profile.get("quests", []):
        if q.get("completed") and not q.get("claimed"):
            qt = q.get("type")
            if qt in QUEST_TYPES:
                reward = QUEST_TYPES[qt]["reward"]
                total += reward
                names.append(QUEST_TYPES[qt]["name"])
                q["claimed"] = True
                any_found   = True

    if not any_found:
        return False, "Không có quest hoàn thành nào để nhận.", 0

    _save(data)
    joined = "**, **".join(names)
    return True, f"Nhận reward từ **{joined}**!", total
