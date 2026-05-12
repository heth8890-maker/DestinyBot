"""
===== FILE: rpg_weapon.py =====
Chứa: WEAPONS + WEAPON_EFFECTS + CRATES + hằng số hiển thị + roll/lookup functions.
Không phụ thuộc vào bất kỳ module nào trong project.

⚡ Thay đổi so với bản cũ:
  - Tách trà (465): thêm sell_bonus 2%  → mô tả cập nhật
  - Đuôi tắc kè (467): thêm sell_bonus 5% → mô tả cập nhật
  - ★ Thêm: class RPGWeapon(commands.Cog) + setup()
  - ★ Thêm: Rare Crate (002) + 5 weapons rare crate
  - ★ Sửa: final_legend_bonus = weapon_bonus * 0.45

⚡ Thay đổi v3 (Weapon Identity Layer):
  - weapon_equip / weapon_unequip: dùng WeaponID.parse() thay vì .split("-")
    → CẤM dùng .split("-") ngoài WeaponID (rpg_core.py)

⚡ Fixes (Step 3):
  - FIX 1: WEAPON_EFFECTS["247"] "extra_slots" → "extra_slot" (parse_effects key)
  - FIX 2: Weapon "247" definition now links "effects": WEAPON_EFFECTS["247"]
  - FIX 3: _equipped_display else-branch resolves base_id before get_weapon_by_id
  - FIX 4: give_weapon double-await removed; new_uid → new_id (stack weapon)
  - FIX 5: RPGWeapon.weapon Cog bag display — uid_no_upgrade section added

⚡ Thay đổi v4 (Bulk Sell + DWI):
  - ★ PATCH A: Helpers — _EFFECT_LABEL, _EFFECT_INT_KEYS, _RARITY_ALIAS,
               parse_rarity_alias, get_weapon_sell_price, get_sell_candidates,
               build_bulk_sell_embed, calculate_combined_effects,
               _fmt_effects_scaled, _fmt_combined_effects
  - ★ PATCH B: weapon sell — bulk sell theo base_id hoặc rarity (có confirm View)
  - ★ PATCH C: dwi/dwe — hiển thị 3 weapon equip + combined effects
"""

import random

