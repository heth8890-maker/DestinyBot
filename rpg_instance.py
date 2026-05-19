
import random
import logging

from rpg_weapon_data import get_weapon_by_id, RARITY_LABEL
from rpg_passive import (
    PASSIVE_POOL, PASSIVE_INDEX, PASSIVE_TIER_WEIGHTS,
    roll_passive, resolve_passive, _is_valid_passive,
)

logger = logging.getLogger(__name__)

__all__ = [
    "QUALITY_MIN", "QUALITY_MAX", "QUALITY_COLOR_THRESHOLDS", "DURABILITY_BY_RARITY",
    "PASSIVE_POOL", "PASSIVE_INDEX", "PASSIVE_TIER_WEIGHTS",
    "roll_quality", "quality_label", "quality_color",
    "roll_passive", "resolve_passive",
    "build_weapon_effects",
    "migrate_weapon_instance_fields",
    "decrease_durability",
]


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# Quality lưu dưới dạng float: 1.00 = 100% = chỉ số gốc không đổi.
# Phạm vi hợp lệ: [0.50, 1.50] — tức 50% đến 150%.
QUALITY_MIN: float = 0.50
QUALITY_MAX: float = 1.50

# Ngưỡng màu hiển thị cho Discord embed.
# Mỗi entry: (ngưỡng_trên_exclusive, hex_color).
# Duyệt từ đầu — lấy màu của entry đầu tiên mà quality < ngưỡng.
QUALITY_COLOR_THRESHOLDS: list[tuple[float, int]] = [
    (0.65, 0x696969),   # < 65%   : xám đậm
    (0.75, 0xA0A0A0),   # 65–75%  : bạc
    (0.85, 0xC0C0C0),   # 75–85%  : trắng bạc
    (0.95, 0xFFFFFF),   # 85–95%  : trắng
    (1.05, 0x90EE90),   # 95–105% : xanh lá nhạt (quanh base)
    (1.20, 0x00BFFF),   # 105–120%: xanh dương
    (1.35, 0xFF8C00),   # 120–135%: cam
    (1.51, 0xFF0000),   # 135–150%: đỏ
]

# ── Beta distribution params cho từng rarity ─────────────────────────────────
#
# Dùng Beta(α, β) scaled tuyến tính lên [QUALITY_MIN, QUALITY_MAX]:
#   quality = QUALITY_MIN + betavariate(α, β) * (QUALITY_MAX - QUALITY_MIN)
#
# Công thức nhanh (trong khoảng [0.50, 1.50]):
#   mode  = QUALITY_MIN + (α−1)/(α+β−2) * 1.00
#   mean  = QUALITY_MIN + α/(α+β)       * 1.00
#
# common    Beta(2, 4): mode≈75%,  mean≈83%  ← đỉnh phân phối tại 75%
# uncommon  Beta(2.3, 4): mode≈76%, mean≈84%
# rare      Beta(2.7, 4): mode≈78%, mean≈85%
# epic      Beta(3.2, 4): mode≈81%, mean≈86%
# legendary Beta(3.8, 4): mode≈84%, mean≈88%
# mythical  Beta(5.0, 4): mode≈89%, mean≈91%
# special / soul  Beta(4.5, 4): mode≈87%, mean≈89%
#
# Vì α < β trong mọi trường hợp → phân phối lệch phải (đuôi dài về phía cao),
# tức là xác suất đạt 140–150% rất hiếm — đúng ý đồ thiết kế.
_QUALITY_BETA_PARAMS: dict[str, tuple[float, float]] = {
    "common":    (2.0, 4.0),
    "uncommon":  (2.3, 4.0),
    "rare":      (2.7, 4.0),
    "epic":      (3.2, 4.0),
    "legendary": (3.8, 4.0),
    "legend":    (3.8, 4.0),
    "mythical":  (5.0, 4.0),
    "special":   (4.5, 4.0),
    "soul":      (4.5, 4.0),
}

DURABILITY_BY_RARITY: dict[str, int] = {
    "common":    30,
    "uncommon":  50,
    "rare":      80,
    "epic":      120,
    "legendary": 200,
    "legend":    200,
    "mythical":  420,
    "special":   300,
    "soul":      300,
}

