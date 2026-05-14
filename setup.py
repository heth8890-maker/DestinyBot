import discord
import os

from database_helper import _get_collections, _with_retry

ICON_COIN = "<:Coin:1495831576397742241>"


# ───────────────────────────────────────────
# VIEW CHUYỂN TRANG TOP
# ───────────────────────────────────────────
class TopView(discord.ui.View):
    def __init__(self, bot, ctx):
        super().__init__(timeout=60)
        self.bot = bot
        self.ctx = ctx
        self.mode = "money"

    def get_top_money_embed(self):
        """
        Query MongoDB trực tiếp, sort theo cash DESC, lấy top 10 thật sự.
        Bất kỳ ai có cash cao nhất đều được xét — không bị giới hạn cứng.
        """
        try:
            economy_col, _ = _get_collections()

            # Sort server-side theo cash giảm dần, lấy top 10
            cursor = _with_retry(
                economy_col.find,
                {},
                {"_id": 1, "cash": 1},
            )
            # Lọc bỏ document không có cash hợp lệ, sort tại client
            # (dùng sort() của pymongo để chắc chắn đúng thứ tự)
            cursor = cursor.sort("cash", -1).limit(10)
            top_10 = list(cursor)

        except Exception as e:
            embed = discord.Embed(
                title="🏆 | BẢNG XẾP HẠNG BAL",
                description=f"❌ Không thể tải dữ liệu: {e}",
                color=0xf1c40f,
            )
            return embed

        embed = discord.Embed(
            title="🏆 | BẢNG XẾP HẠNG BAL",
            color=0xf1c40f,
        )

        if not top_10:
            embed.description = "Chưa có dữ liệu."
        else:
            description = ""
            for i, doc in enumerate(top_10, 1):
                uid = doc["_id"]
                bal = doc.get("cash", 0) or 0
                try:
                    user_obj = self.bot.get_user(int(uid))
                except (ValueError, TypeError):
                    user_obj = None
                name = user_obj.name if user_obj else f"User ID: {uid}"
                description += f"**#{i}. {name}** — `{bal:,}` {ICON_COIN}\n"
            embed.description = description

        embed.set_footer(
            text=f"Yêu cầu bởi {self.ctx.author.name}",
            icon_url=self.ctx.author.display_avatar.url,
        )
        return embed

    def get_top_level_embed(self):
        data = load_exp()
        users = []

        for uid, val in data.items():
            if isinstance(val, dict):
                xp = val.get("xp", 0)
                lvl = val.get("level", 1)
                users.append((uid, xp, lvl))

        users.sort(key=lambda x: x[1], reverse=True)
        top_10 = users[:10]

        embed = discord.Embed(
            title="🏆 | BẢNG XẾP HẠNG LEVEL",
            color=0x3498DB,
        )

        description = ""
        for i, (uid, xp, lvl) in enumerate(top_10, 1):
            user_obj = self.bot.get_user(int(uid))
            name = user_obj.name if user_obj else f"User ID: {uid}"
            description += f"**#{i}. {name}** — `Lv.{lvl}`\n└ *{xp:,} xp*\n"

        embed.description = description if description else "Chưa có dữ liệu."
        embed.set_footer(
            text=f"Yêu cầu bởi {self.ctx.author.name}",
            icon_url=self.ctx.author.display_avatar.url,
        )
        return embed

    @discord.ui.button(label="Xem Level", style=discord.ButtonStyle.primary)
    async def switch_top(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Đây không phải bảng của bạn!",
                ephemeral=True,
            )

        if self.mode == "money":
            self.mode = "level"
            button.label = "Xem Tiền"
            embed = self.get_top_level_embed()
        else:
            self.mode = "money"
            button.label = "Xem Level"
            embed = self.get_top_money_embed()

        await interaction.response.edit_message(embed=embed, view=self)


# ───────────────────────────────────────────
# COG RELOAD
# ───────────────────────────────────────────
class ReloadCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reloadf")
    @commands.is_owner()
    async def reload(self, ctx, target: str = None):
        """
        dtn reload <file>
        dtn reload all
        """
        if not target:
            return await ctx.send("❌ | Dùng: `dtn reload <file>` hoặc `dtn reload all`")

        if target.lower() == "all":
            success = []
            failed = []

            for ext in list(self.bot.extensions.keys()):
                try:
                    await self.bot.reload_extension(ext)
                    success.append(ext)
                except Exception as e:
                    failed.append(f"{ext} → {e}")

            embed = discord.Embed(title="🔄 Reload All Extensions", color=0x00FFCC)
            embed.add_field(name="✅ Success", value="\n".join(success) if success else "None", inline=False)
            embed.add_field(name="❌ Failed", value="\n".join(failed) if failed else "None", inline=False)
            return await ctx.send(embed=embed)

        try:
            await self.bot.reload_extension(target)
            embed = discord.Embed(
                title="🔄 Reload Success",
                description=f"Đã reload `{target}` thành công.",
                color=0x2ECC71,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Reload Failed",
                description=f"Không thể reload `{target}`",
                color=0xE74C3C,
            )
            embed.add_field(name="Error", value=str(e), inline=False)
            await ctx.send(embed=embed)


# ───────────────────────────────────────────
# COG CHÍNH
# ───────────────────────────────────────────
class RPG(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="top")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def top(self, ctx):
        view = TopView(self.bot, ctx)
        embed = view.get_top_money_embed()
        await ctx.send(embed=embed, view=view)


# ───────────────────────────────────────────
# SETUP
# ───────────────────────────────────────────
async def setup(bot):
    await bot.add_cog(RPG(bot))
    await bot.add_cog(ReloadCog(bot))
