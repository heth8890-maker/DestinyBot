"""
===== FILE: rpg_weapon_data.py =====
Chứa: WEAPONS + WEAPON_EFFECTS + CRATES + hằng số hiển thị + roll/lookup/sell helpers.
Không phụ thuộc vào bất kỳ module discord nào trong project.

Tách từ rpg_weapon.py để tránh phình to.
rpg_weapon_cog.py import từ file này.

⚡ Patches áp dụng (từ rpg_weapon_audit.md):
  - PATCH 1: _fmt_effects_scaled — thêm instance_missing, label passive riêng
  - PATCH 2: calculate_combined_effects — skip passive khi missing instance, inject sentinel
  - PATCH 3: _fmt_combined_effects — surface missing-instance warning
"""

import random
import discord

# ═══════════════════════════════════════════════════════════
# WEAPON EFFECTS
# Mỗi key tương ứng với 1 hiệu ứng trong game logic (rpg_core.py).
# ═══════════════════════════════════════════════════════════

WEAPON_EFFECTS = {
    # ── COMMON: Gỗ cổ thụ ──
    "463": {
        "reduce_fail": 0.05,           # giảm 5% fail rate khi hunt
    },

    # ── UNCOMMON: Tách trà thư giãn ──
    "465": {
        "reduce_fail":     0.15,       # giảm 15% fail rate
        "reduce_cooldown": 0.20,       # giảm 20% cooldown hunt
        "sell_bonus":      0.02,       # ★ NEW: tăng 2% giá bán vật phẩm
    },

    # ── RARE: Đuôi tắc kè hoa ──
    "467": {
        "extra_slot":  1,              # +1 ô hunt (5 ô tổng)
        "rare_bias":   0.01,           # +1% tỉ lệ ra rare/epic/legendary
        "sell_bonus":  0.05,           # ★ NEW: tăng 5% giá bán vật phẩm
    },

    # ── EPIC: Ngôi sao may mắn ──
    "464": {
        "luck_up":    0.15,            # giảm common 15% (shift sang uncommon)
        "rare_bias":  0.002,           # +2% rare/epic/legendary
        "sell_bonus": 0.1,             # tăng 20% giá bán
    },

    # ── LEGENDARY: Chiếc kéo của Apolo ──
    "466": {
        "double_drop": 0.1,            # 15% nhân đôi item khi hunt
        "rare_bias":   0.03,           # +2.5% rare/epic/legendary
        "sell_bonus":  0.32,           # FIX 1: sell_boost → sell_bonus
    },

    # ── RARE CRATE: Dâu tây ──
    "3696": {
        "sell_bonus": 0.05,            # +5% giá bán
    },

    # ── RARE CRATE: Ấn dấu ──
    "3695": {
        "extra_slot":        1,        # +1 slot
        "sell_bonus":        0.08,     # +5% giá bán
        "reduce_uncommon":   0.05,     # giảm 5% tỉ lệ ra uncommon (item < 50 coin)
    },

    # ── RARE CRATE: Tay gấu ──
    "3706": {
        "extra_slot":    2,            # +2 slot
        "reduce_fail":   0.15,
        "sell_bonus":   0.05,
    },

    # ── RARE CRATE: Gậy giám mục của thánh Nicholas ──
    "3708": {
        "rare_bias":       0.02,       # +4% tỉ lệ ra legend và rare
        "reduce_cooldown": 0.1,        # giảm 30% cooldown
        "sell_bonus":      0.08,       # +5% giá bán
        "reduce_uncommon": 0.15,       # giảm 25% tỉ lệ ra uncommon
    },

    # ── RARE CRATE: Sự cứu rỗi của Hades ──
    "3697": {
        "double_value":    0.2,        # 25% cơ hội x2 giá trị từng item
        "rare_bias":       0.04,       # +6% tỉ lệ ra legend, epic, rare
        "extra_slot":      1,          # +1 slot hunt
        "passive_oneiroi": 0.03,
        "luck_up":   0.18,
        "reduce_fail":   0.1,
        "sell_bonus": 0.1,
    },

    # ── SPECIAL: Domain of Makima ──
    "2509": {
        "extra_slot": 2,              # +3 ô hunt
        "reduce_fail": 0.08,           # Giảm 50% tỉ lệ hunt hụt
        "sell_bonus": 0.05,            # (Tùy chọn) Tăng thêm giá bán item khi hunt
        "luck_up": 0.11
    },

    # ── SPECIAL: Con Bò ──
    "247": {
        "extra_slot": 2,               # +2 slot hunt  (FIX 1: was "extra_slots" — parse_effects uses "extra_slot")
        "rare_bias":  0.03,            # +3% tỉ lệ ra rare legend
        "sell_bonus": 0.10,            # FIX 1: sell_boost → sell_bonus
    },

    # ── SOUL SPECIAL: Tam hoả thống soái ──
    "5001": {
        "sell_bonus": 0.20,            # +20% giá bán
        "luck_up": 0.50,               # +10% luck
        "reduce_fail": 0.1,            # +10% tỉ lệ thành công (gộp 10%+10%)
    },

    # ── SOUL SPECIAL: Hồn giáp bất diệt ──
    "5002": {
        "extra_slot": 3,               # +3 slot hunt
        "sell_bonus": 0.1,             # +5% giá bán
        "reduce_cooldown": 0.3,        # -2% cooldown
    },

    # ── SOUL SPECIAL: Linh diệm sát thần ──
    "5003": {
        "sell_bonus": 0.33,            # +32% giá bán
        "rare_bias": 0.05,             # +5% rare bias
        "luck_up": 0.1,                # +5% luck
    },

    # ── NEW: Dagger blood ──
    "4510": {
        "reduce_fail": 0.05,
        "sell_bonus": 0.03,
    },

    # ── NEW: Áo choàng thương nhân ──
    "4518": {
        "reduce_cooldown": 0.30,
    },

    # ── NEW: Demon eyes ──
    "4511": {
        "sell_bonus": 0.10,
        "extra_slot": 1,
        "luck_up": 0.02,
        "rare_bias": 0.02,
    },

    # ── NEW: Scythes of death ──
    "4529": {
        "reduce_fail": 0.10,
        "sell_bonus": 0.24,
        "luck_up": 0.10,
        "reduce_cooldown": 0.02,
        "rare_bias": 0.05,
    },

    # ── NEW: King of soul ──
    "4541": {
        "double_value": 0.15,
        "double_drop": 0.25,
        "extra_slot": 2,
        "luck_up": 0.35,
        "reduce_fail": 0.15,
        "reduce_cooldown": 0.15,
        "sell_bonus": 0.20,
        "passive_oneiroi": 0.03,
        "rare_bias": 0.05,
    },

    # ── NEW: Đầu lâu bạc ──
    "4509": {
        "extra_slot": 1,
        "reduce_fail": 0.20,
        "sell_bonus": 0.14,
        "luck_up": 0.05,
        "rare_bias": 0.02,
    },
}

