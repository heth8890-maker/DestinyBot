"""
===== FILE: rpg_item.py =====
Chứa: Định nghĩa ITEMS + BASE_RARITY_RATES + helper functions liên quan item
Không phụ thuộc vào bất kỳ module nào trong project.
"""

import random

# ═══════════════════════════════════════════════════════════
# ITEM DEFINITIONS
# Rarity: common / uncommon / rare / epic / legendary / special / ancient
# ═══════════════════════════════════════════════════════════

ITEMS = [
    # ── COMMON ──
    {
        "id": "001",
        "name": "Cành cây",
        "emoji": "<:2849:1495250166352183347>",
        "rarity": "common",
        "min": 2,
        "max": 5,
        "drop_chance": 0.75,
    },
    {
        "id": "002",
        "name": "Dừa",
        "emoji": "<:2857:1495250150334005390>",
        "rarity": "common",
        "min": 5,
        "max": 8,
        "drop_chance": 0.15,
    },
    {
        "id": "004",
        "name": "Quả trứng",
        "emoji": "<:2852:1495250159037317180>",
        "rarity": "common",
        "min": 8,
        "max": 13,
        "drop_chance": 0.08,
        "special": "egg",          # cơ chế egg: auto spawn 1-4 khi hunt
    },
    {
        "id": "3018",
        "name": "Mảnh hoá thạch",
        "emoji": "<:3018:1495434782656434366>",
        "rarity": "common",
        "min": 15,
        "max": 21,
        "drop_chance": 0.05,
    },

    # ── UNCOMMON ──
    {
        "id": "3020",
        "name": "Vỏ sò",
        "emoji": "<:3020:1495434785575800872>",
        "rarity": "uncommon",
        "min": 12,
        "max": 16,
        "drop_chance": 0.07,
    },

    # ── RARE ──
    {
        "id": "003",
        "name": "Da thú",
        "emoji": "<:2851:1495250164116492469>",
        "rarity": "rare",
        "min": 7,
        "max": 15,
        "drop_chance": 0.3,
    },
    {
        "id": "2858",
        "name": "Đá phản quang",
        "emoji": "<:2858:1495250144331825312>",
        "rarity": "rare",
        "min": 22,
        "max": 28,
        "drop_chance": 0.09,
    },
    {
        "id": "3021",
        "name": "Đá quý Browndust",
        "emoji": "<:3021:1495434779942850741>",
        "rarity": "rare",
        "min": 40,
        "max": 70,
        "drop_chance": 0.01,
        "rare_multiplier": 1.5,
    },
    {
        "id": "3019",
        "name": "Càng cua biển sâu",
        "emoji": "<:3019:1495434788360814672>",
        "rarity": "rare",
        "min": 16,
        "max": 22,
        "drop_chance": 0.07,
    },
    {
        "id": "2855",
        "name": "Nanh rắn hổ mang",
        "emoji": "<:2855:1495250151953006767>",
        "rarity": "rare",
        "min": 32,
        "max": 46,
        "drop_chance": 0.06,
    },
    {
        "id": "3700",
        "name": "Nhũ hoa mật",
        "emoji": "<:3700:1496187469945634978>",
        "rarity": "rare",
        "min": 53,
        "max": 71,
        "drop_chance": 0.05,
    },

    # ── EPIC ──
    {
        "id": "3023",
        "name": "Lông vũ thiên điểu",
        "emoji": "<:3023:1495434778671845546>",
        "rarity": "epic",
        "min": 1960,
        "max": 2850,
        "drop_chance": 0.0009,
    },
    {
        "id": "3702",
        "name": "Chào mào bật sắc",
        "emoji": "<:3702:1496187462676905985>",
        "rarity": "epic",
        "min": 1645,
        "max": 1870,
        "drop_chance": 0.0008,
    },
    {
        "id": "3703",
        "name": "Mảnh Vẩy của Leviathan",
        "emoji": "<:3703:1496187460516974682>",
        "rarity": "epic",
        "min": 2975,
        "max": 3430,
        "drop_chance": 0.0006,
    },

    # ── LEGENDARY ──
    {
        "id": "2859",
        "name": "Đầu lâu của vulture",
        "emoji": "<:2859:1495250145942704189>",
        "rarity": "legendary",
        "min": 3500,
        "max": 4520,
        "drop_chance": 0.0001,
    },
    {
        "id": "2862",
        "name": "Nước bọt Hydra",
        "emoji": "<:2862:1495250137516081222>",
        "rarity": "legendary",
        "min": 6500,
        "max": 8560,
        "drop_chance": 0.00006,
    },
    {
        "id": "3704",
        "name": "Đôi cánh của Oneiroi",
        "emoji": "<:3704:1496187457727889468>",
        "rarity": "legendary",
        "min": 7100,
        "max": 8350,
        "drop_chance": 0.00004,
    },
    {
        "id": "3701",
        "name": "Vây lưng của giao long",
        "emoji": "<:3701:1496187465491419366>",
        "rarity": "legendary",
        "min": 9650,
        "max": 13500,
        "drop_chance": 0.00002,
    },
    {
        "id": "5200",
        "name": "Linh hoả",
        "emoji": "<:Linh_hoa:1498614127386562601>",
        "rarity": "rare",
        "min": 400, "max": 1600,
        "drop_chance": 0.005,
    },
        # ── UNCOMMON ──
    {
        "id": "4507",
        "name": "Xương",
        "emoji": "<:4507:1498962301271937114>",
        "rarity": "uncommon",
        "min": 13,
        "max": 15,
        "drop_chance": 0.08,
    },
    {
        "id": "4512",
        "name": "Xương cá",
        "emoji": "<:4512:1498962290341449768>",
        "rarity": "uncommon",
        "min": 14,
        "max": 18,
        "drop_chance": 0.07,
    },
    {
        "id": "4525",
        "name": "Nấm",
        "emoji": "<:4537:1498976393185464342>",
        "rarity": "uncommon",
        "min": 12,
        "max": 16,
        "drop_chance": 0.08,
    },
    {
        "id": "4521",
        "name": "Vỏ sỏ biển",
        "emoji": "<:4538:1498976390513557524>",
        "rarity": "uncommon",
        "min": 21,
        "max": 25,
        "drop_chance": 0.04,  # Tỉ lệ thấp nhất trong Uncommon
    },

    # ── RARE ──
    {
        "id": "4523",
        "name": "Lông đại bàng",
        "emoji": "<:4536:1498976395433480303>",
        "rarity": "rare",
        "min": 26,
        "max": 34,
        "drop_chance": 0.08,
    },
    {
        "id": "4528",
        "name": "Đuôi thú lớn",
        "emoji": "<:4534:1498976399585968158>",
        "rarity": "rare",
        "min": 38,
        "max": 61,
        "drop_chance": 0.06,
    },
    {
        "id": "4526",
        "name": "Sừng tê giác",
        "emoji": "<:4535:1498976397555925094>",
        "rarity": "rare",
        "min": 45,
        "max": 72,
        "drop_chance": 0.05,
    },
    {
        "id": "4508",
        "name": "Quặng vàng",
        "emoji": "<:4508:1498962299065466971>",
        "rarity": "rare",
        "min": 103,
        "max": 153,
        "drop_chance": 0.005, # Tỉ lệ thấp nhất trong Rare
    },

    # ── LEGENDARY ──
    {
        "id": "4506",
        "name": "Cánh tiên",
        "emoji": "<:4506:1498962302991601748>",
        "rarity": "legendary",
        "min": 4500,
        "max": 6000,
        "drop_chance": 0.00008,
    },
    # ── UNCOMMON ──
    {
        "id": "4613",
        "name": "Ổ trứng",
        "emoji": "<:4613:1499452964056600758>",
        "rarity": "uncommon",
        "min": 32,
        "max": 37,
        "drop_chance": 0.05,
    },

    # ── RARE ──
    {
        "id": "4615",
        "name": "Quặng phản quang",
        "emoji": "<:4615:1499452957362491392>",
        "rarity": "rare",
        "min": 45,
        "max": 58,
        "drop_chance": 0.04, # Thấp hơn Đá phản quang (0.09)
    },

    # ── EPIC ──
    {
        "id": "4614",
        "name": "Đá quý biển sâu Larimar",
        "emoji": "<:4614:1499452959677743284>",
        "rarity": "epic",
        "min": 1875,
        "max": 2246,
        "drop_chance": 0.0007,
    },

    # ── LEGENDARY ──
    {
        "id": "4616",
        "name": "Lõi biển sâu",
        "emoji": "<:4616:1499452955240435895>",
        "rarity": "legendary",
        "min": 9450,
        "max": 13450,
        "drop_chance": 0.00002, # Ngang vây lưng giao long
    },
    # ── VẬT PHẨM MỚI THÊM ──
    {
        "id": "5305",
        "name": "Vỏ ốc biển",
        "emoji": "<:5329:1503843371788275862>",
        "rarity": "rare",
        "min": 15,
        "max": 19,
        "drop_chance": 0.05,
    },
    {
        "id": "5304",
        "name": "Tinh thể thạch anh xanh",
        "emoji": "<:5330:1503843369187672104>",
        "rarity": "rare",
        "min": 35,
        "max": 52,
        "drop_chance": 0.0009,
    },

    # ── SPECIAL ──
    # Special: không rớt khi hunt thông thường (drop_chance = 0.0)
    # Chỉ nhận được qua phân rã, sự kiện, hoặc cơ chế đặc biệt khác
    {
        "id": "1099",
        "name": "Enchant shard",
        "emoji": "<:Enchant_shard:1506136888988405782>",
        "rarity": "special",
        "min": 230,
        "max": 320,
        "drop_chance": 0.0,  # Không rớt khi hunt – nhận từ phân rã/sự kiện
    },

    # ── ANCIENT ──
    # Placeholder cho tương lai – thêm item vào đây khi có
]

