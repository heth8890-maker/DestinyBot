"""
===== FILE: rpg_instance.py =====

Weapon Instance Layer — quản lý quality, durability, passive cho từng instance.

DEPENDENCY (một chiều, không circular):
    rpg_instance.py → rpg_weapon.py (get_weapon_by_id, RARITY_LABEL)

KHÔNG import từ: rpg_core, rpg_hunt, rpg_forge, rpg_database, rpg_addon.

Exports:
    QUALITY_TIERS, QUALITY_WEIGHTS, DURABILITY_BY_RARITY
    PASSIVE_POOL, PASSIVE_INDEX, PASSIVE_TIER_WEIGHTS
    roll_quality, roll_passive, resolve_passive
    build_weapon_effects
    migrate_weapon_instance_fields
    decrease_durability
"""

import random
import logging

from rpg_weapon import get_weapon_by_id, RARITY_LABEL

logger = logging.getLogger(__name__)

__all__ = [
    "QUALITY_TIERS", "QUALITY_WEIGHTS", "DURABILITY_BY_RARITY",
    "PASSIVE_POOL", "PASSIVE_INDEX", "PASSIVE_TIER_WEIGHTS",
    "roll_quality", "roll_passive", "resolve_passive",
    "build_weapon_effects",
    "migrate_weapon_instance_fields",
    "decrease_durability",
]


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

QUALITY_TIERS = {
    "very_low":    {"label": "Rất Thấp",   "multiplier": 0.55, "color": 0x808080},
    "low":         {"label": "Thấp",       "multiplier": 0.75, "color": 0xA0A0A0},
    "medium_low":  {"label": "Khá Thấp",   "multiplier": 0.90, "color": 0xC0C0C0},
    "medium":      {"label": "Trung Bình", "multiplier": 1.00, "color": 0xFFFFFF},
    "medium_high": {"label": "Khá Cao",    "multiplier": 1.12, "color": 0x90EE90},
    "high":        {"label": "Cao",        "multiplier": 1.25, "color": 0x00BFFF},
    "very_high":   {"label": "Rất Cao",    "multiplier": 1.45, "color": 0xFF8C00},
    "extreme":     {"label": "Cực Cao",    "multiplier": 1.70, "color": 0xFF0000},
}

# Phân phối hình chuông lệch phải — medium là đỉnh
# very_low (0.2%) hiếm hơn extreme (0.3%) — CỐ Ý
QUALITY_WEIGHTS = {
    "very_low":    0.2,
    "low":         5.0,
    "medium_low":  18.0,
    "medium":      35.0,
    "medium_high": 25.0,
    "high":        12.0,
    "very_high":   4.5,
    "extreme":     0.3,
}

DURABILITY_BY_RARITY = {
    "common":    30,
    "uncommon":  50,
    "rare":      80,
    "epic":      120,
    "legendary": 150,
    "special":   200,
    "soul":      200,
}

# Tỉ lệ chọn passive tier theo weapon rarity
PASSIVE_TIER_WEIGHTS = {
    "common":    {"common": 70,  "uncommon": 25, "rare": 4,  "epic": 0.8, "legendary": 0.2},
    "uncommon":  {"common": 50,  "uncommon": 35, "rare": 10, "epic": 4,   "legendary": 1},
    "rare":      {"common": 30,  "uncommon": 35, "rare": 25, "epic": 8,   "legendary": 2},
    "epic":      {"common": 15,  "uncommon": 25, "rare": 35, "epic": 20,  "legendary": 5},
    "legendary": {"common": 5,   "uncommon": 15, "rare": 30, "epic": 35,  "legendary": 15},
    "special":   {"common": 3,   "uncommon": 10, "rare": 25, "epic": 37,  "legendary": 25},
    "soul":      {"common": 2,   "uncommon": 8,  "rare": 20, "epic": 35,  "legendary": 35},
}