# ═══════════════════════════════════════════════════════════
# WEAPON DEFINITIONS
# ═══════════════════════════════════════════════════════════

WEAPONS = [
    {
        "id": "463",
        "name": "Gỗ cổ thụ",
        "emoji": "<:2850:1495250168340156467>",
        "rarity": "common",
        "chance": 60,
        "effects": WEAPON_EFFECTS["463"],
        "description": "Giảm 5% tỉ lệ thất bại khi hunt.",
        "min": 800,
        "max": 1200,
    },
    {
        "id": "465",
        "name": "Tách trà thư giãn",
        "emoji": "<:2863:1495250142364700883>",
        "rarity": "uncommon",
        "chance": 31,
        "effects": WEAPON_EFFECTS["465"],
        "description": "Giảm 15% thất bại hunt, giảm 20% cooldown, tăng 2% giá bán.",
        "min": 1600,
        "max": 2000,
    },
    {
        "id": "467",
        "name": "Đuôi tắc kè hoa",
        "emoji": "<:2861:1495250140326396034>",
        "rarity": "rare",
        "chance": 7,
        "effects": WEAPON_EFFECTS["467"],
        "description": "+1 ô hunt, tăng 1% tỉ lệ ra rare/epic/legendary, tăng 5% giá bán.",
        "min": 28000,
        "max": 31000,
    },
    {
        "id": "464",
        "name": "Ngôi sao may mắn",
        "emoji": "<:2860:1495250148295446540>",
        "rarity": "epic",
        "chance": 3.5,
        "effects": WEAPON_EFFECTS["464"],
        "description": "Giảm 15% common (shift sang uncommon), tăng 0.2% tỉ lệ rare+, tăng 10% giá bán.",
        "min": 2900,
        "max": 3200,
    },
    {
        "id": "466",
        "name": "Chiếc kéo của Apolo",
        "emoji": "<:2856:1495250154696081540>",
        "rarity": "legendary",
        "chance": 1.5,
        "effects": WEAPON_EFFECTS["466"],
        "description": "10% cơ hội nhân đôi item, tăng 3% tỉ lệ rare+, tăng 32% giá bán.",
        "min": 8000,
        "max": 12000,
    },
]

