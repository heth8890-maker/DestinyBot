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
        "sell_boost":  0.32,           # tăng 35% giá bán
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
        "sell_boost": 0.10,            # +10% sell_bonus
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
        "no_upgrade": True,
    },
    {
        "id": "247",
        "name": "Con Bò",
        "description": "Con bò ăn cỏ, bò hư. Một người bạn đồng hành đầy 'thái độ'. +2 slot, +3% rare_bias, +10% sell_boost",
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
        "no_upgrade": True, # Theo logic các món Special thường không nâng cấp
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
        "no_upgrade": True,
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
        "no_upgrade": True,
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


def _equipped_display(equipped: list, user: dict | None = None) -> str:
    """Hiển thị 3 ô trang bị. Hỗ trợ unique upgrade IDs. Hiện ID + gợi ý upgrade."""
    lines   = []
    slots   = list(equipped) + [None] * (3 - len(equipped))
    uw_map  = {uw["uid"]: uw for uw in (user or {}).get("upgraded_weapons", [])}

    for i, wid in enumerate(slots[:3], 1):
        if wid is None:
            lines.append(f"  `[{i}]` — trống")
        elif wid in uw_map:
            uw     = uw_map[wid]
            w      = get_weapon_by_id(uw["base_id"])
            nm     = w["name"] if w else wid
            em     = w["emoji"] if w else "<:Effect:1495466103047061679>"
            max_lv = max(uw["effect_levels"].values()) if uw["effect_levels"] else 1
            lines.append(
                f"  `[{i}]` <:Effect:1495466103047061679>{em} **{nm}** _(lv{max_lv})_\n"
                f"       `{wid}` • `dtn up {wid} <effect>`"
            )
        else:
            # FIX 3: wid may be a UID ("467-ABC12") with no upgraded_weapons entry.
            # get_weapon_by_id("467-ABC12") always returns None because UIDs are not
            # database keys.  Resolve base_id first via get_base_id() before lookup.
            from rpg_core import get_base_id as _get_base_id
            resolved = _get_base_id(wid)
            w = get_weapon_by_id(resolved)
            if w:
                lines.append(
                    f"  `[{i}]` {w['emoji']} **{w['name']}**\n"
                    f"       `{wid}` • `dtn up {wid} <effect>`"
                )
            else:
                lines.append(f"  `[{i}]` `{wid}`")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# COG: WEAPON  (+status +upgrade)
# ═══════════════════════════════════════════════════════════

class RPGWeapon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="weapon", aliases=["w"], invoke_without_command=True)
    async def weapon(self, ctx, *, weapon_id: str = None):
        from rpg_core import load_data, get_user, WeaponID, get_weapon_entity
        from rpg_addon import get_upgraded_weapon

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        if weapon_id:
            # ── dtn weapon <id> ── chi tiết vũ khí
            w = get_weapon_by_id(weapon_id)
            if not w:
                # Thử là unique ID (vũ khí đã nâng cấp)
                uw = get_upgraded_weapon(user, weapon_id)
                if uw:
                    # Dùng WeaponEntity.build_embed() — single source cho UI
                    entity = get_weapon_entity(user, weapon_id)
                    if entity:
                        embed = entity.build_embed()
                        embed.add_field(
                            name="📋 Lệnh nhanh",
                            value=(
                                f"`dtn weapon equip {uw['uid']}`\n"
                                f"`dtn upgrade {uw['uid']} <effect>`"
                            ),
                            inline=False,
                        )
                        return await ctx.send(embed=embed)
                return await ctx.send(f"{ERR} | Không tìm thấy vũ khí ID `{weapon_id}`.")

            # Weapon bình thường — dùng entity.build_embed()
            entity = get_weapon_entity(user, weapon_id)
            if entity:
                embed = entity.build_embed()
                rarity = w.get("rarity", "common")
                if rarity == "special":
                    chance_text = "**???**"
                else:
                    chance_text = f"**{w['chance']}%**"
                embed.add_field(
                    name="<:Key:1496098633395998740> | Tỉ lệ crate",
                    value=chance_text,
                    inline=True,
                )
                if w.get("no_upgrade"):
                    embed.add_field(name="⚠️ Nâng cấp", value="Không thể nâng cấp.", inline=False)
                embed.add_field(
                    name="📋 Lệnh nhanh",
                    value=(
                        f"`dtn weapon equip {w['id']}`\n"
                        f"`dtn status {w['id']}`"
                    ),
                    inline=False,
                )
                embed.set_footer(text=f"ID: {w['id']}")
                return await ctx.send(embed=embed)
            return  # entity ký không tồn tại — thoát im lặng

        # ── dtn weapon (no id) ── kho
        embed = discord.Embed(
            title=f" <:Hamer:1495462570469888069> Weapon của {ctx.author.display_name}",
            color=0xE91E63,
        )
        embed.add_field(
            name="<:2913:1495252023912956025>️ Đang trang bị (3 ô)",
            value=_equipped_display(user.get("equipped", []), user),
            inline=False,
        )

        # FIX 5: Regular weapons (base ID) + track UID weapons with no upgrade entry.
        # Under v1.7, a UID without an upgrade record is valid.  Old code skipped
        # them in both sections (is_unique gate + no entry to iterate) → invisible.
        uw_uids: set[str] = {
            uw["uid"] for uw in user.get("upgraded_weapons", [])
            if isinstance(uw, dict) and "uid" in uw
        }
        weapon_counts: dict[str, int] = {}
        uid_no_upgrade: list[str]     = []   # UIDs with no upgrade entry yet

        for wid in user.get("weapons", []):
            if not WeaponID.is_unique(str(wid)):
                weapon_counts[wid] = weapon_counts.get(wid, 0) + 1
            elif wid not in uw_uids:
                uid_no_upgrade.append(wid)

        if weapon_counts or uid_no_upgrade:
            bag_lines = []
            for wid, cnt in weapon_counts.items():
                entity = get_weapon_entity(user, wid)
                if entity:
                    stats_str = entity.fmt_stats().replace("• ", "").replace("\n", ", ")
                    bag_lines.append(
                        f"{entity.base_data['emoji']} `{wid}` **{entity.base_data['name']}** x{cnt}\n"
                        f"  ↳ {_rarity_tier(entity.base_data['rarity'])}  |  {stats_str}"
                    )
            # FIX 5: render UID weapons that have no upgrade entry (were invisible before)
            for wid in uid_no_upgrade:
                entity = get_weapon_entity(user, wid)
                if entity:
                    bag_lines.append(
                        f"{entity.base_data['emoji']} `{wid}` **{entity.base_data['name']}** _(chưa nâng cấp)_\n"
                        f"  ↳ {_rarity_tier(entity.base_data['rarity'])}"
                    )
            embed.add_field(
                name="<:Hamer:1495462570469888069> Kho vũ khí",
                value="\n\n".join(bag_lines), inline=False,
            )
        else:
            embed.add_field(
                name="<:Hamer:1495462570469888069> Kho vũ khí",
                value="_Trống_", inline=False,
            )

        if user.get("upgraded_weapons"):
            up_lines = []
            for uw in user["upgraded_weapons"]:
                entity = get_weapon_entity(user, uw["uid"])
                if entity:
                    max_lv = max(uw["effect_levels"].values()) if uw["effect_levels"] else 1
                    up_lines.append(
                        f"<:Effect:1495466103047061679> | {entity.fmt_name()} "
                        f"`{uw['uid']}` _(max lv{max_lv}/30)_"
                    )
            if up_lines:
                embed.add_field(
                    name="<:3057:1495466091319918714> | Vũ Khí Nâng Cấp",
                    value="\n".join(up_lines), inline=False,
                )

        embed.set_footer(
            text="dtn weapon <id>  │  dtn weapon equip <id>  │  dtn upgrade <uid> <effect>"
        )
        await ctx.send(embed=embed)

    @weapon.command(name="equip")
    async def weapon_equip(self, ctx, weapon_id: str, slot: int = None):
        from rpg_core import load_data, save_data, get_user, equip_weapon, unequip_weapon, WeaponID
        from rpg_quest import add_quest_progress

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        # ── Resolve base_id (dùng WeaponID.parse() — KHÔNG dùng .split("-")) ─
        target_base_id, _ = WeaponID.parse(weapon_id)
        w_new      = get_weapon_by_id(target_base_id)
        new_rarity = w_new.get("rarity", "common") if w_new else "common"

        # ── Upgrade-based base_id conflict validation ─────────────────────────
        # Rule: An UPGRADED weapon (UID-based, has upgrade entry) must not share
        #       base_id with ANY currently equipped weapon (upgraded or not).
        #       A NON-UPGRADED weapon may stack freely UNLESS an upgraded weapon
        #       with the same base_id is already equipped.
        #       DO NOT compare UID — ONLY compare base_id.
        new_is_upgraded = WeaponID.is_unique(weapon_id)
        uw_uid_set: set[str] = {
            uw["uid"] for uw in user.get("upgraded_weapons", [])
            if isinstance(uw, dict) and "uid" in uw
        }

        for i, wid in enumerate(user.get("equipped", [])):
            if wid is None:
                continue
            existing_base_id, _ = WeaponID.parse(str(wid))
            if existing_base_id != target_base_id:
                continue  # different base_id — no conflict possible

            existing_is_upgraded = WeaponID.is_unique(str(wid)) and str(wid) in uw_uid_set

            if new_is_upgraded:
                # Upgraded weapon: any same-base_id equipped weapon → block
                return await ctx.send(
                    f"{ERR} | Vũ khí nâng cấp không thể trang bị cùng **base_id** với "
                    f"vũ khí đang ở ô **[{i + 1}]** (`{wid}`)."
                )
            else:
                # Non-upgraded: only block if the conflicting slot holds an upgraded weapon
                if existing_is_upgraded:
                    return await ctx.send(
                        f"{ERR} | Ô **[{i + 1}]** đã có vũ khí nâng cấp `{wid}` "
                        f"cùng base_id — không thể trang bị thêm."
                    )

        ok, msg = equip_weapon(user, weapon_id, slot)
        if ok:
            await save_data(data)
            # Dùng WeaponID.parse() — KHÔNG dùng .split("-")
            base_id, _ = WeaponID.parse(weapon_id)
            w         = get_weapon_by_id(base_id)
            name      = w["name"] if w else weapon_id
            slot_used = next(
                (i + 1 for i, wid in enumerate(user["equipped"]) if wid == weapon_id), "?",
            )
            add_quest_progress(ctx.author.id, "weapons_equipped")
            await ctx.send(f"{OK} | Đã trang bị **{name}** vào ô **[{slot_used}]**.")
        else:
            await ctx.send(f"{ERR} | {msg}")

    @weapon.command(name="unequip")
    async def weapon_unequip(self, ctx, slot: int):
        from rpg_core import load_data, save_data, get_user, unequip_weapon, WeaponID

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        ok, result = unequip_weapon(user, slot)
        if ok:
            await save_data(data)
            # Dùng WeaponID.parse() — KHÔNG dùng .split("-")
            base_id, _ = WeaponID.parse(result)
            w    = get_weapon_by_id(base_id)
            name = w["name"] if w else result
            await ctx.send(f"{OK} | Đã bỏ trang bị ô **[{slot}]**: trả **{name}** về kho.")
        else:
            await ctx.send(f"{ERR} | {result}")

    @commands.command(name="givew")
    @commands.is_owner()
    async def give_weapon(self, ctx, member: discord.Member, weapon_id: str):
        """
        Admin command: dtn givew @user <base_weapon_id>

        Gives a stack weapon (base_id). The recipient can enchant it to get a UID.
        Per project rule: only rpg_enchant is allowed to call make_unique=True.
        """
        from rpg_core import load_data, save_data, get_user, add_weapon, get_weapon_entity

        # ── 1. Validate weapon exists in the database ─────────────────────────
        w = get_weapon_by_id(weapon_id)
        if not w:
            return await ctx.send(f"{ERR} | Không tìm thấy vũ khí ID `{weapon_id}`.")

        # ── 2. Load user data ─────────────────────────────────────────────────
        data   = load_data()
        user   = get_user(str(member.id), data)

        # ── 3. Add weapon to bag (stack weapon — admin gives base_id) ─────────
        new_id = add_weapon(user, weapon_id)

        # ── 4. Persist (save_data is a coroutine — MUST be awaited ONCE) ──────
        # FIX 4: was `await await save_data(data)` (double-await → TypeError)
        await save_data(data)

        # ── 5. Build display via WeaponEntity — single source of truth ────────
        entity       = get_weapon_entity(user, new_id)
        rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)

        display_name = entity.fmt_name() if entity else f"`{new_id}`"
        stats_value  = entity.fmt_stats() if entity else "—"

        # ── 6. Build confirmation embed ───────────────────────────────────────
        embed = discord.Embed(
            title=f"{OK} | Xác nhận trao tặng vũ khí",
            description=(
                f"**Creator** đã trao tặng cho {member.mention} vũ khí:\n"
                f"{display_name}"
            ),
            color=rarity_color,
        )
        embed.add_field(
            name="<:Effect:1495466103047061679> | Chỉ số",
            value=stats_value,
            inline=False,
        )
        embed.add_field(
            name="📖 Mô tả",
            value=w.get("description", "—"),
            inline=False,
        )
        embed.add_field(
            name="<:Key:1496098633395998740> | Độ hiếm",
            value=_rarity_tier(w.get("rarity", "common")),
            inline=True,
        )
        embed.add_field(
            name=f"{COIN_EMOJI} Giá trị",
            value=f"**{w.get('min', 0):,}**",
            inline=True,
        )
        embed.add_field(
            name="📋 ID (tap để copy)",
            value=(
                f"**ID:** `{new_id}`\n"
                f"**Người nhận:** `{member.id}`"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Trao tặng bởi {ctx.author} | Identity Layer v5.5")

        await ctx.send(embed=embed)
    @commands.command(name="wid")
    async def weapon_id_list(self, ctx):
        """
        Hiển thị danh sách vũ khí dưới dạng text thuần để dễ copy ID.
        """
        from rpg_core import load_data, get_user, get_weapon_entity
        
        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)
        
        lines = []
        lines.append(f"**=== DANH SÁCH VŨ KHÍ CỦA {ctx.author.display_name.upper()} ===**")
        
        # 1. Phần đang trang bị
        lines.append("\n**[ ĐANG TRANG BỊ ]**")
        equipped = user.get("equipped", [])
        for i, wid in enumerate(equipped):
            slot_num = i + 1
            if not wid:
                lines.append(f"Slot {slot_num}: (Trống)")
                continue
            
            entity = get_weapon_entity(user, wid)
            if entity:
                # Lấy level cao nhất của các effect (nếu có)
                uw_data = next((item for item in user.get("upgraded_weapons", []) if item["uid"] == wid), None)
                lv_str = f"| Lv.{max(uw_data['effect_levels'].values())}" if uw_data and uw_data.get("effect_levels") else ""
                
                lines.append(f"Slot {slot_num}: {entity.base_data['emoji']} **{entity.base_data['name']}** {lv_str}")
                lines.append(f"`{wid}`") # Để ID ở dòng riêng cho dễ nhấn giữ copy

        # 2. Kho vũ khí (Hàng nâng cấp - Upgraded)
        upgraded = user.get("upgraded_weapons", [])
        if upgraded:
            lines.append("\n**[ VŨ KHÍ NÂNG CẤP ]**")
            for uw in upgraded:
                wid = uw["uid"]
                entity = get_weapon_entity(user, wid)
                if entity:
                    lv = max(uw["effect_levels"].values()) if uw["effect_levels"] else 1
                    stats = entity.fmt_stats().replace('\n', ', ')
                    lines.append(f"● {entity.base_data['emoji']} **{entity.base_data['name']}** (Lv.{lv})")
                    lines.append(f"ID: `{wid}`")
                    lines.append(f"↳ *{stats}*")

        # 3. Kho vũ khí thường (Base items)
        weapon_counts = {}
        for wid in user.get("weapons", []):
            # Chỉ đếm những món không phải unique ID (hàng thường)
            if "-" not in str(wid):
                weapon_counts[wid] = weapon_counts.get(wid, 0) + 1
        
        if weapon_counts:
            lines.append("\n**[ KHO ĐỒ THƯỜNG ]**")
            for wid, count in weapon_counts.items():
                w_base = get_weapon_by_id(wid)
                if w_base:
                    lines.append(f"○ {w_base['emoji']} **{w_base['name']}** (x{count})")
                    lines.append(f"ID: `{wid}`")

        # Phân trang nếu text quá dài (Discord giới hạn 2000 ký tự)
        full_text = "\n".join(lines)
        if len(full_text) > 1900:
            # Chia đôi nếu cần, hoặc gửi thành các đoạn nhỏ
            parts = [full_text[i:i+1900] for i in range(0, len(full_text), 1900)]
            for part in parts:
                await ctx.send(part)
        else:
            await ctx.send(full_text)




# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGWeapon(bot))