# Pool 21 passive — gắn vào weapon instance, KHÔNG phải weapon trang bị được
# Giá trị âm là CỐ Ý trade-off design — không sửa, không abs()
PASSIVE_POOL = [
    {"id": "5234", "name": "Bánh Xe Tai Ương",               "emoji": "<:5234:1503397777579708547>", "rarity": "legendary", "effects": {"rare_bias": 0.01,  "luck_up": 0.03}},
    {"id": "5233", "name": "Cổ Nha",                         "emoji": "<:5233:1503397779589042326>", "rarity": "legendary", "effects": {"reduce_fail": 0.05, "sell_bonus": 0.05}},
    {"id": "5232", "name": "Ảnh Trảm",                       "emoji": "<:5232:1503397781325217933>", "rarity": "uncommon",  "effects": {"reduce_cooldown": 0.08}},
    {"id": "5231", "name": "Kẻ Dối Trá",                     "emoji": "<:5231:1503397783699456140>", "rarity": "rare",      "effects": {"extra_slot": 1,    "sell_bonus": 0.02}},
    {"id": "5230", "name": "Sự Hối Lỗi",                     "emoji": "<:5230:1503397785964249148>", "rarity": "rare",      "effects": {"reduce_fail": 0.04}},
    {"id": "5229", "name": "Nắm Chặt",                       "emoji": "<:5229:1503397788107673710>", "rarity": "rare",      "effects": {"reduce_fail": 0.10, "reduce_cooldown": -0.01}},
    {"id": "5228", "name": "Lá Vàng",                        "emoji": "<:5228:1503397789852504155>", "rarity": "rare",      "effects": {"sell_bonus": 0.06,  "luck_up": 0.01}},
    {"id": "5227", "name": "Trói Buộc",                      "emoji": "<:5227:1503397792062898237>", "rarity": "epic",      "effects": {"reduce_cooldown": -0.03, "rare_bias": 0.03, "sell_bonus": 0.01}},
    {"id": "5226", "name": "Búa Vỡ",                         "emoji": "<:5226:1503397793421594907>", "rarity": "uncommon",  "effects": {"sell_bonus": 0.03,  "reduce_fail": 0.02}},
    {"id": "5225", "name": "Kẻ Ngốc",                        "emoji": "<:5225:1503397796684894380>", "rarity": "legendary", "effects": {"sell_bonus": 0.10,  "reduce_fail": 0.03}},
    {"id": "5224", "name": "Nhật Kí Của Oneiroi",             "emoji": "<:5224:1503397799406997585>", "rarity": "epic",      "effects": {"passive_oneiroi": 0.02}},
    {"id": "5223", "name": "Khiêu Chiến",                    "emoji": "<:5223:1503397801588162591>", "rarity": "uncommon",  "effects": {"reduce_fail": 0.03}},
    {"id": "5222", "name": "Lòng Tham Và Sự Dối Trá",         "emoji": "<:5222:1503397811801034905>", "rarity": "epic",      "effects": {"reduce_fail": 0.03, "luck_up": 0.02, "reduce_cooldown": 0.01}},
    {"id": "5221", "name": "Lôi Đỏ",                         "emoji": "<:5221:1503397814930116608>", "rarity": "rare",      "effects": {"sell_bonus": 0.04}},
    {"id": "5220", "name": "Dao Găm Của Lựa Chọn Cuối Cùng",  "emoji": "<:5220:1503397819262963893>", "rarity": "epic",      "effects": {"luck_up": 0.05}},
    {"id": "5219", "name": "Bảo Thủ",                        "emoji": "<:5219:1503397821888335902>", "rarity": "uncommon",  "effects": {"luck_up": -0.02,   "reduce_cooldown": 0.03}},
    {"id": "5218", "name": "Hoả Lâu",                        "emoji": "<:5218:1503397824098996284>", "rarity": "epic",      "effects": {"sell_bonus": 0.01,  "luck_up": 0.01,  "double_drop": 0.01}},
    {"id": "5217", "name": "Mưa Tên",                        "emoji": "<:5217:1503397826150010961>", "rarity": "rare",      "effects": {"reduce_fail": 0.02}},
    {"id": "5216", "name": "Tín Đồ",                         "emoji": "<:5216:1503397828238774362>", "rarity": "rare",      "effects": {"luck_up": 0.02,    "rare_bias": 0.01}},
    {"id": "5212", "name": "Tham Lam",                       "emoji": "<:5212:1503397837449330698>", "rarity": "epic",      "effects": {"sell_bonus": -0.06, "rare_bias": 0.04}},
    {"id": "5210", "name": "Sự Cứu Rỗi",                     "emoji": "<:5210:1503397842180509878>", "rarity": "uncommon",  "effects": {"sell_bonus": 0.03}},
]

