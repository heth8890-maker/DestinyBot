"""
===== FILE: rpg_addon.py =====
Quản lý Weapon Shop (10 slot, reset mỗi SHOP_RESET_SEC giây)
+ Upgrade System (Lv 30, effect_value_at_level, passives, EFFECT_CAPS).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★  REFACTOR v3 – Weighted Spawn System (Logic Fix + Legend Split)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(giữ nguyên toàn bộ shop logic, thay thế upgrade helpers)

⚡ THAY ĐỔI v5.5-fix (Hybrid Stack/UID restore):
  - parse_effects_upgraded(): dùng get_base_id() từ rpg_core thay vì WeaponID.parse()
    → CẤM dùng .split("-") ngoài get_base_id() (rpg_core.py)
"""

import json
import os
import random
import time
import uuid as _uuid

from rpg_weapon import WEAPONS, RARE_CRATE_WEAPONS

# ═══════════════════════════════════════════════════════════════
# CONSTANTS — SHOP
# ═══════════════════════════════════════════════════════════════

SHOP_FILE      = "weapon_shop.json"
SHOP_RESET_SEC = 6 * 3600
SHOP_SLOTS     = 10

COIN_EMOJI = "<:Coin:1495831576397742241>"

_RARITY_SPAWN_WEIGHT: dict[str, float] = {
    "common":    10.00,
    "uncommon":   4.00,
    "rare":       1.50,
    "epic":       0.40,
    "legendary":  0.15,
    "legend":     0.09,
}

_MAX_COPIES_PER_WEAPON: dict[str, int] = {
    "common":    3,
    "uncommon":  2,
    "rare":      2,
    "epic":      1,
    "legendary": 1,
    "legend":    1,
}

_RARITY_SLOT_CAP: dict[str, int] = {
    "common":    4,
    "uncommon":  5,
    "rare":      4,
    "epic":      3,
}

_LEGENDARY_COMBINED_CAP: int       = 1
_LEGENDARY_TIERS:         frozenset = frozenset({"legendary", "legend"})


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — UPGRADE SYSTEM
# ═══════════════════════════════════════════════════════════════

UPGRADE_MAX_LEVEL: int = 30

RARITY_MULT: dict[str, float] = {
    "common":    0.10,  # Legend = chuẩn 1.0, common = 10%
    "uncommon":  0.20,
    "rare":      0.45,
    "epic":      0.80,
    "legendary": 1.70,
    "legend":    2.00,
}

EFFECT_CAPS: dict[str, float] = {
    "sell_bonus":      1.0,
    "reduce_cooldown": 0.8,
    "double_drop":     0.5,
}

_PASSIVE_UNLOCKS: dict[str, dict] = {
    "sell_bonus":      {"lv": 20, "passive": "x2_sell_chance",  "desc": "+5% cơ hội nhân đôi tiền bán"},
    "reduce_cooldown": {"lv": 20, "passive": "flat_cd_reduce",  "desc": "-1s cooldown cố định"},
}


# ═══════════════════════════════════════════════════════════════
# SHOP HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_shop_weight(w: dict) -> float:
    rarity = w.get("rarity", "common")
    return _RARITY_SPAWN_WEIGHT.get(rarity, 1.0)


def _build_slot(slot_num: int, w: dict) -> dict:
    rarity     = w.get("rarity", "common").lower()
    chance_val = w.get("chance", 5) / 100.0

    is_rare_source = any(rc["id"] == w["id"] for rc in RARE_CRATE_WEAPONS)
    crate_base     = 12000 if is_rare_source else 5000

    if not is_rare_source:
        multipliers = {
            "common": 0.65, "uncommon": 1.05, "rare": 1.50,
            "epic": 2.40, "legendary": 14.0, "legend": 14.0,
        }
    else:
        multipliers = {
            "uncommon": 1.50, "rare": 2.00, "epic": 2.60,
            "legendary": 20.0, "legend": 20.0,
        }

    r_mult   = multipliers.get(rarity, 1.0)
    drop_mod = min(1.6, 1.0 + (1.0 - chance_val) * 0.4)
    price    = max(500, int(crate_base * r_mult * drop_mod))

    return {
        "slot":       slot_num,
        "weapon_id":  w["id"],
        "name":       w["name"],
        "emoji":      w["emoji"],
        "rarity":     w["rarity"],
        "drop_rate":  w.get("chance", 5),
        "price":      price,
        "sold":       False,
    }


