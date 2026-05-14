
import random
import logging

from rpg_weapon_data import get_weapon_by_id, RARITY_LABEL

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
    "legend":    150,
    "mythical":  200,
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
    {"id": "5234", "name": "Bánh Xe Tai Ương", "emoji": "<:5234:1503397777579708547>", "rarity": "legendary", 
     "desc": "Vòng xoay không vận hành bằng may mắn, nó nghiền nát linh hồn để đổi lấy thiên cơ.", 
     "effects": {"rare_bias": 0.01,  "luck_up": 0.03}},

    {"id": "5233", "name": "Cổ Nha", "emoji": "<:5233:1503397779589042326>", "rarity": "legendary", 
     "desc": "Nanh vuốt che chắn cho kẻ kế thừa trước những bước chân lầm lạc.", 
     "effects": {"reduce_fail": 0.05, "sell_bonus": 0.05}},

    {"id": "5232", "name": "Ảnh Trảm", "emoji": "<:5232:1503397781325217933>", "rarity": "uncommon", 
     "desc": "Nhát chém cắt đứt sợi dây của thời gian, để lại thực tại một vết mờ hư ảo.", 
     "effects": {"reduce_cooldown": 0.08}},

    {"id": "5231", "name": "Kẻ Dối Trá", "emoji": "<:5231:1503397783699456140>", "rarity": "rare", 
     "desc": "Nụ cười che giấu quân bài rác; trong thế giới này, sự chân thật là một sai lầm.", 
     "effects": {"extra_slot": 1,    "sell_bonus": 0.02}},

    {"id": "5230", "name": "Sự Hối Lỗi", "emoji": "<:5230:1503397785964249148>", "rarity": "rare", 
     "desc": "Lời cầu nguyện muộn màng trước giá treo cổ đôi khi khiến thần chết mủi lòng.", 
     "effects": {"reduce_fail": 0.04}},

    {"id": "5229", "name": "Nắm Chặt", "emoji": "<:5229:1503397788107673710>", "rarity": "rare", 
     "desc": "Ghì chặt định mệnh trong lòng bàn tay, dù đôi chân phải quỵ ngã vì sức nặng.", 
     "effects": {"reduce_fail": 0.10, "reduce_cooldown": -0.01}},

    {"id": "5228", "name": "Lá Vàng", "emoji": "<:5228:1503397789852504155>", "rarity": "rare", 
     "desc": "Mảnh vụn từ vương miện của một vị vua mất nước; hào nhoáng nhưng đầy phù du.", 
     "effects": {"sell_bonus": 0.06,  "luck_up": 0.01}},

    {"id": "5227", "name": "Trói Buộc", "emoji": "<:5227:1503397792062898237>", "rarity": "epic", 
     "desc": "Chấp nhận giam mình trong lồng sắt để nhìn thấu những bí mật của thế gian.", 
     "effects": {"reduce_cooldown": -0.03, "rare_bias": 0.03, "sell_bonus": 0.01}},

    {"id": "5226", "name": "Búa Vỡ", "emoji": "<:5226:1503397793421594907>", "rarity": "uncommon", 
     "desc": "Đập tan trật tự cũ để tìm thấy cơ hội trong những mảnh vụn đổ nát.", 
     "effects": {"sell_bonus": 0.03,  "reduce_fail": 0.02}},

    {"id": "5225", "name": "Kẻ Ngốc", "emoji": "<:5225:1503397796684894380>", "rarity": "legendary", 
     "desc": "Bước qua vực thẳm với nụ cười vô tri, nơi quy luật trần thế không còn chạm tới.", 
     "effects": {"sell_bonus": 0.10,  "reduce_fail": 0.03}},

    {"id": "5224", "name": "Nhật Kí Của Oneiroi", "emoji": "<:5224:1503397799406997585>", "rarity": "epic", 
     "desc": "Những trang giấy từ cõi mộng, nơi thực tại bị bóp méo bởi lời thì thầm điên loạn.", 
     "effects": {"passive_oneiroi": 0.02}},

    {"id": "5223", "name": "Khiêu Chiến", "emoji": "<:5223:1503397801588162591>", "rarity": "uncommon", 
     "desc": "Ném găng tay vào mặt định mệnh; sự ngạo mạn chính là tấm khiên vững chãi nhất.", 
     "effects": {"reduce_fail": 0.03}},

    {"id": "5222", "name": "Lòng Tham Và Sự Dối Trá", "emoji": "<:5222:1503397811801034905>", "rarity": "epic", 
     "desc": "Bản khế ước viết bằng máu khô, hứa hẹn sự sống nhưng giấu nhẹm đi cái giá.", 
     "effects": {"reduce_fail": 0.03, "luck_up": 0.02, "reduce_cooldown": 0.01}},

    {"id": "5221", "name": "Lôi Đỏ", "emoji": "<:5221:1503397814930116608>", "rarity": "rare", 
     "desc": "Tiếng sấm từ bầu trời máu; điềm báo của sự thịnh vượng xây trên tro tàn.", 
     "effects": {"sell_bonus": 0.04}},

    {"id": "5220", "name": "Dao Găm Của Lựa Chọn Cuối Cùng", "emoji": "<:5220:1503397819262963893>", "rarity": "epic", 
     "desc": "Lưỡi dao chỉ sắc khi kẻ cầm nó không còn đường lui; một canh bạc sinh tử.", 
     "effects": {"luck_up": 0.05}},

    {"id": "5219", "name": "Bảo Thủ", "emoji": "<:5219:1503397821888335902>", "rarity": "uncommon", 
     "desc": "An toàn trong chiếc lồng của quá khứ, mù quáng trước ánh sáng của tương lai.", 
     "effects": {"luck_up": -0.02,   "reduce_cooldown": 0.03}},

    {"id": "5218", "name": "Hoả Lâu", "emoji": "<:5218:1503397824098996284>", "rarity": "epic", 
     "desc": "Hộp sọ rực cháy lửa tội đồ, soi sáng những kho báu bị nguyền rủa.", 
     "effects": {"sell_bonus": 0.01,  "luck_up": 0.01,  "double_drop": 0.01}},

    {"id": "5217", "name": "Mưa Tên", "emoji": "<:5217:1503397826150010961>", "rarity": "rare", 
     "desc": "Khi cái chết đổ xuống từ hư không, kẻ tĩnh lặng nhất mới tìm thấy lối thoát.", 
     "effects": {"reduce_fail": 0.02}},

    {"id": "5216", "name": "Tín Đồ", "emoji": "<:5216:1503397828238774362>", "rarity": "rare", 
     "desc": "Sự sùng bái mù quáng mở ra những cánh cửa mà lý trí không bao giờ chạm tới.", 
     "effects": {"luck_up": 0.02,    "rare_bias": 0.01}},

    {"id": "5212", "name": "Tham Lam", "emoji": "<:5212:1503397837449330698>", "rarity": "epic", 
     "desc": "Cơn đói vĩnh cửu; bạn thấy được mọi báu vật nhưng đôi tay mãi mãi run rẩy.", 
     "effects": {"sell_bonus": -0.06, "rare_bias": 0.04}},

    {"id": "5210", "name": "Sự Cứu Rỗi", "emoji": "<:5210:1503397842180509878>", "rarity": "uncommon", 
     "desc": "Tia sáng yếu ớt nơi đáy ngục; nó không cứu mạng bạn, chỉ giữ bạn không bỏ cuộc.", 
     "effects": {"sell_bonus": 0.03}},
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
        "desc":    base.get("desc", ""),
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
#  MIGRATION — HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _is_valid_quality(quality: object) -> bool:
    """
    A quality value is valid iff it is an exact key in QUALITY_TIERS.
    Does NOT accept None, empty-string, or any unknown tier name.
    """
    return isinstance(quality, str) and quality in QUALITY_TIERS