RARE_CRATE_WEAPONS = [
    {
        "id": "3696",
        "name": "Quả dâu tây",
        "emoji": "<:3696:1496187477231145160>",
        "rarity": "uncommon",
        "chance": 65,
        "effects": WEAPON_EFFECTS["3696"],
        "description": "+5% giá bán.",
        "min": 2100,
        "max": 2600,
    },
    {
        "id": "3695",
        "name": "Ấn dấu",
        "emoji": "<:3695:1496187479286481016>",
        "rarity": "rare",
        "chance": 25.5,
        "effects": WEAPON_EFFECTS["3695"],
        "description": "+1 slot, +5% giá bán, giảm 5% tỉ lệ ra uncommon (item < 50 coin).",
        "min": 3200,
        "max": 3500,
    },
    {
        "id": "3706",
        "name": "Tay gấu",
        "emoji": "<:3706:1496188900006432929>",
        "rarity": "epic",
        "chance": 6.5,
        "effects": WEAPON_EFFECTS["3706"],
        "description": "+2 slot, giảm 15% hụt, +5% giá bán.",
        "min": 4600,
        "max": 5300,
    },
    {
        "id": "3708",
        "name": "Gậy giám mục của thánh Nicholas",
        "emoji": "<:3698:1496187467873914962>",
        "rarity": "epic",
        "chance": 3,
        "effects": WEAPON_EFFECTS["3708"],
        "description": "+2% tỉ lệ ra legend và rare, giảm 10% cooldown, +8% giá bán, giảm 15% tỉ lệ ra uncommon.",
        "min": 5100,
        "max": 5550,
    },
    {
        "id": "3697",
        "name": "Sự cứu rỗi của Hades",
        "emoji": "<:3697:1496187472131129486>",
        "rarity": "legend",
        "chance": 1,
        "effects": WEAPON_EFFECTS["3697"],
        "description": "20% cơ hội x2 giá trị từng item, +4% tỉ lệ ra legend/epic/rare, +1 slot hunt, giảm 12% ra common, giảm 10% hụt, +10% sell_bonus. Passive: +3% rơi Cánh Oneiroi.",
        "min": 14500,
        "max": 16700,
    },
]