# ═══════════════════════════════════════════════════════════
# RARITY CONFIG – base rates (%) per hunt slot
# Epic / Legendary chỉ xuất hiện khi có weapon tăng rare_bias
# Special / Ancient không bao giờ xuất hiện khi hunt thông thường
# ═══════════════════════════════════════════════════════════

BASE_RARITY_RATES = {
    "common":    78.5,
    "uncommon":  8.0,
    "rare":      10.5,
    "epic":      1.0,    # locked without weapon effect
    "legendary": 0.0,    # locked without weapon effect
    "special":   0.0,    # không drop khi hunt – chỉ qua cơ chế đặc biệt
    "ancient":   0.0,    # không drop khi hunt – chỉ qua cơ chế đặc biệt
}

# Rarity không bao giờ được chọn trong hunt thông thường
NON_HUNT_RARITIES = {"special", "ancient"}

# Thứ tự hiển thị rarity (thấp → cao)
RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "special", "ancient"]

# Màu embed Discord theo rarity
RARITY_COLORS = {
    "common":    0xAAAAAA,
    "uncommon":  0x55FF55,
    "rare":      0x5555FF,
    "epic":      0xAA00AA,
    "legendary": 0xFFAA00,
    "special":   0xFF4444,
    "ancient":   0x00FFEE,
}

