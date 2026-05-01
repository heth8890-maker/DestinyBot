import discord
from discord.ext import commands

INFO_ICON = "<:Info:1496098636247863491>"

HELP_DATA = {
    "weapon": {
        "title": "WEAPON (VŨ KHÍ)",
        "desc": "Hệ thống vũ khí và nâng cấp",
        "commands": [
            "•dtn weapon - Xem danh sách vũ khí",
            "•dtn weapon <id> - Xem chi tiết vũ khí",
            "•dtn status <id> - Xem chỉ số nâng cấp",
            "•dtn upgrade <id> <effect> - Nâng cấp hiệu ứng",
            "•dtn wid - Xem ID vũ khí trong kho",
            "•dtn weapon equip <id> <slot> - Trang bị vũ khí",
            "•dtn weapon unequip <slot> - Tháo vũ khí",
            "•dtn sell weapon <id> - Bán vũ khí",
            "•dtn shop weapon - Cửa hàng vũ khí",
            "•dtn shopbuy <slot> - Mua đồ trong shop weapon",
        ]
    },
    "item": {
        "title": "ITEM & CRATE (VẬT PHẨM & HÒM)",
        "desc": "Kho đồ và hòm vật phẩm",
        "commands": [
            "•dtn inv - Xem kho",
            "•dtn shop item - Xem giá vật phẩm",
            "•dtn shop crate - Cửa hàng hòm",
            "•dtn crate buy <id> - Mua hòm",
            "•dtn crate open <id> - Mở hòm",
        ]
    },
    "hunt": {
        "title": "HUNT & CATCH (SĂN BẮN)",
        "desc": "Hoạt động săn và thu thập",
        "commands": [
            "•dtn hunt - Đi săn",
            "•dtn catch - Nhặt vật phẩm",
            "•dtn sell all - Bán vật phẩm thường",
            "•dtn marketp - Shop Potion",
            "•dtn dp <id> - Dùng Potion",
        ]
    },
    "trade": {
        "title": "TRADE (GIAO DỊCH)",
        "desc": "Giao dịch giữa người chơi",
        "commands": [
            "•dtn trade <user> - Gửi yêu cầu",
            "•dtn trade accept - Đồng ý",
            "•dtn trade cancel - Huỷ",
            "•dtn add weapon/item <id> - Thêm đồ",
            "•dtn remove weapon/item <id> - Xoá đồ",
            "•dtn trade give <money> - Thêm tiền",
        ]
    },
    "pet": {
        "title": "PET (THÚ CƯNG)",
        "desc": "Hệ thống thú cưng",
        "commands": [
            "•dtn shopegg - Shop trứng",
            "•dtn openegg - Mở trứng",
            "•dtn mypet - Xem pet",
            "•dtn pet <id> - Xem pet",
            "•dtn shopf - Shop thức ăn",
            "•dtn feed <food> <pet> - Cho ăn",
        ]
    },
    "eco": {
        "title": "ECONOMY & PROFILE",
        "desc": "Tiền tệ và thông tin",
        "commands": [
            "•dtn bal - Kiểm tra tiền",
            "•dtn top - BXH",
            "•dtn pay <user> <money> - Chuyển tiền",
            "•dtn profile - Hồ sơ",
            "•dtn bg shop - Shop background",
            "•dtn bg buy/check/equip <id> - Background",
        ]
    },
    "casino": {
        "title": "CASINO",
        "desc": "Trò chơi may rủi",
        "commands": [
            "•dtn bj <bet/all> - Blackjack",
            "•dtn spin <bet/all> - Roulette",
            "•dtn slot <bet/all> - Slot",
        ]
    },
    "event": {
        "title": "EVENT",
        "desc": "Sự kiện đặc biệt",
        "commands": [
            "•dtn event - Thông tin",
            "•dtn echeck - Nhận quà",
            "•dtn eshop - Shop event",
            "•dtn ebuy <id> - Mua đồ event",
        ]
    }
}

MAX_PER_PAGE = 8


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def build_embed(self, ctx, title, desc, commands_list, page=1):
        pages = [
            commands_list[i:i + MAX_PER_PAGE]
            for i in range(0, len(commands_list), MAX_PER_PAGE)
        ]

        page = max(1, min(page, len(pages)))
        chunk = pages[page - 1]

        embed = discord.Embed(
            title=f"{INFO_ICON} {title} (Page {page}/{len(pages)})",
            description=desc,
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Commands",
            value="\n".join(chunk),
            inline=False
        )

        if ctx.bot.user:
            embed.set_thumbnail(
                url=ctx.bot.user.avatar.url if ctx.bot.user.avatar else None
            )

        embed.set_footer(text="•dtn RPG Help System")
        return embed

    @commands.command(name="help")
    async def help(self, ctx, category: str = None, page: int = 1):

        if category is None:
            embed = discord.Embed(
                title=f"{INFO_ICON} •dtn HELP MENU",
                description="Danh sách chủ đề lệnh. Dùng: •dtn help <chủ đề>",
                color=discord.Color.blue()
            )

            for key, data in HELP_DATA.items():
                embed.add_field(
                    name=data["title"],
                    value=f"•dtn help {key}",
                    inline=False
                )

            if ctx.bot.user:
                embed.set_thumbnail(url=ctx.bot.user.avatar.url if ctx.bot.user.avatar else None)

            return await ctx.send(embed=embed)

        category = category.lower()

        if category not in HELP_DATA:
            return await ctx.send(f"{INFO_ICON} Không tìm thấy chủ đề `{category}`")

        data = HELP_DATA[category]
        embed = self.build_embed(ctx, data["title"], data["desc"], data["commands"], page)

        await ctx.send(embed=embed)

    @commands.command(name="addrule")
    async def addrule(self, ctx, category: str, *, command: str):
        category = category.lower()

        if category not in HELP_DATA:
            return await ctx.send(f"{INFO_ICON} Chủ đề không tồn tại")

        HELP_DATA[category]["commands"].append(command)
        await ctx.send(f"{INFO_ICON} Đã thêm lệnh vào `{category}`")

    @commands.command(name="removerule")
    async def removerule(self, ctx, category: str, *, command: str):
        category = category.lower()

        if category not in HELP_DATA:
            return await ctx.send(f"{INFO_ICON} Chủ đề không tồn tại")

        try:
            HELP_DATA[category]["commands"].remove(command)
            await ctx.send(f"{INFO_ICON} Đã xoá lệnh khỏi `{category}`")
        except ValueError:
            await ctx.send(f"{INFO_ICON} Không tìm thấy lệnh trong `{category}`")


async def setup(bot):
    await bot.add_cog(Help(bot))