SPECIAL_WEAPONS = [
    {
        "id": "2509",
        "name": "Domain of Makima",
        "emoji": "<:Makima:1497296773377691848>",
        "rarity": "special",
        "chance": 0,
        "effects": WEAPON_EFFECTS["2509"],
        "description": "Sự chi phối. +3 slot hunt, giảm 8% fail, +5% giá bán, +11% luck_up.",
        "min": 23000,
        "max": 27000,
    },
    {
        "id": "247",
        "name": "Con Bò",
        "description": "Con bò ăn cỏ, bò hư. Một người bạn đồng hành đầy 'thái độ'. +2 slot, +3% rare_bias, +10% sell_bonus",
        "rarity": "special",
        "min": 35000,
        "max": 35000,
        "emoji": "<a:Boooo:1497657966688731198>",
        "effects": WEAPON_EFFECTS["247"],          # FIX 2: was missing — effects were unreachable
    },
    {
        "id": "5001",
        "name": "Tam hoả thống soái (魔火統帥)",
        "emoji": "<a:4574:1499013628672610334>",
        "rarity": "special",
        "chance": 0.3,
        "effects": WEAPON_EFFECTS["5001"],
        "description": "Thống soái hoả linh. Năng lượng rực cháy tăng mạnh hiệu quả kinh tế và hunt.",
        "min": 50000,
        "max": 65000,
    },
    {
        "id": "5002",
        "name": "Hồn giáp bất diệt (魂甲不滅)",
        "emoji": "<a:4572:1499013638319505530>",
        "rarity": "special",
        "chance": 0.3,
        "effects": WEAPON_EFFECTS["5002"],
        "description": "Cự nham hoả linh. Một bộ giáp linh hồn vững chãi giúp mở rộng khả năng chứa đựng.",
        "min": 45000,
        "max": 55000,
    },
    {
        "id": "5003",
        "name": "Linh diệm sát thần (靈焰殺神)",
        "emoji": "<a:4573:1499013635555463198>",
        "rarity": "special",
        "chance": 0.3,
        "effects": WEAPON_EFFECTS["5003"],
        "description": "Linh hoả ngút trời, phá thời hủy địa. Sức mạnh tuyệt đối để tìm kiếm bảo vật.",
        "min": 75000,
        "max": 90000,
    },
]

DARK_CRATE_WEAPON = [
    {
        "id": "4510",
        "name": "Dagger blood",
        "emoji": "<:4510:1498962294539812925>",
        "rarity": "uncommon",
        "chance": 65.75,  # Chiếm 50%
        "effects": WEAPON_EFFECTS["4510"],
        "description": "Dao găm rỉ máu. Giảm 5% thất bại, tăng 3% giá bán.",
        "min": 2500, "max": 3500,
    },
    {
        "id": "4518",
        "name": "Áo choàng thương nhân",
        "emoji": "<:4518:1498962288189640724>",
        "rarity": "rare",
        "chance": 24.9,  # Chiếm 30%
        "effects": WEAPON_EFFECTS["4518"],
        "description": "Chiếc áo choàng cũ. Giảm 30% thời gian chờ hunt.",
        "min": 5000, "max": 7500,
    },
    {
        "id": "4511",
        "name": "Demon eyes",
        "emoji": "<:4511:1498962292530741368>",
        "rarity": "epic",
        "chance": 4,  # Chiếm 10%
        "effects": WEAPON_EFFECTS["4511"],
        "description": "Đôi mắt quỷ quyệt. Tăng 10% giá bán, +1 ô hunt, +2% luck/rare.",
        "min": 12000, "max": 15000,
    },
    {
        "id": "4509",
        "name": "Đầu lâu bạc",
        "emoji": "<:4509:1498962296796483594>",
        "rarity": "epic",
        "chance": 4,  # Chiếm 7%
        "effects": WEAPON_EFFECTS["4509"],
        "description": "Vật phẩm cổ xưa. +1 ô hunt, +20% thành công, +14% giá bán.",
        "min": 13500, "max": 16800,
    },
    {
        "id": "4529",
        "name": "Scythes of death",
        "emoji": "<:4529:1498965085077639218>",
        "rarity": "legendary",
        "chance": 1.25,  # Chiếm 2.9%
        "effects": WEAPON_EFFECTS["4529"],
        "description": "Lưỡi hái tử thần. Chỉ số cực cao, tăng mạnh tỉ lệ rơi đồ hiếm.",
        "min": 25000, "max": 35000,
    },
    {
        "id": "4541",
        "name": "King of soul",
        "emoji": "<a:4541:1498981969227157624>",
        "rarity": "mythical",
        "chance": 0.1,  # Chiếm 0.1%
        "effects": WEAPON_EFFECTS["4541"],
        "description": "Vương quyền linh hồn. Chỉ số tối thượng của một vị vua.",
        "min": 150000, "max": 250000,
    },
]