# ═══════════════════════════════════════════════════════════
# ITEM HELPERS
# ═══════════════════════════════════════════════════════════

def get_item_by_id(item_id: str) -> dict | None:
    """Tra cứu item theo ID. Trả về None nếu không tìm thấy."""
    return next((i for i in ITEMS if i["id"] == item_id), None)


def get_items_by_rarity(rarity: str) -> list[dict]:
    """Trả về danh sách tất cả item của một rarity nhất định."""
    return [i for i in ITEMS if i["rarity"] == rarity]


def is_obtainable_from_hunt(item: dict) -> bool:
    """
    Kiểm tra item có thể rớt khi hunt thông thường không.
    Trả về False nếu rarity thuộc NON_HUNT_RARITIES hoặc drop_chance == 0.
    """
    return item["rarity"] not in NON_HUNT_RARITIES and item.get("drop_chance", 0.0) > 0.0


def _pick_item_from_rarity(rarity: str) -> dict | None:
    """
    Chọn ngẫu nhiên 1 item từ pool của rarity nhất định.
    Dùng drop_chance làm weight.
    - Special / Ancient: chỉ có thể gọi trực tiếp (không qua hunt thông thường).
    - Trả về None nếu pool rỗng hoặc tất cả drop_chance == 0.
    """
    pool = [i for i in ITEMS if i["rarity"] == rarity and i.get("drop_chance", 0.0) > 0.0]
    if not pool:
        return None
    weights = [i["drop_chance"] for i in pool]
    return random.choices(pool, weights=weights, k=1)[0]


# ═══════════════════════════════════════════════════════════
# DISCORD COG – thêm mới, không ảnh hưởng phần data bên trên
# ═══════════════════════════════════════════════════════════

import discord
from discord.ext import commands

ERR        = "<:X_:1495466670616219819>"
COIN_EMOJI = "<:Coin:1495831576397742241>"


class RPGItem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="item", invoke_without_command=True)
    async def item_group(self, ctx):
        await ctx.send(
            "<:2851:1495250164116492469> **Lệnh item:**\n"
            "• `dtn item use <id>` — sử dụng vật phẩm\n"
            "• `dtn shop item` — xem danh sách vật phẩm & giá bán"
        )

    @item_group.command(name="use")
    async def item_use(self, ctx, item_id: str):
        from rpg_core import load_data, save_data, get_user, remove_item, handle_egg

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        item = get_item_by_id(item_id)
        if not item:
            return await ctx.send(f"{ERR} | Item ID `{item_id}` không tồn tại.")
        if user["inv"].get(item_id, 0) <= 0:
            return await ctx.send(
                f"{ERR} | Bạn không có {item['emoji']} **{item['name']}** trong kho."
            )

        if item.get("special") == "egg":
            if not remove_item(user, item_id):
                return await ctx.send(f"{ERR} | Không thể sử dụng.")
            eggs = handle_egg(user)
            save_data(data)
            await ctx.send(
                f"🥚 Đã ấp trứng! → Nhận được **{len(eggs)}x** "
                f"{item['emoji']} **{item['name']}**"
            )
        else:
            await ctx.send(
                f"{ERR} | {item['emoji']} **{item['name']}** không thể sử dụng trực tiếp.\n"
                f"Dùng `dtn sell item {item_id}` để bán."
            )


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGItem(bot))