def _is_valid_passive(passive: object) -> bool:
    """
    A passive is structurally valid iff:
      - it is a non-empty dict
      - its "id" is present in PASSIVE_INDEX (known pool entry)
      - its "roll" is a numeric value (int or float)

    Intentionally rejects {} (empty dict) — the invisible-passive corruption
    where setdefault previously silently preserved a useless value.
    A valid passive is NEVER rerolled; only structurally broken ones are replaced.
    """
    if not isinstance(passive, dict) or not passive:
        return False
    pid  = str(passive.get("id", ""))
    roll = passive.get("roll")
    return pid in PASSIVE_INDEX and isinstance(roll, (int, float))


def _dur_max_floor(rarity: str) -> int:
    """
    The absolute minimum durability_max that any legitimate roll could produce
    for a given rarity: base_durability × very_low_multiplier (0.55).

    Any stored durability_max at or above this floor is considered legitimate
    (i.e. was produced by a real roll_quality call) and will not be repaired.
    Any value *below* the floor is impossible and indicates corruption.
    """
    base = DURABILITY_BY_RARITY.get(rarity, DURABILITY_BY_RARITY["common"])
    lowest_multiplier = QUALITY_TIERS["very_low"]["multiplier"]   # 0.55 — never changes
    return max(1, int(base * lowest_multiplier))