# ═══════════════════════════════════════════════════════════
# CRATE DEFINITIONS
# ═══════════════════════════════════════════════════════════

CRATES = {
    "001": {
        "name": "Crate Common",
        "emoji": "<:2832:1495000964824826056>",
        "price": 3250,
        "description": (
            "Chứa vũ khí ngẫu nhiên.\n"
            "Tỉ lệ: Gỗ 55% | Tách Trà 27% | Đuôi TKH 10% | Sao MM 6% | Kéo APL 2%"
        ),
    },
    "002": {
        "name": "Crate Rare",
        "emoji": "<:Openrare:1496191896836636873>",
        "price": 8000,
        "rarity": "rare",
        "description": (
            "Chứa vũ khí Rare+.\n"
            "Tỉ lệ: Dâu tây 55% | Ấn dấu 30% | Tay gấu 9% | Gậy giám mục 4.5% | Sự cứu rỗi của Hades 1.5%"
        ),
    },
    "003": {
        "name": "Dark Crate",
        "emoji": "<:Darkcrate:1498988759612657735>",
        "price": 13000,
        "rarity": "epic",
        "description": (
            "Chứa đựng sức mạnh bóng tối và những vũ khí bị nguyền rủa.\n"
            "King of Soul 0.1% | Scythes of Death 2.0% | Đầu lâu bạc 4.5% | Demon Eyes 4.5% | Áo choàng thương nhân 18.9% | 70% Dagger Blood"
        ),
    },
    "004": {
        "name": "Soul Crate",
        "emoji": "<:Soulcrate:1498617031501807646>",
        "price": 32000,
        "rarity": "special",
        "description": (
            "Chứa đựng linh hồn rực cháy.\n"
            "Ma Hỏa Thống Soái 0.3% | Linh Diệm Sát Thần 0,3% | Hồn Giáp Bất Diệt 0,3% | Linh Hoả 35% | 64,4% 2000-6000 Coin"
        ),
    },
}

# ═══════════════════════════════════════════════════════════
# RARITY – màu sắc & nhãn hiển thị (dùng trong embed)
# ═══════════════════════════════════════════════════════════

RARITY_COLOR = {
    "common":    0x9E9E9E,
    "uncommon":  0x4CAF50,
    "rare":      0x2196F3,
    "epic":      0x9C27B0,
    "legendary": 0xFF9800,
    "legend":    0xFF9800,
    "special":   0xFF0040,
    "mythical":  0xff0000,
}

RARITY_LABEL = {
    "common":    " Common",
    "uncommon":  " Uncommon",
    "rare":      " Rare",
    "epic":      " Epic",
    "legendary": " Legendary",
    "legend":    " Legend",
    "special":   " ★ Special",
    "mythical":  "Mythical",
}

# ═══════════════════════════════════════════════════════════
# LOOKUP HELPERS
# ═══════════════════════════════════════════════════════════

def get_weapon_by_id(weapon_id: any) -> dict | None:
    """Tra cứu weapon theo ID. Đã sửa lỗi lệch kiểu dữ liệu int/str."""
    wid_str = str(weapon_id)  # Chuyển ID về dạng chuỗi để so sánh chính xác

    result = next((w for w in WEAPONS if w["id"] == wid_str), None)
    if result is None:
        result = next((w for w in RARE_CRATE_WEAPONS if w["id"] == wid_str), None)
    if result is None:
        result = next((w for w in SPECIAL_WEAPONS if w["id"] == wid_str), None)
    if result is None:
        result = next((w for w in DARK_CRATE_WEAPON if w["id"] == wid_str), None)  # FIX: dark crate weapons
    return result


def get_crate_by_id(crate_id: str) -> dict | None:
    """Tra cứu crate theo ID. Trả về None nếu không tìm thấy."""
    return CRATES.get(crate_id)


# ═══════════════════════════════════════════════════════════
# ROLL ENGINE – CRATE
# ═══════════════════════════════════════════════════════════