# ── Legacy mapping: tier string cũ → float tương đương khi migrate ───────────
# "extreme" (1.70) bị clamp xuống QUALITY_MAX (1.50) khi áp dụng.
_LEGACY_QUALITY_TO_FLOAT: dict[str, float] = {
    "very_low":    0.55,
    "low":         0.75,
    "medium_low":  0.90,
    "medium":      1.00,
    "medium_high": 1.12,
    "high":        1.25,
    "very_high":   1.45,
    "extreme":     1.50,   # clamp từ 1.70 → 1.50 (trần mới)
}


# ══════════════════════════════════════════════════════════════════════════════
#  DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def quality_color(quality: float) -> int:
    """Trả về Discord embed color (int hex) tương ứng với quality float."""
    for threshold, color in QUALITY_COLOR_THRESHOLDS:
        if quality < threshold:
            return color
    return QUALITY_COLOR_THRESHOLDS[-1][1]


def quality_label(quality: float) -> str:
    """
    Trả về chuỗi hiển thị quality kèm emoji màu.
    Ví dụ: 0.834 → '⬜ 83%'

    Emoji map (khớp với QUALITY_COLOR_THRESHOLDS):
        ⬛ < 65%  |  🔘 65–75%  |  ⬜ 75–85%  |  🟢 85–95%
        🔵 95–105% |  🟡 105–120%  |  🟠 120–135%  |  🔴 135–150%
    """
    pct = round(quality * 100)
    if quality < 0.65:
        emoji = "⬛"
    elif quality < 0.75:
        emoji = "🔘"
    elif quality < 0.85:
        emoji = "⬜"
    elif quality < 0.95:
        emoji = "🟢"
    elif quality < 1.05:
        emoji = "🔵"
    elif quality < 1.20:
        emoji = "🟡"
    elif quality < 1.35:
        emoji = "🟠"
    else:
        emoji = "🔴"
    return f"{emoji} **{pct}%**"