# O(1) lookup theo id
PASSIVE_INDEX: dict[str, dict] = {p["id"]: p for p in PASSIVE_POOL}


# ══════════════════════════════════════════════════════════════════════════════
#  ROLL FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def roll_quality(rarity: str = "common") -> str:
    """
    Roll quality tier cho weapon instance mới.
    Weapon rarity cao → cộng bonus weight vào các tier tốt.

    Rarity bonus (cộng vào medium_high, high, very_high, extreme):
        common:0  uncommon:+1  rare:+3  epic:+6  legendary:+10  special/soul:+15
    """
    _bonus_map = {
        "common": 0, "uncommon": 1, "rare": 3,
        "epic": 6,   "legendary": 10, "special": 15, "soul": 15,
    }
    bonus   = _bonus_map.get(rarity, 0)
    weights = dict(QUALITY_WEIGHTS)
    for tier in ("medium_high", "high", "very_high", "extreme"):
        weights[tier] = weights.get(tier, 0) + bonus
    tiers = list(weights.keys())
    w     = list(weights.values())
    return random.choices(tiers, weights=w, k=1)[0]


def roll_passive(weapon_rarity: str = "common", quality: str = "medium") -> dict:
    """
    Roll 1 passive cho weapon instance mới.
    Lưu compact {"id", "roll"} — resolve full data tại runtime qua resolve_passive().

    Quality ảnh hưởng nhỏ lên roll multiplier.
    Roll lẻ trong [0.88, 1.12] × quality_bonus → giá trị độc nhất mỗi instance.

    Returns: {"id": "5228", "roll": 1.0573}
    """
    tier_weights = PASSIVE_TIER_WEIGHTS.get(weapon_rarity, PASSIVE_TIER_WEIGHTS["common"])
    tiers   = list(tier_weights.keys())
    w       = list(tier_weights.values())
    tier    = random.choices(tiers, weights=w, k=1)[0]

    pool = [p for p in PASSIVE_POOL if p["rarity"] == tier]
    if not pool:
        pool = [p for p in PASSIVE_POOL if p["rarity"] == "uncommon"] or PASSIVE_POOL

    chosen    = random.choice(pool)
    q_multi   = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
    base_roll = random.uniform(0.88, 1.12)
    roll      = round(base_roll * (1 + q_multi * 0.05), 6)

    return {"id": chosen["id"], "roll": roll}