def roll_weapon() -> dict:
    """
    Roll ngẫu nhiên 1 weapon từ crate thường dựa trên weighted chance.
    Tổng chance của tất cả weapon = 100.
    """
    roll = random.uniform(0, 100)
    cumulative = 0
    for w in WEAPONS:
        cumulative += w["chance"]
        if roll <= cumulative:
            return w
    return WEAPONS[0]   # fallback: trả weapon đầu tiên


def roll_rare_crate_weapon() -> dict:
    """
    Roll ngẫu nhiên 1 weapon từ Rare Crate dựa trên weighted chance.
    Tổng chance = 100.
    """
    roll = random.uniform(0, 100)
    cumulative = 0
    for w in RARE_CRATE_WEAPONS:
        cumulative += w["chance"]
        if roll <= cumulative:
            return w
    return RARE_CRATE_WEAPONS[0]   # fallback


def roll_dark_crate_weapon() -> dict:
    """
    Roll ngẫu nhiên 1 weapon từ Dark Crate dựa trên weighted chance.
    Tổng chance = 100.
    """
    roll = random.uniform(0, 100)
    cumulative = 0
    for w in DARK_CRATE_WEAPON:
        cumulative += w["chance"]
        if roll <= cumulative:
            return w
    return DARK_CRATE_WEAPON[0]  # fallback


# ═══════════════════════════════════════════════════════════
# COSMETIC CONSTANTS
# ═══════════════════════════════════════════════════════════

COIN_EMOJI = "<:Coin:1495831576397742241>"
ERR        = "<:X_:1495466670616219819>"
OK         = "<:Tick:1495466684520206528>"


def _rarity_tier(rarity: str) -> str:
    return RARITY_LABEL.get(rarity, rarity)


# ═══════════════════════════════════════════════════════════
# PATCH A — BULK SELL + DWI HELPERS
# ═══════════════════════════════════════════════════════════

# ── Effect key → nhãn hiển thị ──────────────────────────
_EFFECT_LABEL: dict[str, str] = {
    "sell_bonus":      "sell_bonus",
    "extra_slot":      "extra_slot",
    "rare_bias":       "rare_bias",
    "luck_up":         "luck_up",
    "reduce_fail":     "reduce_fail",
    "reduce_cooldown": "reduce_cooldown",
    "double_drop":     "double_drop",
    "double_value":    "double_value",
    "reduce_uncommon": "reduce_uncommon",
    "passive_oneiroi": "passive_oneiroi",
}

# Effect dạng số nguyên — không scale, không format %
_EFFECT_INT_KEYS: frozenset = frozenset({"extra_slot"})

# ── Rarity alias map (1 nơi duy nhất, dùng cả T1 lẫn T2) ──
_RARITY_ALIAS: dict[str, str] = {
    "c": "common",    "co": "common",    "common": "common",
    "u": "uncommon",  "uc": "uncommon",  "uncommon": "uncommon",
    "r": "rare",      "ra": "rare",      "rare": "rare",
    "e": "epic",      "ep": "epic",      "epic": "epic",
    "l": "legendary", "le": "legendary", "lend": "legendary",
    "legend": "legendary", "legendary": "legendary",
    "m": "mythical",  "myth": "mythical", "mythical": "mythical",
    "s": "special",   "sp": "special",   "special": "special",
}


def parse_rarity_alias(s: str) -> str | None:
    """Alias → rarity chuẩn. Trả None nếu không hợp lệ."""
    return _RARITY_ALIAS.get(s.lower().strip())


def get_weapon_sell_price(base_id: str, level: int = 1) -> int:
    """
    Tính giá bán theo base_id + level.
    Scale tuyến tính: min (Lv1) → max (Lv50).
    """
    w = get_weapon_by_id(base_id)
    if not w:
        return 0
    min_p = w.get("min", 0)
    max_p = w.get("max", min_p)
    level = max(1, min(level, 50))
    return int(min_p + (max_p - min_p) * (level - 1) / 49)