def generate_new_shop() -> dict:
    all_weapons      = WEAPONS + RARE_CRATE_WEAPONS
    copy_count:      dict[str, int] = {}
    tier_count:      dict[str, int] = {}
    legendary_total: int            = 0
    slots:           list[dict]     = []

    max_attempts = SHOP_SLOTS * 20
    attempt      = 0

    while len(slots) < SHOP_SLOTS and attempt < max_attempts:
        attempt += 1

        eligible: list[dict] = []
        for w in all_weapons:
            rid = w.get("rarity", "common")
            if copy_count.get(w["id"], 0) >= _MAX_COPIES_PER_WEAPON.get(rid, 1):
                continue
            if rid not in _LEGENDARY_TIERS:
                if tier_count.get(rid, 0) >= _RARITY_SLOT_CAP.get(rid, 10):
                    continue
            if rid in _LEGENDARY_TIERS and legendary_total >= _LEGENDARY_COMBINED_CAP:
                continue
            eligible.append(w)

        if not eligible:
            copy_count.clear()
            for w in all_weapons:
                rid = w.get("rarity", "common")
                if rid not in _LEGENDARY_TIERS:
                    if tier_count.get(rid, 0) >= _RARITY_SLOT_CAP.get(rid, 10):
                        continue
                if rid in _LEGENDARY_TIERS and legendary_total >= _LEGENDARY_COMBINED_CAP:
                    continue
                eligible.append(w)

        if not eligible:
            for w in sorted(all_weapons,
                            key=lambda x: tier_count.get(x.get("rarity"), 0)):
                rid = w.get("rarity", "common")
                if rid not in _LEGENDARY_TIERS:
                    eligible.append(w)
                    break
            if not eligible:
                eligible = [w for w in all_weapons
                            if w.get("rarity") not in _LEGENDARY_TIERS]

        if not eligible:
            break

        weights = [_get_shop_weight(w) for w in eligible]
        chosen  = random.choices(eligible, weights=weights, k=1)[0]
        rid     = chosen.get("rarity", "common")

        copy_count[chosen["id"]] = copy_count.get(chosen["id"], 0) + 1
        if rid in _LEGENDARY_TIERS:
            legendary_total += 1
        else:
            tier_count[rid] = tier_count.get(rid, 0) + 1

        slots.append(_build_slot(len(slots) + 1, chosen))

    return {"slots": slots, "generated_at": time.time()}


# ═══════════════════════════════════════════════════════════════
# SHOP I/O
# ═══════════════════════════════════════════════════════════════