def _dur_max_expected(rarity: str, quality: str) -> int:
    """Canonical durability_max for a given (rarity, quality) pair."""
    q_multi = QUALITY_TIERS.get(quality, {}).get("multiplier", 1.0)
    return int(DURABILITY_BY_RARITY.get(rarity, DURABILITY_BY_RARITY["common"]) * q_multi)


# ══════════════════════════════════════════════════════════════════════════════
#  MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

def migrate_weapon_instance_fields(user: dict) -> bool:
    """
    Repair and back-fill weapon instances surgically.

    Design principles
    -----------------
    REPAIR-CAPABLE  — each field is validated for *correctness*, not just presence.
                      Fields that exist but contain corrupt values are fixed.
    SURGICAL        — only fields that fail their specific invariant are written;
                      everything else is left exactly as the player earned it.
    IDEMPOTENT      — running twice produces identical output to running once.
    ADDITIVE        — missing instances for bag/equipped UIDs are created as stubs
                      and then repaired in the same pass; no UID is left orphaned.
    SAFE CLEANUP    — orphan detection requires only a valid uid anchor; instances
                      missing base_id but with a known uid are kept and repaired,
                      not destroyed.

    Repair order (each field may depend on the previous):
        quality → durability_max → durability → broken → passive

    Returns True if any field was written (caller should persist the user record).
    """
    changed = False

    # ── Step 0: Build the authoritative UID set ──────────────────────────────
    # Accept only string UIDs; silently skip malformed entries.
    valid_uids: set[str] = set()
    for uid in user.get("weapons", []):
        if isinstance(uid, str):
            valid_uids.add(uid)
    for uid in user.get("equipped", []):
        if isinstance(uid, str):
            valid_uids.add(uid)

    # ── Step 1: Safer orphan cleanup ─────────────────────────────────────────
    # Remove an instance only when we are CERTAIN it is orphaned:
    #   (a) not a dict                     — structurally unusable
    #   (b) uid key missing                — cannot anchor to any inventory slot
    #   (c) uid present but not in bag/equipped — genuine orphan
    #
    # Critically: we do NOT require base_id here.  An instance that has a valid
    # uid but is missing base_id can still be repaired in Step 3 (it falls back
    # to "common" rarity).  Destroying it would silently wipe player progression.
    raw_instances = user.get("weapon_instances", [])
    kept: list[dict] = []
    removed_count = 0
    for wi in raw_instances:
        if not isinstance(wi, dict):
            removed_count += 1
            logger.debug("migrate: discarding non-dict entry in weapon_instances")
            continue
        uid = wi.get("uid")
        if uid is None:
            removed_count += 1
            logger.debug("migrate: discarding instance with no uid")
            continue
        if uid not in valid_uids:
            removed_count += 1
            logger.debug(f"migrate: discarding orphan uid={uid!r}")
            continue
        kept.append(wi)

    if removed_count:
        logger.info(
            f"migrate_weapon_instance_fields: removed {removed_count} orphan/invalid instances"
        )
        changed = True
    user["weapon_instances"] = kept

    # ── Step 2: Create stub instances for UIDs that have none ─────────────────
    # A UID in the player's bag or equipped slots with no matching instance is
    # a data gap — the instance was never written or was accidentally deleted.
    # We create a minimal stub {"uid": uid} so Step 3 repairs it fully.
    # Sorted for determinism (consistent ordering across saves).
    existing_uids = {wi["uid"] for wi in user["weapon_instances"]}
    for uid in sorted(valid_uids - existing_uids):
        base_id = uid.split("-")[0] if "-" in uid else uid
        stub: dict = {"uid": uid, "base_id": base_id}
        user["weapon_instances"].append(stub)
        logger.info(f"migrate_weapon_instance_fields: created stub instance for uid={uid!r}")
        changed = True

    # ── Step 3: Surgical per-field repair ────────────────────────────────────
    for wi in user["weapon_instances"]:
        if not isinstance(wi, dict):
            continue  # paranoia guard; Step 1 already filtered these

        uid = wi.get("uid", "<unknown>")

        # Resolve rarity from weapon definition — safe fallback to "common".
        # If base_id is missing or unknown, all calculations use "common" values.
        try:
            w_data = get_weapon_by_id(wi.get("base_id", "")) or {}
        except Exception as exc:
            logger.warning(f"migrate: uid={uid!r} get_weapon_by_id failed: {exc}")
            w_data = {}
        rarity: str = w_data.get("rarity", "common")

        # ── quality ──────────────────────────────────────────────────────────
        # Invariant: must be a key in QUALITY_TIERS.
        # Repair:    reroll using the weapon's rarity for correct probability.
        # False-positive guard: only invalid tier names trigger a reroll;
        #   any recognised key (e.g. "high") is preserved unconditionally.
        quality_stored = wi.get("quality")
        if not _is_valid_quality(quality_stored):
            try:
                new_quality = roll_quality(rarity)
            except Exception as exc:
                logger.warning(f"migrate: uid={uid!r} roll_quality failed: {exc}")
                new_quality = "medium"
            wi["quality"] = new_quality
            logger.info(
                f"migrate: uid={uid!r} quality repaired {quality_stored!r} → {new_quality!r}"
            )
            changed = True
        quality: str = wi["quality"]  # guaranteed valid from this point

        # ── durability_max ───────────────────────────────────────────────────
        # Invariant: int ≥ _dur_max_floor(rarity).
        # Repair:    recalculate from (rarity, quality) — the same formula used
        #            at instance creation.
        # False-positive guard: the floor is the minimum any real roll could ever
        #   produce (base × 0.55).  A stored value at or above the floor is kept,
        #   even if it doesn't match the exact current formula, because the player
        #   may legitimately have rolled that tier before rounding changed.
        dur_max_stored = wi.get("durability_max")
        dur_floor      = _dur_max_floor(rarity)
        if not isinstance(dur_max_stored, int) or dur_max_stored < dur_floor:
            new_dur_max = _dur_max_expected(rarity, quality)
            logger.info(
                f"migrate: uid={uid!r} durability_max repaired "
                f"{dur_max_stored!r} → {new_dur_max} (floor={dur_floor})"
            )
            wi["durability_max"] = new_dur_max
            changed = True
        dur_max: int = wi["durability_max"]  # guaranteed ≥ dur_floor

        # ── durability ───────────────────────────────────────────────────────
        # Invariant: int in [0, dur_max].
        # Missing    → full (treat as a freshly issued weapon).
        # Negative   → clamp to 0 (impossible value; may have come from a
        #               subtraction bug elsewhere).
        # > dur_max  → clamp to dur_max (e.g. dur_max was *reduced* by repair
        #               above from a corrupted value; preserve as much as possible).
        # NOTE: we never invent durability > dur_max even to "restore" the weapon;
        #       that would silently grant free durability.
        dur_stored = wi.get("durability")
        if not isinstance(dur_stored, int):
            wi["durability"] = dur_max
            logger.info(
                f"migrate: uid={uid!r} durability set to {dur_max} (was {dur_stored!r})"
            )
            changed = True
        elif dur_stored < 0:
            wi["durability"] = 0
            logger.info(
                f"migrate: uid={uid!r} durability clamped {dur_stored} → 0"
            )
            changed = True
        elif dur_stored > dur_max:
            wi["durability"] = dur_max
            logger.info(
                f"migrate: uid={uid!r} durability clamped {dur_stored} → {dur_max}"
            )
            changed = True
        durability: int = wi["durability"]  # guaranteed in [0, dur_max]

        # ── broken ───────────────────────────────────────────────────────────
        # Invariant: bool; broken MUST be True when durability == 0.
        # `broken` is a derived field — decrease_durability() sets it at the
        # moment durability hits zero.  Any disagreement between broken and
        # durability is therefore corruption, not a legitimate game state.
        #
        # We fix BOTH impossible cases:
        #   durability == 0  and  broken == False  → weapon should be broken
        #   durability  > 0  and  broken == True   → weapon can't be broken with HP
        broken_stored = wi.get("broken")
        correct_broken = (durability == 0)
        if not isinstance(broken_stored, bool):
            wi["broken"] = correct_broken
            logger.info(
                f"migrate: uid={uid!r} broken set to {correct_broken} "
                f"(was {broken_stored!r}, durability={durability})"
            )
            changed = True
        elif broken_stored != correct_broken:
            wi["broken"] = correct_broken
            logger.info(
                f"migrate: uid={uid!r} broken corrected "
                f"{broken_stored} → {correct_broken} (durability={durability})"
            )
            changed = True

        # ── passive ──────────────────────────────────────────────────────────
        # Invariant: {"id": <id in PASSIVE_INDEX>, "roll": <numeric>}.
        # Invalid states (all trigger a fresh roll):
        #   - key absent in wi entirely
        #   - {} empty dict  ← the "invisible passive" silent corruption
        #   - id missing or not in PASSIVE_INDEX (unknown passive, possibly from
        #     a content removal)
        #   - roll missing or not numeric
        #
        # Valid passives are NEVER rerolled.  The check is strict equality
        # against PASSIVE_INDEX — no fuzzy matching.
        passive_stored = wi.get("passive")
        if not _is_valid_passive(passive_stored):
            try:
                new_passive = roll_passive(rarity, quality)
            except Exception as exc:
                logger.warning(f"migrate: uid={uid!r} roll_passive failed: {exc}")
                new_passive = roll_passive("common", "medium")
            wi["passive"] = new_passive
            logger.info(
                f"migrate: uid={uid!r} passive repaired "
                f"(was {passive_stored!r}) → id={new_passive.get('id')!r}"
            )
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
    dur_max = wi.get("durability_max", 0)
    if not isinstance(dur_max, int) or dur_max <= 0:
        logger.warning(
            f"fmt_instance_info: uid={wi.get('uid')!r} has invalid "
            f"durability_max={dur_max!r} — rendering with fallback"
        )
        dur_max = 1  # safe fallback: render partial info rather than hide all
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
            if resolved.get("desc"):
                lines.append(f"  `{resolved['desc']}`")
            for k, v in resolved["effects"].items():
                if k == "extra_slot":
                    lines.append(f"  └ `{k}`: **+{int(v)} ô**")
                elif isinstance(v, float):
                    lines.append(f"  └ `{k}`: **{v:+.1%}**")
                else:
                    lines.append(f"  └ `{k}`: **{v:+}**")

    return "\n".join(lines)
