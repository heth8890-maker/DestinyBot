"""
===== FILE: rpg_addon.py =====
Quản lý Weapon Shop (slot, reset mỗi SHOP_RESET_SEC giây)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★  REFACTOR v3 – Weighted Spawn System (Logic Fix + Legend Split)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(giữ nguyên toàn bộ shop logic, thay thế upgrade helpers)

⚡ THAY ĐỔI v5.5-fix (Hybrid Stack/UID restore):
  - parse_effects_upgraded(): dùng get_base_id() từ rpg_core thay vì WeaponID.parse()
    → CẤM dùng .split("-") ngoài get_base_id() (rpg_core.py)

⚡ THAY ĐỔI v6 (MongoDB shop storage):
  - _load_raw() / _save_raw() dùng database_helper thay vì weapon_shop.json
  - Bỏ SHOP_FILE, import os, import json (không cần nữa)
"""

import random
import time

from rpg_weapon_data import WEAPONS, RARE_CRATE_WEAPONS
from database_helper import load_shop_data, save_shop_data

# ═══════════════════════════════════════════════════════════════
# CONSTANTS — SHOP
# ═══════════════════════════════════════════════════════════════

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

EFFECT_CAPS: dict[str, float] = {
    "sell_bonus":      3.0,
    "reduce_cooldown": 0.7,
    "double_drop":     0.5,
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
# SHOP I/O  — MongoDB backend (thay thế JSON file)
# ═══════════════════════════════════════════════════════════════

def _load_raw() -> dict:
    """Đọc shop data từ MongoDB. Trả về {} nếu chưa có."""
    return load_shop_data()


def _save_raw(data: dict) -> None:
    """Ghi shop data lên MongoDB."""
    save_shop_data(data)


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
# PARSE EFFECTS
# ═══════════════════════════════════════════════════════════════

def parse_effects_upgraded(equipped: list, user: dict) -> dict:
    """
    Tổng hợp effects từ equipped weapons, scale theo weapon level.
    Level 1 = 0.60x base, mỗi level +2.857%, Level 50 = 2.00x base.
    Formula: effect = base * (0.60 + (level - 1) * 0.02857)
    """
    from rpg_core import get_weapon_by_id, get_base_id
    from rpg_weapon import WEAPONS, RARE_CRATE_WEAPONS

    _STACKABLE = frozenset({
        "sell_bonus", "rare_bias", "reduce_fail",
        "reduce_cooldown", "double_value", "event_hunt", "treasure_hunt"
    })

    _all_weapons = {w["id"]: w for w in WEAPONS + RARE_CRATE_WEAPONS}
    wi_map = {
        wi["uid"]: wi
        for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    raw_contribs: dict[str, list[float]] = {}
    non_stack: dict[str, float] = {}

    for wid in equipped:
        if wid is None:
            continue
        base_id = get_base_id(str(wid))
        if not base_id:
            continue
        w = _all_weapons.get(base_id) or get_weapon_by_id(base_id)
        if not w:
            continue

        effects = dict(w.get("effects", {}))

        if wid in wi_map:
            level = max(1, min(50, wi_map[wid].get("level", 1)))
            for k in list(effects.keys()):
                if isinstance(effects[k], (int, float)):
                    effects[k] = effects[k] * (0.60 + (level - 1) * 0.02857)
        else:
            # Base weapon chưa có instance → scale 0.60x
            for k in list(effects.keys()):
                if isinstance(effects[k], (int, float)):
                    effects[k] = effects[k] * 0.60

        for k, v in effects.items():
            if not isinstance(v, (int, float)):
                non_stack[k] = v
                continue
            if k in _STACKABLE:
                raw_contribs.setdefault(k, []).append(float(v))
            else:
                non_stack[k] = non_stack.get(k, 0.0) + v

    totals = dict(non_stack)
    for key, contribs in raw_contribs.items():
        if len(contribs) == 1:
            totals[key] = contribs[0]
        else:
            cs = sorted(contribs, reverse=True)
            totals[key] = cs[0] + sum(cs[1:]) * 0.4

    for key, cap in EFFECT_CAPS.items():
        if key in totals:
            totals[key] = min(totals[key], cap)

    return totals