def _load_raw() -> dict:
    if os.path.exists(SHOP_FILE):
        try:
            with open(SHOP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    return {}


def _save_raw(data: dict) -> None:
    with open(SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_weapon_shop() -> dict:
    data         = _load_raw()
    generated_at = data.get("generated_at", 0)
    if time.time() - generated_at >= SHOP_RESET_SEC or not data.get("slots"):
        data = generate_new_shop()
        _save_raw(data)
    return data


def seconds_to_shop_reset() -> int:
    data         = _load_raw()
    generated_at = data.get("generated_at", 0)
    elapsed      = time.time() - generated_at
    return max(0, int(SHOP_RESET_SEC - elapsed))


def get_shop_slot(slot: int) -> dict | None:
    shop = load_weapon_shop()
    for s in shop.get("slots", []):
        if s["slot"] == slot:
            return None if s.get("sold") else s
    return None


def mark_shop_slot_sold(slot: int) -> None:
    data = _load_raw()
    for s in data.get("slots", []):
        if s["slot"] == slot:
            s["sold"] = True
            break
    _save_raw(data)


# ═══════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ═══════════════════════════════════════════════════════════════

def fmt_effect_val(key: str, val) -> str:
    if key == "extra_slot":
        return f"+{int(val)} ô"
    if isinstance(val, float):
        return f"{key}: +{round(val * 100, 1)}%"
    return f"{key}: {val}"


# ═══════════════════════════════════════════════════════════════
# UPGRADE SYSTEM
# ═══════════════════════════════════════════════════════════════

def effect_value_at_level(base, lv: int, key: str = ""):
    """
    Tính giá trị của 1 effect tại level lv.
    Đây là công thức DUY NHẤT — không dùng công thức nào khác.

    Scaling tuyến tính:
        Value = base × (1 + (lv − 1) / 29)
        → Lv 1 = 1× base (không tăng)
        → Lv 30 = đúng 2× base (tăng 100%)

    Clamp lv vào [1, UPGRADE_MAX_LEVEL].
    Nếu key có trong EFFECT_CAPS → kết quả bị cap.
    """
    lv    = min(max(1, lv), UPGRADE_MAX_LEVEL)
    value = float(base) * (1.0 + (lv - 1) / 29.0)
    if key and key in EFFECT_CAPS:
        value = min(value, EFFECT_CAPS[key])
    return value


def upgrade_cost(base_max: int, current_lv: int,
                 rarity: str = "common",
                 effects: dict | None = None) -> int:
    """
    Chi phí nâng từ current_lv → current_lv+1.

    Nội suy tuyến tính từng đoạn (Legend làm chuẩn):
      Lv  1→10 :  3,000 → 16,000   (+1,444/lv)
      Lv 10→20 : 16,000 → 30,000   (+1,400/lv)
      Lv 20→30 : 30,000 → 120,000  (+9,000/lv)

    Các rarity khác nhân theo RARITY_MULT (legend = 1.0).
    base_max và effects được giữ lại trong signature để tương thích ngược,
    nhưng không ảnh hưởng đến kết quả tính toán.
    """
    lv = min(max(1, current_lv), UPGRADE_MAX_LEVEL - 1)

    if lv < 10:
        base_cost = 3000 + (lv - 1) * 1444
    elif lv < 20:
        base_cost = 16000 + (lv - 10) * 1400
    else:
        base_cost = 30000 + (lv - 20) * 9000

    r_mult = RARITY_MULT.get(rarity.lower(), 0.10)
    return max(100, int(base_cost * r_mult))


def create_upgrade_entry(user: dict, base_id: str) -> str | None:
    """
    Tạo upgraded_weapon entry mới từ base_id.

    1. Xác nhận weapon tồn tại.
    2. Sinh unique ID dạng "<base_id>-<6 hex>".
    3. Xóa base_id khỏi user["weapons"]  ← FIX duplication bug.
    4. Thêm uid vào user["weapons"].
    5. Append entry vào user["upgraded_weapons"].

    Trả về uid nếu thành công, None nếu không tìm thấy weapon data.
    """
    all_w = {w["id"]: w for w in WEAPONS + RARE_CRATE_WEAPONS}
    w     = all_w.get(base_id)
    if not w:
        return None

    uid           = f"{base_id}-{_uuid.uuid4().hex[:6].upper()}"
    effect_levels = {k: 1 for k in w.get("effects", {})}

    weapons = user.setdefault("weapons", [])
    if base_id in weapons:
        weapons.remove(base_id)

    entry = {
        "uid":           uid,
        "base_id":       base_id,
        "effect_levels": effect_levels,
        "passives":      {},
    }
    user.setdefault("upgraded_weapons", []).append(entry)
    weapons.append(uid)
    return uid


def _update_passives(uw: dict, w: dict) -> None:
    """Unlock passive vào uw["passives"] khi effect đạt lv 20."""
    for eff_key, info in _PASSIVE_UNLOCKS.items():
        lv = uw.get("effect_levels", {}).get(eff_key, 0)
        if lv >= info["lv"] and info["passive"] not in uw.get("passives", {}):
            uw.setdefault("passives", {})[info["passive"]] = info["desc"]


def get_upgraded_weapon(user: dict, uid: str) -> dict | None:
    """Tìm upgraded weapon trong user["upgraded_weapons"] theo uid."""
    for uw in user.get("upgraded_weapons", []):
        if uw.get("uid") == uid:
            return uw
    return None


def parse_effects_upgraded(equipped: list, user: dict) -> dict:
    """
    Tổng hợp effects từ danh sách equipped, áp dụng upgrade bonus.

    Với upgraded weapon → dùng effect_value_at_level(base, lv, key).
    Với base weapon → dùng giá trị gốc.

    Effect Stacking (Rule #1) — áp dụng cho STACKABLE_EFFECTS:
        final = max_contribution + (sum_others × 0.4)
    Các effect không thuộc STACKABLE_EFFECTS → cộng thẳng (hoặc take max).
    Sau cùng → apply EFFECT_CAPS lên totals.

    Backward-compat: user cũ không có upgraded_weapons → không crash.

    ⚡ v5.5-fix: Dùng get_base_id() — KHÔNG dùng str(wid).split("-")[0] trực tiếp.
    """
    from rpg_core import get_weapon_by_id, get_base_id

    # Effects áp dụng stacking diminishing returns (Rule #1)
    _STACKABLE: frozenset = frozenset({
        "sell_bonus", "rare_bias", "reduce_fail",
        "reduce_cooldown", "double_value",
    })

    _all_weapons: dict[str, dict] = {w["id"]: w for w in WEAPONS + RARE_CRATE_WEAPONS}
    uw_map: dict[str, dict]       = {
        uw["uid"]: uw for uw in user.get("upgraded_weapons", [])
    }

    # Per-key list of contributions (before stacking merge)
    raw_contribs: dict[str, list[float]] = {}
    non_stack: dict[str, float]          = {}

    for wid in equipped:
        if wid is None:
            continue

        # ── Use get_base_id() — KHÔNG dùng .split("-") trực tiếp ─────────────
        base_id = get_base_id(str(wid))
        if not base_id:
            continue

        w = _all_weapons.get(base_id) or get_weapon_by_id(base_id)
        if not w:
            continue

        effects: dict = dict(w.get("effects", {}))

        if wid in uw_map:
            uw         = uw_map[wid]
            eff_levels = uw.get("effect_levels", {})
            for eff_key in list(effects.keys()):
                if isinstance(effects[eff_key], (int, float)):
                    lv = min(max(1, eff_levels.get(eff_key, 1)), UPGRADE_MAX_LEVEL)
                    effects[eff_key] = effect_value_at_level(
                        effects[eff_key], lv, eff_key
                    )

        for k, v in effects.items():
            if not isinstance(v, (int, float)):
                # Non-numeric (e.g. string flags) → keep last seen
                non_stack[k] = v
                continue
            if k in _STACKABLE:
                raw_contribs.setdefault(k, []).append(float(v))
            else:
                # Non-stackable numeric (e.g. extra_slot) → additive
                non_stack[k] = non_stack.get(k, 0.0) + v

    # Apply stacking formula: max + sum_others × 0.4
    totals: dict[str, float] = dict(non_stack)
    for key, contribs in raw_contribs.items():
        if len(contribs) == 1:
            totals[key] = contribs[0]
        else:
            contribs_sorted = sorted(contribs, reverse=True)
            totals[key] = contribs_sorted[0] + sum(contribs_sorted[1:]) * 0.4

    # Global caps
    for key, cap in EFFECT_CAPS.items():
        if key in totals:
            totals[key] = min(totals[key], cap)

    return totals