def resolve_passive(passive_stored: dict) -> dict | None:
    """
    Resolve passive {"id", "roll"} → full display data tại runtime.
    Nhân roll vào numeric effects để tạo giá trị thực của instance này.
    Trả về None nếu passive_stored không hợp lệ hoặc id không tìm thấy.
    """
    if not isinstance(passive_stored, dict):
        return None
    pid  = str(passive_stored.get("id", ""))
    roll = float(passive_stored.get("roll", 1.0))
    base = PASSIVE_INDEX.get(pid)
    if not base:
        return None

    resolved_effects: dict = {}
    for k, v in base["effects"].items():
        if isinstance(v, (int, float)) and k != "extra_slot":
            resolved_effects[k] = round(v * roll, 6)
        else:
            resolved_effects[k] = v

    return {
        "id":      base["id"],
        "name":    base["name"],
        "emoji":   base["emoji"],
        "rarity":  base["rarity"],
        "effects": resolved_effects,
        "roll":    roll,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLE SOURCE OF TRUTH — EFFECT SCALING
# ══════════════════════════════════════════════════════════════════════════════

def build_weapon_effects(base_effects: dict, wi: dict | None = None) -> dict:
    """
    SINGLE SOURCE OF TRUTH cho weapon effect scaling.
    Dùng cho cả parse_effects() (gameplay) lẫn fmt_stats() (UI display).
    Đảm bảo UI và gameplay không bao giờ lệch nhau.

    Pipeline:
        1. Scale theo weapon level   (0.60 + (lv-1) * 0.02857)
        2. Nhân quality multiplier
        3. Cộng passive effects (resolved từ roll)

    Args:
        base_effects: dict effects từ WEAPON_EFFECTS (chưa scale)
        wi:           weapon_instance dict hoặc None

    Returns:
        dict effects đã scale đầy đủ — sẵn sàng để accumulate vào agg
    """
    if not isinstance(base_effects, dict):
        return {}

    effects = dict(base_effects)

    # --- Step 1: level scale ---
    level    = max(1, min(50, wi.get("level", 1))) if wi else 1
    lv_scale = 0.60 + (level - 1) * 0.02857
    for k in list(effects.keys()):
        if isinstance(effects[k], (int, float)) and k != "extra_slot":
            effects[k] = effects[k] * lv_scale

    # --- Step 2: quality multiplier ---
    quality = wi.get("quality", "medium") if wi else "medium"
    q_multi = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
    for k in list(effects.keys()):
        if isinstance(effects[k], (int, float)) and k != "extra_slot":
            effects[k] = effects[k] * q_multi

    # --- Step 3: cộng passive effects ---
    if wi:
        passive_stored = wi.get("passive")
        if passive_stored:
            resolved = resolve_passive(passive_stored)
            if resolved:
                for k, v in resolved["effects"].items():
                    if isinstance(v, (int, float)):
                        effects[k] = effects.get(k, 0) + v
                    elif k not in effects:
                        effects[k] = v

    return effects


# ══════════════════════════════════════════════════════════════════════════════
#  MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

def migrate_weapon_instance_fields(user: dict) -> bool:
    """
    Back-fill quality, durability, passive, broken cho instance cũ còn thiếu.
    Dọn orphan instances — uid không còn tồn tại trong bag hoặc equipped.

    Salvage-First: setdefault only — KHÔNG ghi đè field đã có.
    Gọi trong get_user() sau migrate_all_weapons_to_uid().

    Returns: True nếu có thay đổi (để caller biết cần save).
    """
    changed = False

    # --- Dọn orphan instances ---
    valid_uids: set[str] = set(user.get("weapons", [])) | {
        e for e in user.get("equipped", []) if isinstance(e, str)
    }
    before = len(user.get("weapon_instances", []))
    user["weapon_instances"] = [
        wi for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict)
        and "uid"     in wi
        and "base_id" in wi
        and wi["uid"] in valid_uids
    ]
    if len(user["weapon_instances"]) != before:
        logger.info(
            f"migrate_weapon_instance_fields: "
            f"removed {before - len(user['weapon_instances'])} orphan instances"
        )
        changed = True

    # --- Back-fill fields mới ---
    for wi in user["weapon_instances"]:
        if not isinstance(wi, dict):
            continue
        # FIX: phải kiểm tra đủ 5 field VÀ durability > 0
        # Nếu durability == 0 mà durability_max chưa có → vẫn cần back-fill
        if (
            "quality"        in wi
            and "durability"     in wi and wi.get("durability", 0) > 0
            and "durability_max" in wi
            and "passive"        in wi
            and "broken"         in wi
        ):
            continue  # đã đầy đủ, bỏ qua

        try:
            w_data  = get_weapon_by_id(wi.get("base_id", "")) or {}
            rarity  = w_data.get("rarity", "common")
            quality = roll_quality(rarity)
            q_multi = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
            dur_max = int(DURABILITY_BY_RARITY.get(rarity, 30) * q_multi)
            passive = roll_passive(rarity, quality)
        except Exception as e:
            logger.warning(f"migrate_weapon_instance_fields: fallback for uid={wi.get('uid')}: {e}")
            quality = "medium"
            dur_max = 30
            passive = {}

        wi.setdefault("quality",        quality)
        wi.setdefault("durability",     dur_max)
        wi.setdefault("durability_max", dur_max)
        wi.setdefault("passive",        passive)
        wi.setdefault("broken",         False)
        changed = True

    return changed