def get_sell_candidates(
    user: dict,
    *,
    base_id: str | None = None,
    rarity:  str | None = None,
) -> list[dict]:
    """
    Trả về danh sách weapon CÓ THỂ BÁN (không equip).
    Filter bằng base_id hoặc rarity (không dùng cả hai).

    Mỗi phần tử: {"uid", "base_id", "level", "rarity", "price", "name", "emoji"}
    """
    from rpg_core import get_base_id  # lazy import

    equipped_set = {w for w in user.get("equipped", []) if w}
    wi_map = {
        wi["uid"]: wi
        for wi in user.get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    def _norm_rarity(r: str) -> str:
        """Coi "legend" và "legendary" là một."""
        return "legendary" if r == "legend" else r

    candidates = []
    for uid in user.get("weapons", []):
        if uid in equipped_set:
            continue

        b_id = get_base_id(str(uid)) or str(uid)
        w    = get_weapon_by_id(b_id)
        if not w:
            continue

        if base_id is not None and b_id != str(base_id):
            continue

        if rarity is not None:
            if _norm_rarity(w.get("rarity", "common")) != _norm_rarity(rarity):
                continue

        wi    = wi_map.get(uid, {})
        level = wi.get("level", 1)
        candidates.append({
            "uid":     uid,
            "base_id": b_id,
            "level":   level,
            "rarity":  w.get("rarity", "common"),
            "price":   get_weapon_sell_price(b_id, level),
            "name":    w.get("name", b_id),
            "emoji":   w.get("emoji", "⚔️"),
        })

    return candidates


def build_bulk_sell_embed(
    candidates: list[dict],
    *,
    title_extra: str = "",
    color: int = 0xFFA500,
) -> discord.Embed:
    """
    Embed preview danh sách weapon sắp bán.
    Dùng chung cho bulk sell by base_id và by rarity.
    Hiển thị tối đa 15 dòng để tránh vượt giới hạn Discord.
    """
    total   = sum(c["price"] for c in candidates)
    display = candidates[:15]
    hidden  = len(candidates) - len(display)

    lines = []
    for c in display:
        rlabel = RARITY_LABEL.get(c["rarity"], c["rarity"])
        lines.append(
            f"{c['emoji']} **{c['name']}** │ Lv **{c['level']}** │ {rlabel}\n"
            f"-# `{c['uid']}` │ **{c['price']:,}** {COIN_EMOJI}"
        )

    desc = "\n\n".join(lines)
    if hidden > 0:
        desc += f"\n\n-# _... và {hidden} weapon khác_"

    embed = discord.Embed(
        title=f"Xác nhận Bulk Sell{title_extra}",
        description=desc,
        color=color,
    )
    embed.add_field(name="Số lượng", value=f"**{len(candidates)}** weapon", inline=True)
    embed.add_field(name=f"Tổng {COIN_EMOJI}", value=f"**{total:,}** coins", inline=True)
    embed.set_footer(text="Hết hạn sau 30 giây")
    return embed


# ═══════════════════════════════════════════════════════════
# PATCH 2 — calculate_combined_effects
# Skip passive_* khi instance missing; inject sentinel _missing_instance_uids
# ═══════════════════════════════════════════════════════════

def calculate_combined_effects(
    equipped_uids: list,
    wi_map: dict,
    get_base_id_fn,
) -> dict[str, float | int]:
    """
    Tính tổng effect của tất cả weapon đang equip (có scale theo level).

    Safety rules:
    - Weapons whose instance record is absent in wi_map are computed at Lv1 scale
      but their passive_* effects are EXCLUDED — a passive cannot be active without
      a live instance record.
    - A "_missing_instance_uids" key is injected into the result so callers can
      display a warning if any equipped weapon has no instance record.
    """
    combined: dict[str, float | int] = {}
    missing_uids: list[str] = []

    for uid in equipped_uids:
        if not uid:
            continue
        b_id    = get_base_id_fn(str(uid)) or str(uid)
        w       = get_weapon_by_id(b_id)
        if not w:
            continue
        effects = w.get("effects", {})

        wi = wi_map.get(uid)   # intentionally no default — None means absent
        instance_missing = wi is None
        if instance_missing:
            missing_uids.append(str(uid))
            level = 1           # fallback for non-passive effects only
        else:
            level = wi.get("level", 1)

        scale = round(0.6 + (level - 1) * 0.02857, 3)

        for key, val in effects.items():
            # SAFETY: never include passive effects for missing instances.
            # A passive without a live instance record is not truly active.
            if key.startswith("passive_") and instance_missing:
                continue

            if key in _EFFECT_INT_KEYS:
                combined[key] = int(combined.get(key, 0)) + int(val)
            elif isinstance(val, (int, float)):
                combined[key] = combined.get(key, 0.0) + float(val) * scale

    # Inject sentinel so display layer can show a warning
    if missing_uids:
        combined["_missing_instance_uids"] = missing_uids  # type: ignore[assignment]

    return combined


# ═══════════════════════════════════════════════════════════
# PATCH 1 — _fmt_effects_scaled
# Thêm instance_missing; label passive riêng; mark approx khi absent
# ═══════════════════════════════════════════════════════════

def _fmt_effects_scaled(
    effects: dict,
    level: int,
    *,
    instance_missing: bool = False,
) -> list[str]:
    """
    Format từng effect có scale theo level — dùng trong DWI cho từng weapon.

    instance_missing=True: scale bị ép về Lv1 do không có instance record.
    Passive effects được đánh dấu riêng để phân biệt với regular effects.
    """
    # If the instance record is absent we cannot know the real level.
    # We still render effects but flag them as unverified.
    if instance_missing:
        scale = 0.60   # Lv1 assumed — do NOT claim this is accurate
    else:
        scale = round(0.60 + (level - 1) * 0.02857, 3)

    lines = []
    for key, val in effects.items():
        label = _EFFECT_LABEL.get(key, key)

        # Passive effects require a live, registered instance.
        # If the instance is missing, mark the passive as unverified.
        is_passive = key.startswith("passive_")
        if is_passive and instance_missing:
            lines.append(f"-# ⚠️ {label}: _passive — instance missing, may be inactive_")
            continue

        if key in _EFFECT_INT_KEYS:
            suffix = " _(Lv? — approx)_" if instance_missing else ""
            lines.append(f"-# {label}: **+{int(val)}**{suffix}")
        elif isinstance(val, float):
            suffix = " _(Lv? — approx)_" if instance_missing else ""
            if is_passive:
                lines.append(f"-# 🔮 {label}: **+{val * scale:.1%}**{suffix}")
            else:
                lines.append(f"-# {label}: **+{val * scale:.1%}**{suffix}")
        else:
            lines.append(f"-# {label}: **+{val}**")
    return lines


# ═══════════════════════════════════════════════════════════
# PATCH 3 — _fmt_combined_effects
# Surface _missing_instance_uids sentinel thành warning line
# ═══════════════════════════════════════════════════════════

def _fmt_combined_effects(combined: dict) -> list[str]:
    """
    Format combined effects — style nhỏ, dùng cho phần cuối DWI.

    If calculate_combined_effects injected a "_missing_instance_uids" sentinel,
    surface it as a warning line instead of silently omitting the affected weapons.
    """
    lines = []

    # Surface missing-instance sentinel before effect lines
    missing = combined.get("_missing_instance_uids")
    if missing:
        uid_tags = ", ".join(f"`{u}`" for u in missing)
        lines.append(
            f"-# ⚠️ Instance record not found for: {uid_tags} "
            f"— passive effects excluded, other effects shown at Lv1 scale."
        )

    for key, val in combined.items():
        if key.startswith("_"):
            continue  # skip internal sentinels

        label = _EFFECT_LABEL.get(key, key)
        is_passive = key.startswith("passive_")

        if key in _EFFECT_INT_KEYS:
            lines.append(f"-# {label}: **+{int(val)}**")
        elif isinstance(val, float):
            prefix = "🔮 " if is_passive else ""
            lines.append(f"-# {prefix}{label}: **+{val:.1%}**")
        else:
            lines.append(f"-# {label}: **+{val}**")
    return lines
