
import random

from rpg_weapon_data import RARITY_LABEL

__all__ = [
    "PASSIVE_POOL", "PASSIVE_INDEX", "PASSIVE_TIER_WEIGHTS",
    "roll_passive", "resolve_passive",
]


# ══════════════════════════════════════════════════════════════════════════════
#  PASSIVE CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

PASSIVE_TIER_WEIGHTS = {
    "common":    {"common": 70,  "uncommon": 25, "rare": 4,  "epic": 0.8, "legendary": 0.2},
    "uncommon":  {"common": 50,  "uncommon": 35, "rare": 10, "epic": 4,   "legendary": 1},
    "rare":      {"common": 30,  "uncommon": 35, "rare": 25, "epic": 8,   "legendary": 2},
    "epic":      {"common": 15,  "uncommon": 25, "rare": 35, "epic": 20,  "legendary": 5},
    "legendary": {"common": 5,   "uncommon": 15, "rare": 30, "epic": 35,  "legendary": 15},
    "special":   {"common": 3,   "uncommon": 10, "rare": 25, "epic": 37,  "legendary": 25},
    "soul":      {"common": 2,   "uncommon": 8,  "rare": 20, "epic": 35,  "legendary": 35},
}

# Giá trị âm là CỐ Ý trade-off design — không sửa, không abs()
PASSIVE_POOL = [

    {"id": "5234", "name": "Bánh Xe Tai Ương", "emoji": "<:5234:1503397777579708547>", "rarity": "legendary",
     "desc": "Vòng xoay không vận hành bằng may mắn, nó nghiền nát linh hồn để đổi lấy thiên cơ.",
     "effects": {"rare_bias": 0.06, "luck_up": -0.03}},

    {"id": "5233", "name": "Cổ Nha", "emoji": "<:5233:1503397779589042326>", "rarity": "legendary",
     "desc": "Nanh vuốt che chắn cho kẻ kế thừa trước những bước chân lầm lạc.",
     "effects": {"reduce_fail": 0.08, "reduce_cooldown": -0.04}},

    {"id": "5225", "name": "Kẻ Ngốc", "emoji": "<:5225:1503397796684894380>", "rarity": "legendary",
     "desc": "Bước qua vực thẳm với nụ cười vô tri, nơi quy luật trần thế không còn chạm tới.",
     "effects": {"sell_bonus": 0.10, "rare_bias": -0.04}},

    {"id": "5950", "name": "Tự Do", "emoji": "<:5950:1506176200433729587>", "rarity": "legendary",
     "desc": "Không xiềng, không trói, không còn giới hạn của thế gian bủa vây.",
     "effects": {"reduce_cooldown": 0.08, "reduce_fail": 0.04}},

    {"id": "5948", "name": "Thủ Lĩnh Và Sự Kiểm Soát", "emoji": "<:5948:1506176204686884914>", "rarity": "legendary",
     "desc": "Quyền năng thật sự không phải là sức mạnh, mà là sự tĩnh lặng giữa hỗn loạn.",
     "effects": {"reduce_fail": 0.06, "sell_bonus": 0.04, "rare_bias": 0.02}},

    {"id": "5227", "name": "Trói Buộc", "emoji": "<:5227:1503397792062898237>", "rarity": "epic",
     "desc": "Chấp nhận giam mình trong lồng sắt để nhìn thấu những bí mật của thế gian.",
     "effects": {"rare_bias": 0.06, "reduce_cooldown": -0.05}},

    {"id": "5224", "name": "Nhật Kí Của Oneiroi", "emoji": "<:5224:1503397799406997585>", "rarity": "epic",
     "desc": "Những trang giấy từ cõi mộng, nơi thực tại bị bóp méo bởi lời thì thầm điên loạn.",
     "effects": {"passive_oneiroi": 0.06, "luck_up": -0.03}},

    {"id": "5222", "name": "Lòng Tham Và Sự Dối Trá", "emoji": "<:5222:1503397811801034905>", "rarity": "epic",
     "desc": "Bản khế ước viết bằng máu khô, hứa hẹn sự sống nhưng giấu nhẹm đi cái giá.",
     "effects": {"luck_up": 0.07, "reduce_fail": -0.04}},

    {"id": "5220", "name": "Dao Găm Của Lựa Chọn Cuối Cùng", "emoji": "<:5220:1503397819262963893>", "rarity": "epic",
     "desc": "Lưỡi dao chỉ sắc khi kẻ cầm nó không còn đường lui; một canh bạc sinh tử.",
     "effects": {"luck_up": 0.08, "sell_bonus": -0.04}},

    {"id": "5218", "name": "Hoả Lâu", "emoji": "<:5218:1503397824098996284>", "rarity": "epic",
     "desc": "Hộp sọ rực cháy lửa tội đồ, soi sáng những kho báu bị nguyền rủa.",
     "effects": {"double_drop": 0.08, "sell_bonus": -0.03}},

    {"id": "5212", "name": "Tham Lam", "emoji": "<:5212:1503397837449330698>", "rarity": "epic",
     "desc": "Cơn đói vĩnh cửu; bạn thấy được mọi báu vật nhưng đôi tay mãi mãi run rẩy.",
     "effects": {"rare_bias": 0.08, "sell_bonus": -0.07}},

    {"id": "5954", "name": "Trói Buộc II", "emoji": "<:5954:1506176193475641374>", "rarity": "epic",
     "desc": "Lồng sắt cũ bị đúc lại, nặng hơn nhưng sắc hơn trong từng khóa then.",
     "effects": {"rare_bias": 0.07, "reduce_cooldown": -0.06}},

    {"id": "5953", "name": "Kiểm Soát", "emoji": "<:5953:1506176195056893972>", "rarity": "epic",
     "desc": "Không phải ai cũng cần may mắn — kẻ đủ kiên nhẫn tự tạo ra xác suất của mình.",
     "effects": {"reduce_fail": 0.06, "luck_up": -0.04}},

    {"id": "5951", "name": "Quả Bom Nổ Chậm", "emoji": "<:5951:1506176198466863145>", "rarity": "epic",
     "desc": "Sức mạnh nằm im trong từng tick tích lũy, rồi bùng nổ khi ít ai ngờ tới nhất.",
     "effects": {"double_drop": 0.07, "sell_bonus": -0.08}},

    {"id": "5949", "name": "Tiếp Thu", "emoji": "<:5949:1506176202061119509>", "rarity": "epic",
     "desc": "Ít mà tinh; bạn học cách lọc tạp chất trước khi chúng kịp bén rễ.",
     "effects": {"luck_up": 0.05, "double_item": -0.06}},

    {"id": "5562", "name": "Nhìn Thấu", "emoji": "<:5562:1506176208998629496>", "rarity": "epic",
     "desc": "Mắt nhìn xuyên màn đêm, tai nghe vọng tiếng kho báu từ cõi mộng.",
     "effects": {"treasure_hunt": 0.06, "passive_oneiroi": 0.03}},

    {"id": "5565", "name": "Cơn Xoáy Nội Tâm", "emoji": "<:5565:1506176210978345021>", "rarity": "epic",
     "desc": "Vòng xoáy từ bên trong kéo theo mọi thứ xung quanh vào quỹ đạo của nó.",
     "effects": {"double_item": 0.09, "reduce_cooldown": -0.05}},

    {"id": "5231", "name": "Kẻ Dối Trá", "emoji": "<:5231:1503397783699456140>", "rarity": "rare",
     "desc": "Nụ cười che giấu quân bài rác; trong thế giới này, sự chân thật là một sai lầm.",
     "effects": {"extra_slot": 1, "luck_up": -0.02}},

    {"id": "5230", "name": "Sự Hối Lỗi", "emoji": "<:5230:1503397785964249148>", "rarity": "rare",
     "desc": "Lời cầu nguyện muộn màng trước giá treo cổ đôi khi khiến thần chết mủi lòng.",
     "effects": {"reduce_fail": 0.07}},

    {"id": "5229", "name": "Nắm Chặt", "emoji": "<:5229:1503397788107673710>", "rarity": "rare",
     "desc": "Ghì chặt định mệnh trong lòng bàn tay, dù đôi chân phải quỵ ngã vì sức nặng.",
     "effects": {"reduce_fail": 0.09, "reduce_cooldown": -0.05}},

    {"id": "5228", "name": "Lá Vàng", "emoji": "<:5228:1503397789852504155>", "rarity": "rare",
     "desc": "Mảnh vụn từ vương miện của một vị vua mất nước; hào nhoáng nhưng đầy phù du.",
     "effects": {"sell_bonus": 0.06, "luck_up": 0.02}},

    {"id": "5221", "name": "Lôi Đỏ", "emoji": "<:5221:1503397814930116608>", "rarity": "rare",
     "desc": "Tiếng sấm từ bầu trời máu; điềm báo của sự thịnh vượng xây trên tro tàn.",
     "effects": {"sell_bonus": 0.07}},

    {"id": "5217", "name": "Mưa Tên", "emoji": "<:5217:1503397826150010961>", "rarity": "rare",
     "desc": "Khi cái chết đổ xuống từ hư không, kẻ tĩnh lặng nhất mới tìm thấy lối thoát.",
     "effects": {"reduce_fail": 0.06}},

    {"id": "5216", "name": "Tín Đồ", "emoji": "<:5216:1503397828238774362>", "rarity": "rare",
     "desc": "Sự sùng bái mù quáng mở ra những cánh cửa mà lý trí không bao giờ chạm tới.",
     "effects": {"luck_up": 0.06, "rare_bias": 0.03}},

    {"id": "5215", "name": "Mưa Sao Băng", "emoji": "<:5215:1503397830851694753>", "rarity": "rare",
     "desc": "Ánh sao xẹt qua nhanh đến mức không ai kịp ước — nhưng bầu trời lại đầy hơn bao giờ hết.",
     "effects": {"double_drop": 0.07, "sell_bonus": -0.04}},

    {"id": "5955", "name": "Ẩn Nấp", "emoji": "<:5955:1506176191550328832>", "rarity": "rare",
     "desc": "Kẻ không bị nhìn thấy không cần phòng thủ — tốc độ chính là tàng hình tốt nhất.",
     "effects": {"reduce_cooldown": 0.09, "extra_slot": -1}},

    {"id": "5952", "name": "Xuyên Thấu", "emoji": "<:5952:1506176196809855037>", "rarity": "rare",
     "desc": "Mũi nhọn cắt qua giáp trụ — nhưng đôi khi xuyên luôn vào khoảng không.",
     "effects": {"double_value": 0.08, "reduce_fail": -0.04}},

    {"id": "5947", "name": "Phá Vỡ", "emoji": "<:5947:1506176207027306566>", "rarity": "rare",
     "desc": "Phá tan giới hạn của túi đựng bằng sức mạnh thô — nhưng thứ gì đó vẫn vỡ vụn theo.",
     "effects": {"extra_slot": 1, "sell_bonus": -0.05}},

    {"id": "5564", "name": "Phá Hoại", "emoji": "<:5564:1506176213134348351>", "rarity": "rare",
     "desc": "Gây hỗn loạn không phải để phá hủy, mà để lộ ra những gì bị chôn vùi phía dưới.",
     "effects": {"event_hunt": 0.07, "sell_bonus": -0.04}},

    {"id": "5566", "name": "Rằng Xé", "emoji": "<:5566:1506176216846303322>", "rarity": "rare",
     "desc": "Nanh sắc bén không phân biệt thịt và vàng — mọi thứ đều bị xé toạc như nhau.",
     "effects": {"rare_bias": 0.05, "reduce_fail": -0.06}},

    {"id": "5232", "name": "Ảnh Trảm", "emoji": "<:5232:1503397781325217933>", "rarity": "uncommon",
     "desc": "Nhát chém cắt đứt sợi dây của thời gian, để lại thực tại một vết mờ hư ảo.",
     "effects": {"reduce_cooldown": 0.08}},

    {"id": "5226", "name": "Búa Vỡ", "emoji": "<:5226:1503397793421594907>", "rarity": "uncommon",
     "desc": "Đập tan trật tự cũ để tìm thấy cơ hội trong những mảnh vụn đổ nát.",
     "effects": {"sell_bonus": 0.04, "reduce_fail": 0.03}},

    {"id": "5223", "name": "Khiêu Chiến", "emoji": "<:5223:1503397801588162591>", "rarity": "uncommon",
     "desc": "Ném găng tay vào mặt định mệnh; sự ngạo mạn chính là tấm khiên vững chãi nhất.",
     "effects": {"reduce_fail": 0.05}},

    {"id": "5219", "name": "Bảo Thủ", "emoji": "<:5219:1503397821888335902>", "rarity": "uncommon",
     "desc": "An toàn trong chiếc lồng của quá khứ, mù quáng trước ánh sáng của tương lai.",
     "effects": {"reduce_cooldown": 0.07, "luck_up": -0.03}},

    {"id": "5210", "name": "Sự Cứu Rỗi", "emoji": "<:5210:1503397842180509878>", "rarity": "uncommon",
     "desc": "Tia sáng yếu ớt nơi đáy ngục; nó không cứu mạng bạn, chỉ giữ bạn không bỏ cuộc.",
     "effects": {"sell_bonus": 0.05}},

    {"id": "5213", "name": "Phong Thủy", "emoji": "<:5213:1503397835255709887>", "rarity": "uncommon",
     "desc": "Cân bằng là tấm khiên vô hình; kẻ không lao vào cực đoan không bao giờ ngã.",
     "effects": {"luck_up": 0.03, "reduce_cooldown": 0.02}},

    {"id": "5563", "name": "Chính Xác", "emoji": "<:5563:1506176215176843354>", "rarity": "uncommon",
     "desc": "Mỗi nhát chém đều có chủ đích; sự chính xác không cần may mắn.",
     "effects": {"reduce_fail": 0.04, "reduce_uncommon": 0.03}},

]

# O(1) lookup theo id
PASSIVE_INDEX: dict[str, dict] = {p["id"]: p for p in PASSIVE_POOL}


# ══════════════════════════════════════════════════════════════════════════════
#  PASSIVE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def roll_passive(weapon_rarity: str = "common", quality: float = 1.0) -> dict:
    """
    Roll 1 passive cho weapon instance mới.
    Lưu compact {"id", "roll"} — resolve full data tại runtime qua resolve_passive().

    Quality là float [0.50, 1.50] — ảnh hưởng nhỏ lên roll multiplier (±2.5%).
    1.0 = neutral, 1.50 = +2.5%, 0.50 = -2.5%.
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
    q_bonus   = (quality - 1.0) * 0.05        # [-0.025, +0.025]
    base_roll = random.uniform(0.88, 1.12)
    roll      = round(base_roll * (1 + q_bonus), 6)

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