import discord
from discord.ext import commands
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
        "rare_bias":  0.002,            # +2% rare/epic/legendary
        "sell_bonus": 0.1,            # tăng 20% giá bán
    },

    # ── LEGENDARY: Chiếc kéo của Apolo ──
    "466": {
        "double_drop": 0.1,           # 15% nhân đôi item khi hunt
        "rare_bias":   0.03,          # +2.5% rare/epic/legendary
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
        "reduce_cooldown": 0.1,       # giảm 30% cooldown
        "sell_bonus":      0.08,       # +5% giá bán
        "reduce_uncommon": 0.15,       # giảm 25% tỉ lệ ra uncommon
    },

    # ── RARE CRATE: Sự cứu rỗi của Hades ──
    "3697": {
        "double_value":    0.2,       # 25% cơ hội x2 giá trị từng item
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
        "reduce_fail": 0.1,           # +10% tỉ lệ thành công (gộp 10%+10%)
    },
    # ── SOUL SPECIAL: Hồn giáp bất diệt ──
    "5002": {
        "extra_slot": 3,               # +3 slot hunt
        "sell_bonus": 0.1,            # +5% giá bán
        "reduce_cooldown": 0.3,       # -2% cooldown
    },
    # ── SOUL SPECIAL: Linh diệm sát thần ──
    "5003": {
        "sell_bonus": 0.33,            # +32% giá bán
        "rare_bias": 0.05,             # +5% rare bias
        "luck_up": 0.1,               # +5% luck
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
        "chance": 65.75, # Chiếm 50%
        "effects": WEAPON_EFFECTS["4510"],
        "description": "Dao găm rỉ máu. Giảm 5% thất bại, tăng 3% giá bán.",
        "min": 2500, "max": 3500,
    },
    {
        "id": "4518",
        "name": "Áo choàng thương nhân",
        "emoji": "<:4518:1498962288189640724>",
        "rarity": "rare",
        "chance": 24.9, # Chiếm 30%
        "effects": WEAPON_EFFECTS["4518"],
        "description": "Chiếc áo choàng cũ. Giảm 30% thời gian chờ hunt.",
        "min": 5000, "max": 7500,
    },
    {
        "id": "4511",
        "name": "Demon eyes",
        "emoji": "<:4511:1498962292530741368>",
        "rarity": "epic",
        "chance": 4, # Chiếm 10%
        "effects": WEAPON_EFFECTS["4511"],
        "description": "Đôi mắt quỷ quyệt. Tăng 10% giá bán, +1 ô hunt, +2% luck/rare.",
        "min": 12000, "max": 15000,
    },
    {
        "id": "4509",
        "name": "Đầu lâu bạc",
        "emoji": "<:4509:1498962296796483594>",
        "rarity": "epic",
        "chance": 4, # Chiếm 7%
        "effects": WEAPON_EFFECTS["4509"],
        "description": "Vật phẩm cổ xưa. +1 ô hunt, +20% thành công, +14% giá bán.",
        "min": 13500, "max": 16800,
    },
    {
        "id": "4529",
        "name": "Scythes of death",
        "emoji": "<:4529:1498965085077639218>",
        "rarity": "legendary",
        "chance": 1.25, # Chiếm 2.9%
        "effects": WEAPON_EFFECTS["4529"],
        "description": "Lưỡi hái tử thần. Chỉ số cực cao, tăng mạnh tỉ lệ rơi đồ hiếm.",
        "min": 25000, "max": 35000,
    },
    {
        "id": "4541",
        "name": "King of soul",
        "emoji": "<a:4541:1498981969227157624>",
        "rarity": "mythical",
        "chance": 0.1, # Chiếm 0.1%
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
        "description": ("Chứa đựng sức mạnh bóng tối và những vũ khí bị nguyền rủa.\n"
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
    "special":    0xFF0040,
    "mythical": 0xff0000,

}

RARITY_LABEL = {
    "common":    " Common",
    "uncommon":  " Uncommon",
    "rare":      " Rare",
    "epic":      " Epic",
    "legendary": " Legendary",
    "legend":    " Legend",
    "special":    " ★ Special",
    "mythical":    "Mythical",

}

# ═══════════════════════════════════════════════════════════
# LOOKUP HELPERS
# ═══════════════════════════════════════════════════════════

def get_weapon_by_id(weapon_id: any) -> dict | None:
    """Tra cứu weapon theo ID. Đã sửa lỗi lệch kiểu dữ liệu int/str."""
    wid_str = str(weapon_id) # Chuyển ID về dạng chuỗi để so sánh chính xác

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
# COSMETIC CONSTANTS (dùng cho Cog bên dưới)
# ═══════════════════════════════════════════════════════════

COIN_EMOJI = "<:Coin:1495831576397742241>"
ERR        = "<:X_:1495466670616219819>"
OK         = "<:Tick:1495466684520206528>"


# ═══════════════════════════════════════════════════════════
# SHARED DISPLAY HELPERS (dùng cho RPGWeapon Cog)
# ═══════════════════════════════════════════════════════════

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


def calculate_combined_effects(
    equipped_uids: list,
    wi_map: dict,
    get_base_id_fn,
) -> dict[str, float | int]:
    """
    Tính tổng effect của tất cả weapon đang equip (có scale theo level).
    - float effect: nhân scale = 0.60 + (level-1) * 0.02857
    - int effect (extra_slot): cộng trực tiếp, không scale
    """
    combined: dict[str, float | int] = {}
    for uid in equipped_uids:
        if not uid:
            continue
        b_id    = get_base_id_fn(str(uid)) or str(uid)
        w       = get_weapon_by_id(b_id)
        if not w:
            continue
        effects = w.get("effects", {})
        wi      = wi_map.get(uid, {})
        level   = wi.get("level", 1)
        scale   = round(0.6 + (level - 1) * 0.02857, 3)

        for key, val in effects.items():
            if key in _EFFECT_INT_KEYS:
                combined[key] = int(combined.get(key, 0)) + int(val)
            elif isinstance(val, (int, float)):
                combined[key] = combined.get(key, 0.0) + float(val) * scale

    return combined


def _fmt_effects_scaled(effects: dict, level: int) -> list[str]:
    """Format từng effect có scale theo level — dùng trong DWI cho từng weapon."""
    scale = round(0.60 + (level - 1) * 0.02857, 3)
    lines = []
    for key, val in effects.items():
        label = _EFFECT_LABEL.get(key, key)
        if key in _EFFECT_INT_KEYS:
            lines.append(f"-# {label}: **+{int(val)}**")
        elif isinstance(val, float):
            lines.append(f"-# {label}: **+{val * scale:.1%}**")
        else:
            lines.append(f"-# {label}: **+{val}**")
    return lines


def _fmt_combined_effects(combined: dict) -> list[str]:
    """Format combined effects — style nhỏ, dùng cho phần cuối DWI."""
    lines = []
    for key, val in combined.items():
        label = _EFFECT_LABEL.get(key, key)
        if key in _EFFECT_INT_KEYS:
            lines.append(f"-# {label}: **+{int(val)}**")
        elif isinstance(val, float):
            lines.append(f"-# {label}: **+{val:.1%}**")
        else:
            lines.append(f"-# {label}: **+{val}**")
    return lines


# ═══════════════════════════════════════════════════════════
# COG: WEAPON
# ═══════════════════════════════════════════════════════════

class RPGWeapon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─────────────────────────────────────────────────────────
    # HELPER: build weapon line cho list
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _weapon_line(wid: str, wi_map: dict, equipped_set: set,
                     get_base_id_fn, get_weapon_by_id_fn) -> str:
        base_id = get_base_id_fn(str(wid)) or str(wid)
        w       = get_weapon_by_id_fn(base_id)
        nm      = w["name"]  if w else base_id
        em      = w["emoji"] if w else "⚔️"
        wi      = wi_map.get(wid)
        lv      = wi.get("level", 1) if wi else 1
        eq_tag  = " **[E]**" if wid in equipped_set else ""
        return f"{em} **{nm}**{eq_tag} • Lv {lv}\n`{wid}`"

    # ─────────────────────────────────────────────────────────
    # MAIN: dtn weapon / dtn weapon <uid>
    # ─────────────────────────────────────────────────────────
    @commands.group(name="weapon", aliases=["w"], invoke_without_command=True)
    async def weapon(self, ctx, *, weapon_id: str = None):
        from rpg_core import WeaponID, get_weapon_entity, get_base_id
        from rpg_database import get_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # ── dtn weapon <uid> — chi tiết ──────────────────────────────
        if weapon_id:
            base_id = get_base_id(weapon_id) or weapon_id
            w       = get_weapon_by_id(base_id)
            entity  = get_weapon_entity(user, weapon_id)

            if not entity and not w:
                return await ctx.send(
                    f"{ERR} | Không tìm thấy vũ khí `{weapon_id}`."
                )

            embed = entity.build_embed() if entity else discord.Embed(
                title=f"⚔️ {weapon_id}",
                color=0xE91E63,
            )

            # Trạng thái equip
            equipped     = user.get("equipped", [])
            equip_status = "—"
            for i, wid in enumerate(equipped, 1):
                if wid == weapon_id:
                    equip_status = f"Ô **[{i}]**"
                    break
            embed.add_field(
                name="️ Trạng thái",
                value=equip_status,
                inline=True,
            )

            # Level / EXP
            wi = wi_map.get(weapon_id)
            if wi:
                from rpg_instance import fmt_instance_info
                level    = wi.get("level", 1)
                exp      = wi.get("exp", 0)
                exp_next = wi.get("exp_to_next", 40)
                filled   = int(exp / max(exp_next, 1) * 20)
                bar      = "█" * filled + "░" * (20 - filled)
                pct      = int(exp / max(exp_next, 1) * 100)
                scale    = round(0.60 + (level - 1) * 0.02857, 3)
                cap_note = " _(Max!)_" if level >= 50 else ""
                embed.add_field(
                    name=" Level & EXP",
                    value=(
                        f"**Lv {level}** / 50{cap_note}\n"
                        f"`{bar}` {exp:,} / {exp_next:,} ({pct}%)\n"
                        f"Effect: **{scale:.0%}** base"
                    ),
                    inline=False,
                )
                instance_text = fmt_instance_info(wi)
                if instance_text.strip():
                    embed.add_field(
                        name="⚙️ Chi tiết",
                        value=instance_text,
                        inline=False,
                    )

            # UID + lệnh nhanh
            embed.add_field(
                name=" UID",
                value=f"`{weapon_id}`",
                inline=False,
            )
            embed.add_field(
                name=" Lệnh nhanh",
                value=(
                    f"`dtn weapon equip {weapon_id}`\n"
                    f"`dtn weapon unequip <slot>`"
                ),
                inline=False,
            )
            if w and w.get("rarity") != "special":
                embed.add_field(
                    name="<:Key:1496098633395998740> Tỉ lệ crate",
                    value=f"**{w['chance']}%**",
                    inline=True,
                )
            embed.set_footer(text=f"UID: {weapon_id}")
            return await ctx.send(embed=embed)

        # ── dtn weapon (no args) — danh sách tất cả ─────────────────
        equipped     = user.get("equipped", [None, None, None])
        equipped_set = {w for w in equipped if w}
        all_weapons  = list(equipped_set) + [
            w for w in user.get("weapons", [])
            if w not in equipped_set
        ]

        if not all_weapons:
            return await ctx.send(
                f"<:Hamer:1495462570469888069> **{ctx.author.display_name}** "
                f"chưa có vũ khí nào."
            )

        # Build lines
        lines = []
        for wid in all_weapons:
            lines.append(
                self._weapon_line(
                    wid, wi_map, equipped_set,
                    get_base_id, get_weapon_by_id,
                )
            )

        # Pagination — 5 weapon/trang
        PAGE_SIZE   = 16
        pages       = [lines[i:i+PAGE_SIZE] for i in range(0, len(lines), PAGE_SIZE)]
        total_pages = len(pages)

        def build_embed(page_idx: int) -> discord.Embed:
            e = discord.Embed(
                title=(
                    f"<:Hamer:1495462570469888069> "
                    f"Weapon của {ctx.author.display_name}"
                ),
                description="\n\n".join(pages[page_idx]),
                color=0xE91E63,
            )
            e.set_footer(
                text=(
                    f"Trang {page_idx+1}/{total_pages}  │  "
                    f"[E] = đang trang bị  │  "
                    f"dtn weapon <uid> để xem chi tiết"
                )
            )
            return e

        if total_pages == 1:
            return await ctx.send(embed=build_embed(0))

        # Multi-page
        class WeaponPages(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.page = 0
                self.message = None  # FIX 2: lưu message ref để disable khi timeout

            async def on_timeout(self):  # FIX 2: disable buttons khi timeout
                for child in self.children:
                    child.disabled = True
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải danh sách của bạn.", ephemeral=True
                    )  # FIX 2: ephemeral thay vì silent defer
                self.page = (self.page - 1) % total_pages
                await interaction.response.edit_message(
                    embed=build_embed(self.page)
                )

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải danh sách của bạn.", ephemeral=True
                    )  # FIX 2: ephemeral thay vì silent defer
                self.page = (self.page + 1) % total_pages
                await interaction.response.edit_message(
                    embed=build_embed(self.page)
                )

        view = WeaponPages()  # FIX 2: lưu view để gán message ref
        view.message = await ctx.send(embed=build_embed(0), view=view)

    # ─────────────────────────────────────────────────────────
    # EQUIP
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="equip", aliases=["e","eq"])
    async def weapon_equip(self, ctx, weapon_id: str, slot: int = None):
        from rpg_core import equip_weapon, WeaponID
        from rpg_quest import add_quest_progress
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        base_id, _ = WeaponID.parse(weapon_id)
        w_new      = get_weapon_by_id(base_id)

        # Chặn equip trùng base_id
        for i, wid in enumerate(user.get("equipped", []), 1):
            if wid is None:
                continue
            existing_base, _ = WeaponID.parse(str(wid))
            if existing_base == base_id:
                return await ctx.send(
                    f"{ERR} | Đã có **{w_new['name'] if w_new else base_id}** "
                    f"ở ô [{i}]. Dùng `dtn weapon unequip {i}` trước."
                )

        ok, msg = equip_weapon(user, weapon_id, slot)
        if ok:
            if not save_user(author_uid, user):  # FIX 3: check return value
                return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Thử lại.")
            slot_used = next(
                (i+1 for i, wid in enumerate(user["equipped"])
                 if wid == weapon_id), "?"
            )
            name = w_new["name"] if w_new else weapon_id
            add_quest_progress(ctx.author.id, "weapons_equipped")
            await ctx.send(
                f"{OK} | Đã trang bị **{name}** vào ô **[{slot_used}]**.\n"
                f"-# `{weapon_id}`"
            )
        else:
            await ctx.send(f"{ERR} | {msg}")

    # ─────────────────────────────────────────────────────────
    # UNEQUIP
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="unequip", aliases=["ue", "une"])
    async def weapon_unequip(self, ctx, slot: int):
        from rpg_core import unequip_weapon, WeaponID
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        ok, result = unequip_weapon(user, slot)
        if ok:
            if not save_user(author_uid, user):  # FIX 3: check return value
                return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Thử lại.")
            base_id, _ = WeaponID.parse(result)
            w    = get_weapon_by_id(base_id)
            name = w["name"] if w else result
            await ctx.send(
                f"{OK} | Đã bỏ trang bị ô **[{slot}]**: "
                f"**{name}** về kho.\n-# `{result}`"
            )
        else:
            await ctx.send(f"{ERR} | {result}")

    # ─────────────────────────────────────────────────────────
    # PATCH B — SELL: Bulk sell theo base_id hoặc rarity
    # Cú pháp:
    #   dtn weapon sell <base_id> [<amount>|all]
    #   dtn weapon sell <rarity>  (vd: r, rare, legend, l, epic)
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="sell", aliases=["s"])
    async def weapon_sell(self, ctx, arg1: str = None, arg2: str = None):
        from rpg_database import get_user, save_user

        if arg1 is None:
            return await ctx.send(
                f"{ERR} | **Cú pháp:**\n"
                f"`dtn weapon sell <base_id> <số lượng|all>`\n"
                f"`dtn weapon sell <rarity>`  _(vd: r, rare, legend, l, epic)_"
            )

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        # ── Parse loại sell ──────────────────────────────────
        rarity_target  = None
        base_id_target = None
        amount         = None   # None = bán tất cả

        parsed_rarity = parse_rarity_alias(arg1)

        if parsed_rarity:
            # Sell by rarity — không nhận arg2
            rarity_target = parsed_rarity
            if arg2 is not None:
                return await ctx.send(
                    f"{ERR} | Sell theo rarity không cần thêm tham số. "
                    f"Dùng `dtn weapon sell {arg1}`."
                )

        elif arg1.isdigit():
            # Sell by base_id
            base_id_target = arg1
            if arg2 is None or arg2.lower() == "all":
                amount = None
            elif arg2.isdigit() and int(arg2) > 0:
                amount = int(arg2)
            else:
                return await ctx.send(
                    f"{ERR} | Số lượng không hợp lệ: `{arg2}`.\n"
                    f"Dùng số nguyên dương hoặc `all`."
                )

        else:
            return await ctx.send(
                f"{ERR} | Không nhận ra `{arg1}`.\n"
                f"Nhập **base_id** _(vd: `463`)_ hoặc "
                f"**rarity** _(vd: `rare`, `r`, `legend`)_."
            )

        # ── Lấy candidates (1 lần duy nhất) ─────────────────
        candidates = get_sell_candidates(
            user,
            base_id=base_id_target,
            rarity=rarity_target,
        )

        # ── Safety checks ─────────────────────────────────────
        if not candidates:
            if base_id_target:
                w_info = get_weapon_by_id(base_id_target)
                nm = w_info["name"] if w_info else base_id_target
                return await ctx.send(
                    f"{ERR} | Không có **{nm}** (`{base_id_target}`) nào có thể bán.\n"
                    f"-# _(Có thể đang equip hết hoặc chưa có trong kho)_"
                )
            rlabel = RARITY_LABEL.get(rarity_target, rarity_target)
            return await ctx.send(
                f"{ERR} | Không có weapon **{rlabel}** nào có thể bán."
            )

        # Sort: level thấp → cao, tie-break random
        candidates.sort(key=lambda c: (c["level"], random.random()))

        if amount is not None:
            if amount > len(candidates):
                return await ctx.send(
                    f"{ERR} | Chỉ có **{len(candidates)}** weapon hợp lệ, "
                    f"không đủ **{amount}** để bán."
                )
            candidates = candidates[:amount]

        # ── Build embed preview ──────────────────────────────
        if base_id_target:
            w_info   = get_weapon_by_id(base_id_target)
            nm       = w_info["name"] if w_info else base_id_target
            color    = RARITY_COLOR.get(
                w_info.get("rarity", "common"), 0xFFA500
            ) if w_info else 0xFFA500
            title_ex = f"\n{nm} (ID: {base_id_target})"
        else:
            color    = RARITY_COLOR.get(rarity_target, 0xFFA500)
            title_ex = f"\n{RARITY_LABEL.get(rarity_target, rarity_target)}"

        embed = build_bulk_sell_embed(candidates, title_extra=title_ex, color=color)

        # ── Confirm View ──────────────────────────────────────
        class ConfirmSellView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)
                self_v.confirmed = None
                self_v.message   = None

            async def on_timeout(self_v):
                for child in self_v.children:
                    child.disabled = True
                try:
                    await self_v.message.edit(
                        content="⏰ Hết thời gian — đã huỷ.",
                        embed=None, view=self_v,
                    )
                except Exception:
                    pass

            @discord.ui.button(
                emoji=discord.PartialEmoji.from_str("<:Tick:1495466684520206528>"),
                label="Xác nhận",
                style=discord.ButtonStyle.success,
            )
            async def btn_confirm(self_v, interaction: discord.Interaction, _btn):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải lệnh của bạn.", ephemeral=True
                    )
                self_v.confirmed = True
                self_v.stop()
                await interaction.response.defer()

            @discord.ui.button(
                emoji=discord.PartialEmoji.from_str("<:X_:1495466670616219819>"),
                label="Huỷ",
                style=discord.ButtonStyle.danger,
            )
            async def btn_cancel(self_v, interaction: discord.Interaction, _btn):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải lệnh của bạn.", ephemeral=True
                    )
                self_v.confirmed = False
                self_v.stop()
                await interaction.response.defer()

        view         = ConfirmSellView()
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

        # ── Huỷ ──────────────────────────────────────────────
        if not view.confirmed:
            for child in view.children:
                child.disabled = True
            return await view.message.edit(
                content=f"{ERR} | Đã huỷ bán.", embed=None, view=view,
            )

        # ── Thực hiện bán (re-fetch tránh race condition) ─────
        user, _ = get_user(author_uid)

        current_bag = set(user.get("weapons", []))
        sold_uids   = {c["uid"] for c in candidates if c["uid"] in current_bag}

        if not sold_uids:
            return await view.message.edit(
                content=f"{ERR} | Weapon đã không còn trong kho.",
                embed=None, view=None,
            )

        actual_total = sum(c["price"] for c in candidates if c["uid"] in sold_uids)

        user["weapons"] = [
            w for w in user.get("weapons", []) if w not in sold_uids
        ]
        user["weapon_instances"] = [
            wi for wi in user.get("weapon_instances", [])
            if not (isinstance(wi, dict) and wi.get("uid") in sold_uids)
        ]
        user["cash"] = user.get("cash", 0) + actual_total

        if not save_user(author_uid, user):
            return await view.message.edit(
                content=f"{ERR} | Lỗi lưu dữ liệu — không có gì bị bán.",
                embed=None, view=None,
            )

        for child in view.children:
            child.disabled = True

        await view.message.edit(
            content=(
                f"{OK} | Đã bán **{len(sold_uids)}** weapon "
                f"— nhận **{actual_total:,}** {COIN_EMOJI}"
            ),
            embed=None, view=view,
        )

    # ─────────────────────────────────────────────────────────
    # GIVE WEAPON (admin)
    # ─────────────────────────────────────────────────────────
    @commands.command(name="givew")
    @commands.is_owner()
    async def give_weapon(self, ctx, member: discord.Member, weapon_id: str):
        from rpg_core import add_weapon, get_weapon_entity
        from rpg_database import get_user, save_user

        w = get_weapon_by_id(weapon_id)
        if not w:
            return await ctx.send(
                f"{ERR} | Không tìm thấy vũ khí ID `{weapon_id}`."
            )

        user, _ = get_user(str(member.id))
        new_uid = add_weapon(user, weapon_id)
        save_user(str(member.id), user)

        entity       = get_weapon_entity(user, new_uid)
        rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)
        display_name = entity.fmt_name() if entity else f"`{new_uid}`"
        stats_value  = entity.fmt_stats() if entity else "—"

        embed = discord.Embed(
            title=f"{OK} | Trao tặng vũ khí",
            description=(
                f"**Creator** đã trao cho {member.mention}:\n"
                f"{display_name}"
            ),
            color=rarity_color,
        )
        embed.add_field(
            name="<:Effect:1495466103047061679> Chỉ số",
            value=stats_value,
            inline=False,
        )
        embed.add_field(
            name=" Mô tả",
            value=w.get("description", "—"),
            inline=False,
        )
        embed.add_field(
            name="<:Key:1496098633395998740> Độ hiếm",
            value=_rarity_tier(w.get("rarity", "common")),
            inline=True,
        )
        embed.add_field(
            name="UID",
            value=f"`{new_uid}`",
            inline=False,
        )
        embed.set_footer(text=f"Trao bởi {ctx.author}")
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────
    # WID — danh sách text để copy ID
    # ─────────────────────────────────────────────────────────
    @commands.command(name="wid")
    async def weapon_id_list(self, ctx):
        from rpg_core import get_base_id
        from rpg_database import get_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        wi_map       = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }
        equipped     = user.get("equipped", [])
        equipped_set = {w for w in equipped if w}

        lines = [f"**=== VŨ KHÍ CỦA {ctx.author.display_name.upper()} ===**"]

        all_weapons = list(equipped_set) + [
            w for w in user.get("weapons", []) if w not in equipped_set
        ]

        if not all_weapons:
            lines.append("_(Không có vũ khí)_")
        else:
            for wid in all_weapons:
                base_id = get_base_id(str(wid)) or str(wid)
                w       = get_weapon_by_id(base_id)
                nm      = w["name"]  if w else base_id
                em      = w["emoji"] if w else "⚔️"
                wi      = wi_map.get(wid)
                lv      = wi.get("level", 1) if wi else 1
                eq_tag  = " [EQUIPPED]" if wid in equipped_set else ""
                lines.append(f"{em} **{nm}** Lv{lv}{eq_tag}")
                lines.append(f"`{wid}`")

        full_text = "\n".join(lines)
        parts = [full_text[i:i+1900] for i in range(0, len(full_text), 1900)]
        for part in parts:
            await ctx.send(part)

    # ─────────────────────────────────────────────────────────
    # PATCH C — DWI / DWE: Hiển thị 3 weapon đang equip
    #           + combined effects
    # ─────────────────────────────────────────────────────────
    @commands.command(name="wi", aliases=["myw", "myweapon"])
    async def display_weapon_info(self, ctx):
        from rpg_core import get_base_id, get_weapon_entity
        from rpg_database import get_user
        from rpg_instance import fmt_instance_info

        author_uid   = str(ctx.author.id)
        user, _      = get_user(author_uid)
        equipped_raw = user.get("equipped", [None, None, None])

        if not any(equipped_raw):
            return await ctx.send(
                f"<:Hamer:1495462570469888069> "
                f"**{ctx.author.display_name}** chưa trang bị weapon nào.\n"
                f"-# Dùng `dtn weapon equip <uid>` để trang bị."
            )

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        embed = discord.Embed(
            title=(
                f"<:Hamer:1495462570469888069> "
                f"Weapon trang bị — {ctx.author.display_name}"
            ),
            color=0xE91E63,
        )

        for slot_idx, uid in enumerate(equipped_raw[:3], 1):
            slot_header = f"Ô [{slot_idx}]"

            if not uid:
                embed.add_field(
                    name=slot_header,
                    value="`🔲 None`",
                    inline=False,
                )
                continue

            b_id  = get_base_id(str(uid)) or str(uid)
            w     = get_weapon_by_id(b_id)
            wi    = wi_map.get(uid, {})
            level = wi.get("level", 1)

            if not w:
                embed.add_field(
                    name=slot_header,
                    value=f"`{uid}`\n-# Không tìm thấy dữ liệu weapon",
                    inline=False,
                )
                continue

            rarity       = w.get("rarity", "common")
            rlabel       = RARITY_LABEL.get(rarity, rarity)
            em           = w.get("emoji", "⚔️")
            nm           = w.get("name", b_id)
            effects      = w.get("effects", {})
            effect_lines = _fmt_effects_scaled(effects, level)

            exp      = wi.get("exp", 0)
            exp_next = wi.get("exp_to_next", 40)
            filled   = int(exp / max(exp_next, 1) * 10)
            bar      = "█" * filled + "░" * (10 - filled)

            field_parts = [
                f"{em} **{nm}** — {rlabel}",
                f"Lv **{level}** / 50 │ `{bar}` {exp}/{exp_next} EXP",
                f"-# `{uid}`",
                fmt_instance_info(wi),
            ]
            if effect_lines:
                field_parts.extend(effect_lines)

            embed.add_field(
                name=slot_header,
                value="\n".join(field_parts),
                inline=False,
            )

        # ── Combined Effects ──────────────────────────────────
        combined = calculate_combined_effects(equipped_raw, wi_map, get_base_id)
        if combined:
            combined_lines = _fmt_combined_effects(combined)
            embed.add_field(
                name="Tổng hiệu ứng",
                value="\n".join(combined_lines),
                inline=False,
            )

        embed.set_footer(
            text=(
                "dtn weapon <uid> để xem chi tiết  •  "
                "dtn weapon equip/unequip"
            )
        )
        await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGWeapon(bot))