# ══════════════════════════════════════════════════════════════════════════════
#  DURABILITY
# ══════════════════════════════════════════════════════════════════════════════

def decrease_durability(user: dict, equipped: list) -> list[str]:
    """
    Trừ 1 durability cho mỗi weapon equipped đang không broken.
    Set broken=True khi durability về 0.
    Mutates user["weapon_instances"] in-place.

    Args:
        user:     user dict từ get_user()
        equipped: list uid đang trang bị (user["equipped"])

    Returns:
        list uid vừa bị hỏng trong lần hunt này — để hiển thị cảnh báo cho người chơi.
    """
    wi_map: dict[str, dict] = {
        wi["uid"]: wi
        for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    just_broken: list[str] = []

    for wid in equipped:
        if not wid or not isinstance(wid, str) or "-" not in wid:
            continue
        wi = wi_map.get(wid)
        if not wi or wi.get("broken", False):
            continue
        wi["durability"] = max(0, wi.get("durability", 1) - 1)
        if wi["durability"] == 0:
            wi["broken"] = True
            just_broken.append(wid)
            logger.info(f"decrease_durability: weapon '{wid}' broken")

    return just_broken


# ══════════════════════════════════════════════════════════════════════════════
#  DISPLAY HELPER — dùng trong WeaponEntity.fmt_stats()
# ══════════════════════════════════════════════════════════════════════════════

def fmt_instance_info(wi: dict) -> str:
    """
    Build display string cho quality + durability + passive của 1 instance.
    Dùng trong WeaponEntity.fmt_stats() để tránh duplicate display logic.

    Returns: multiline string sẵn sàng append vào embed field.
    """
    if not isinstance(wi, dict):
        return ""

    quality = wi.get("quality", "medium")
    q_label = QUALITY_TIERS.get(quality, {}).get("label", quality)
    dur_max = wi.get("durability_max", 1)
    dur     = wi.get("durability", dur_max)  # FIX: default = full durability, không phải 0
    broken  = wi.get("broken", False)
    fill    = int(dur / max(dur_max, 1) * 10)
    dur_bar = "█" * fill + "░" * (10 - fill)

    lines = [
        "",
        f"Phẩm chất: **{q_label}**",
    ]
    if broken:
        lines.append("Độ bền: ⚠️ **HỎng** — cần sửa chữa")
    else:
        lines.append(f"Độ bền: `{dur_bar}` {dur}/{dur_max}")

    passive_stored = wi.get("passive")
    if passive_stored:
        resolved = resolve_passive(passive_stored)
        if resolved:
            p_label = RARITY_LABEL.get(resolved["rarity"], resolved["rarity"])
            lines.append(f"Nội tại: {resolved['emoji']} **{resolved['name']}** _{p_label}_")
            for k, v in resolved["effects"].items():
                if k == "extra_slot":
                    lines.append(f"  └ `{k}`: **+{int(v)} ô**")
                elif isinstance(v, float):
                    lines.append(f"  └ `{k}`: **{v:+.1%}**")
                else:
                    lines.append(f"  └ `{k}`: **{v:+}**")

    return "\n".join(lines)