# ══════════════════════════════════════════════════════════════════════════════
#  ROLL FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def roll_quality(rarity: str = "common") -> float:
    """
    Roll quality cho weapon instance mới.
    Trả về float trong [QUALITY_MIN, QUALITY_MAX] (0.50 → 1.50).

    Dùng Beta distribution: phân phối liên tục hình chuông lệch trái —
    đỉnh xác suất quanh 75–80%, đuôi dài về phía cao (>100% hiếm, >130% rất hiếm).
    Weapon rarity cao → alpha lớn hơn → toàn bộ phân phối dịch phải.

    100.00% = chỉ số gốc không đổi.
    """
    alpha, beta_param = _QUALITY_BETA_PARAMS.get(rarity, _QUALITY_BETA_PARAMS["common"])
    raw     = random.betavariate(alpha, beta_param)            # [0.0, 1.0]
    quality = QUALITY_MIN + raw * (QUALITY_MAX - QUALITY_MIN)  # scale → [0.50, 1.50]
    return round(quality, 4)   # độ chính xác 0.01%


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
        2. Nhân quality multiplier   (float [0.50, 1.50] — trực tiếp từ wi["quality"])
        3. Cộng passive effects      (resolved từ roll)

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
    # quality lưu dưới dạng float: 1.00 = 100% base, 0.75 = 75%, 1.30 = 130%, v.v.
    raw_quality = wi.get("quality", 1.0) if wi else 1.0
    if isinstance(raw_quality, (int, float)) and not isinstance(raw_quality, bool):
        q_multi = max(QUALITY_MIN, min(QUALITY_MAX, float(raw_quality)))
    else:
        q_multi = 1.0   # fallback an toàn khi gặp giá trị corrupt
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
    Quality hợp lệ khi và chỉ khi là số thực (int hoặc float, không phải bool)
    nằm trong [QUALITY_MIN, QUALITY_MAX].
    Không chấp nhận None, chuỗi tier cũ, hay bất kỳ giá trị ngoài phạm vi.
    """
    if isinstance(quality, bool):
        return False
    if not isinstance(quality, (int, float)):
        return False
    return QUALITY_MIN <= float(quality) <= QUALITY_MAX


def _dur_max_floor(rarity: str) -> int:
    """
    Ngưỡng sàn tuyệt đối của durability_max cho một rarity nhất định:
    base_durability × QUALITY_MIN (0.50).

    Mọi durability_max được lưu tại hoặc trên ngưỡng này đều hợp lệ.
    Giá trị dưới ngưỡng là dữ liệu corrupt và sẽ bị sửa.
    """
    base = DURABILITY_BY_RARITY.get(rarity, DURABILITY_BY_RARITY["common"])
    return max(1, int(base * QUALITY_MIN))


def _dur_max_expected(rarity: str, quality: float) -> int:
    """Canonical durability_max cho cặp (rarity, quality) — đúng với công thức hiện tại."""
    q_multi = max(QUALITY_MIN, min(QUALITY_MAX, float(quality)))
    return int(DURABILITY_BY_RARITY.get(rarity, DURABILITY_BY_RARITY["common"]) * q_multi)


# ══════════════════════════════════════════════════════════════════════════════
#  MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

def migrate_weapon_instance_fields(user: dict) -> bool:
    """
    Repair and back-fill weapon instances surgically.

    Design principles
    -----------------
    REPAIR-CAPABLE  — mỗi field được kiểm tra tính đúng đắn, không chỉ sự tồn tại.
                      Field tồn tại nhưng chứa giá trị corrupt sẽ được sửa.
    SURGICAL        — chỉ các field vi phạm invariant mới bị ghi lại;
                      mọi thứ còn lại giữ nguyên như người chơi đã kiếm được.
    IDEMPOTENT      — chạy hai lần cho kết quả giống hệt chạy một lần.
    ADDITIVE        — instance thiếu cho UID trong bag/equipped được tạo mới dạng stub
                      rồi repair trong cùng pass; không UID nào bị bỏ sót.
    SAFE CLEANUP    — orphan detection chỉ cần uid hợp lệ; instance thiếu base_id
                      nhưng có uid đã biết sẽ được giữ lại và repair, không bị xóa.
    LEGACY MIGRATE  — quality dạng string tier cũ (vd: "high") được chuyển đổi sang
                      float tương đương thay vì reroll, bảo toàn tiến trình người chơi.

    Repair order (mỗi field có thể phụ thuộc field trước):
        quality → durability_max → durability → broken → passive

    Returns True nếu có bất kỳ field nào được ghi (caller nên persist lại user record).
    """
    changed = False

    # ── Step 0: Build the authoritative UID set ──────────────────────────────
    valid_uids: set[str] = set()
    for uid in user.get("weapons", []):
        if isinstance(uid, str):
            valid_uids.add(uid)
    for uid in user.get("equipped", []):
        if isinstance(uid, str):
            valid_uids.add(uid)

    # ── Step 1: Safer orphan cleanup ─────────────────────────────────────────
    # Xóa instance khi chắc chắn là orphan:
    #   (a) không phải dict            — không dùng được về cấu trúc
    #   (b) thiếu key uid              — không thể neo vào slot nào
    #   (c) uid có nhưng không có trong bag/equipped — orphan thực sự
    #
    # KHÔNG yêu cầu base_id ở đây. Instance có uid hợp lệ nhưng thiếu base_id
    # vẫn có thể repair ở Step 3 (fallback về rarity "common").
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
            continue

        uid = wi.get("uid", "<unknown>")

        # Resolve rarity từ weapon definition — fallback an toàn về "common".
        try:
            w_data = get_weapon_by_id(wi.get("base_id", "")) or {}
        except Exception as exc:
            logger.warning(f"migrate: uid={uid!r} get_weapon_by_id failed: {exc}")
            w_data = {}
        rarity: str = w_data.get("rarity", "common")

        # ── quality ──────────────────────────────────────────────────────────
        # Invariant: float trong [QUALITY_MIN, QUALITY_MAX].
        #
        # Migration path:
        #   - Đã là float hợp lệ    → giữ nguyên (không reroll)
        #   - String tier cũ        → chuyển đổi sang float tương đương (bảo toàn tiến trình)
        #   - Giá trị không nhận ra → reroll mới theo rarity
        quality_stored = wi.get("quality")
        if not _is_valid_quality(quality_stored):
            if isinstance(quality_stored, str) and quality_stored in _LEGACY_QUALITY_TO_FLOAT:
                # Chuyển đổi legacy tier → float, clamp vào [QUALITY_MIN, QUALITY_MAX]
                legacy_float = _LEGACY_QUALITY_TO_FLOAT[quality_stored]
                new_quality  = round(max(QUALITY_MIN, min(QUALITY_MAX, legacy_float)), 4)
                logger.info(
                    f"migrate: uid={uid!r} quality migrated (legacy) "
                    f"{quality_stored!r} → {new_quality:.2%}"
                )
            else:
                try:
                    new_quality = roll_quality(rarity)
                except Exception as exc:
                    logger.warning(f"migrate: uid={uid!r} roll_quality failed: {exc}")
                    new_quality = 1.0
                logger.info(
                    f"migrate: uid={uid!r} quality repaired "
                    f"{quality_stored!r} → {new_quality:.2%}"
                )
            wi["quality"] = new_quality
            changed = True
        quality: float = float(wi["quality"])   # guaranteed in [QUALITY_MIN, QUALITY_MAX]

        # ── durability_max ───────────────────────────────────────────────────
        # Invariant: int ≥ _dur_max_floor(rarity).
        # Repair: recalculate từ (rarity, quality).
        # False-positive guard: giá trị tại hoặc trên floor là hợp lệ và được giữ,
        #   dù không khớp chính xác với công thức hiện tại.
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
        dur_max: int = wi["durability_max"]   # guaranteed ≥ dur_floor

        # ── durability ───────────────────────────────────────────────────────
        # Invariant: int trong [0, dur_max].
        # Thiếu   → full (vũ khí mới cấp).
        # Âm      → clamp về 0.
        # > max   → clamp về dur_max.
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
        durability: int = wi["durability"]   # guaranteed in [0, dur_max]

        # ── broken ───────────────────────────────────────────────────────────
        # Invariant: bool; broken PHẢI là True khi durability == 0.
        # `broken` là derived field — decrease_durability() set nó khi durability về 0.
        # Mọi bất đồng là corrupt, không phải game state hợp lệ.
        broken_stored  = wi.get("broken")
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
        # Mọi state không hợp lệ → roll passive mới.
        # Passive hợp lệ KHÔNG BAO GIỜ bị reroll.
        passive_stored = wi.get("passive")
        if not _is_valid_passive(passive_stored):
            try:
                new_passive = roll_passive(rarity, quality)
            except Exception as exc:
                logger.warning(f"migrate: uid={uid!r} roll_passive failed: {exc}")
                new_passive = roll_passive("common", 1.0)
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

def decrease_durability(user: dict, equipped: list, effects: dict | None = None) -> list[str]:
    """
    Trừ 1 durability cho mỗi weapon equipped đang không broken.
    Set broken=True khi durability về 0.
    Mutates user["weapon_instances"] in-place.

    Args:
        user:     user dict từ get_user()
        equipped: list uid đang trang bị (user["equipped"])
        effects:  aggregated effects dict (từ parse_effects_upgraded). Optional.
                  Dùng để check unbreaking — xác suất bỏ qua trừ durability.

    Returns:
        list uid vừa bị hỏng trong lần hunt này — để hiển thị cảnh báo cho người chơi.
    """
    if effects is None:
        effects = {}
    unbreaking = effects.get("unbreaking", 0.0)

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
        if unbreaking > 0 and random.random() < unbreaking:
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

    # ── quality ──
    raw_quality = wi.get("quality", 1.0)
    if isinstance(raw_quality, (int, float)) and not isinstance(raw_quality, bool):
        q_float = max(QUALITY_MIN, min(QUALITY_MAX, float(raw_quality)))
    else:
        q_float = 1.0
    q_str = quality_label(q_float)   # vd: "🟡 112%"

    # ── durability ──
    dur_max = wi.get("durability_max", 0)
    if not isinstance(dur_max, int) or dur_max <= 0:
        logger.warning(
            f"fmt_instance_info: uid={wi.get('uid')!r} has invalid "
            f"durability_max={dur_max!r} — rendering with fallback"
        )
        dur_max = 1
    dur    = wi.get("durability", dur_max)
    broken = wi.get("broken", False)
    fill   = int(dur / max(dur_max, 1) * 10)
    dur_bar = "█" * fill + "░" * (10 - fill)

    lines = [
        "",
        f"Phẩm chất: {q_str}",
    ]
    if broken:
        lines.append("Độ bền: ⚠️ **HỎng** — cần sửa chữa")
    else:
        lines.append(f"Độ bền: `{dur_bar}` {dur}/{dur_max}")

    # ── passive ──
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
