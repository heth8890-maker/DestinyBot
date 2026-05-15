"""
rpg_effect.py — Effect System
Tách từ rpg_core.py. Chứa toàn bộ logic parse/aggregate weapon effects,
hunt roll engine, và các calculator dùng effects.

Import vào rpg_core.py:
    from rpg_effect import (
        parse_effects,
        roll_hunt_items,
        calc_hunt_cooldown,
        calc_sell_value,
    )

handle_egg() được giữ lại trong rpg_core.py (tránh circular import với add_item).
"""

import logging
import random

from rpg_item import (
    BASE_RARITY_RATES,
    _pick_item_from_rarity,
)
from rpg_instance import build_weapon_effects

logger = logging.getLogger(__name__)

__all__ = [
    "parse_effects",
    "roll_hunt_items",
    "calc_hunt_cooldown",
    "calc_sell_value",
]


# ══════════════════════════════════════════════════════════════════════════════
#  SAFE PARSE EFFECTS
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
    # Import ở đây để tránh circular import (rpg_core ↔ rpg_effect)
    from rpg_core import get_base_id, get_weapon_by_id

    if not isinstance(equipped, list):
        logger.warning(
            f"parse_effects: equipped is {type(equipped).__name__} instead of list → return {{}}"
        )
        return {}

    agg: dict = {}    # dynamic — không hardcode keys

    # Build O(1) upgrade map once — avoids repeated list scans inside the loop
    wi_map: dict[str, dict] = {}
    if user is not None:
        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

    for wid in equipped:
        try:
            if wid is None:
                continue
            if not isinstance(wid, str) or not wid:
                logger.warning(f"parse_effects: invalid weapon_id {wid!r} — skipping")
                continue

            base_id = get_base_id(wid)
            if not base_id:
                logger.warning(f"parse_effects: empty base_id from '{wid}' — skipping")
                continue

            base_data = get_weapon_by_id(base_id)
            if not base_data:
                logger.warning(f"parse_effects: no weapon data for '{base_id}' — skipping")
                continue

            wi = wi_map.get(wid) if user is not None else None

            # Weapon hỏng → bỏ qua hoàn toàn
            if wi and wi.get("broken", False):
                continue

            # Single source of truth — dùng build_weapon_effects từ rpg_instance
            effects = build_weapon_effects(base_data.get("effects", {}), wi)

            # Dynamic accumulate — tương thích passive key mới sau này
            for key, val in effects.items():
                if not isinstance(val, (int, float)):
                    continue
                try:
                    agg[key] = agg.get(key, 0) + val
                except (TypeError, ValueError) as add_err:
                    logger.warning(
                        f"parse_effects: cannot add '{key}'={val!r} from '{wid}': {add_err}"
                    )

        except Exception as e:
            logger.error(f"parse_effects: unexpected error for weapon_id={wid!r}: {e}")
            continue

    return agg


# ══════════════════════════════════════════════════════════════════════════════
#  HUNT ROLL ENGINE
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
        legendary_gain = extra * 0.12

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
#  CALCULATORS
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
