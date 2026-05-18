import time
import discord
from discord import app_commands
from discord.ext import commands

from rpg_core import add_item
from rpg_database import get_user, save_user

EVENT_IMAGE = "IMG_tamlinh.png"
CRATE_ID    = "004"
COOLDOWN    = 4 * 60 * 60   # 4 tiếng (giây)


class Event(commands.Cog):
    def __init__(self, bot):
        self.bot        = bot
        self.start_time = time.time()   # thời điểm bật bot

    # ──────────────────────────────────────────
    # HIỂN THỊ EVENT
    # ──────────────────────────────────────────
    @commands.command(name="event")
    async def event_panel(self, ctx):
        now     = time.time()
        elapsed = int(now - self.start_time)

        hours   = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60

        embed = discord.Embed(
            title="<:Opensoulcrate:1498617029077499935> | Tam Linh Event",
            description=(
                "Sự kiện đang diễn ra!\n"
                f"<:Linh_hoa:1498614127386562601> | Đã bắt đầu được: **{hours}h {minutes}m {seconds}s**\n"
                "Sự kiện này sẽ nhận: x1 crate soul- sau mỗi 4 tiếng, "
                "Dùng `dtn echeck` để nhận thưởng!\n\n"
                "<a:Tamhoathong:1498612536470409328> | Tam hoả thống soái (魔火統帥)\n"
                "<a:Hongiapbatdiet:1498612522272686101> | Hồn giáp bất diệt (魂甲不滅)\n"
                "<a:Linhdiemsathan:1498612530094805123> | Linh diệm sát thần (靈焰殺神)"
            ),
            color=0xCCFFCC,
        )

        file = discord.File(EVENT_IMAGE, filename="event.png")
        embed.set_image(url="attachment://event.png")
        await ctx.send(embed=embed, file=file)

    # ──────────────────────────────────────────
    # NHẬN THƯỞNG
    # ──────────────────────────────────────────
    @commands.command(name="eventcheck", aliases=["echeck"])
    async def event_check(self, ctx):
        uid                    = str(ctx.author.id)
        user, upgraded_weapons = get_user(uid)   # giữ upgraded_weapons để save đúng

        now        = time.time()
        last_claim = user.get("event_cd", 0)

        # ── Kiểm tra cooldown ──
        if now < last_claim:
            remaining = int(last_claim - now)
            h = remaining // 3600
            m = (remaining % 3600) // 60
            s = remaining % 60
            return await ctx.send(
                f"⏳ | Bạn phải chờ **{h}h {m}m {s}s** để nhận tiếp."
            )

        # ── Cộng 1x Soul Crate vào inventory ──
        add_item(user, f"crate_{CRATE_ID}", 1)

        # ── Cập nhật cooldown rồi lưu lên MongoDB ──
        user["event_cd"] = now + COOLDOWN
        if not save_user(uid, user, upgraded_weapons):
            return await ctx.send("❌ | Lỗi lưu dữ liệu, thử lại sau!")

        await ctx.send(
            f"<:Soulcrate:1498617031501807646> | "
            f"Bạn nhận được **1x Soul Crate (ID: {CRATE_ID})**!"
        )


    # ──────────────────────────────────────────
    # SLASH COMMANDS
    # ──────────────────────────────────────────

    @app_commands.command(name="event", description="Xem thông tin sự kiện Tam Linh")
    @app_commands.guild_only()
    async def slash_event(self, interaction: discord.Interaction):
        now     = time.time()
        elapsed = int(now - self.start_time)
        hours   = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60

        embed = discord.Embed(
            title="<:Opensoulcrate:1498617029077499935> | Tam Linh Event",
            description=(
                "Sự kiện đang diễn ra!\n"
                f"<:Linh_hoa:1498614127386562601> | Đã bắt đầu được: **{hours}h {minutes}m {seconds}s**\n"
                "Sự kiện này sẽ nhận: x1 crate soul- sau mỗi 4 tiếng, "
                "Dùng `/echeck` để nhận thưởng!\n\n"
                "<a:Tamhoathong:1498612536470409328> | Tam hoả thống soái (魔火統帥)\n"
                "<a:Hongiapbatdiet:1498612522272686101> | Hồn giáp bất diệt (魂甲不滅)\n"
                "<a:Linhdiemsathan:1498612530094805123> | Linh diệm sát thần (靈焰殺神)"
            ),
            color=0xCCFFCC,
        )

        try:
            file = discord.File(EVENT_IMAGE, filename="event.png")
            embed.set_image(url="attachment://event.png")
            await interaction.response.send_message(embed=embed, file=file)
        except FileNotFoundError:
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="echeck", description="Nhận thưởng Soul Crate từ sự kiện (mỗi 4 tiếng)")
    @app_commands.guild_only()
    async def slash_echeck(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid                    = str(interaction.user.id)
        user, upgraded_weapons = get_user(uid)

        now        = time.time()
        last_claim = user.get("event_cd", 0)

        if now < last_claim:
            remaining = int(last_claim - now)
            h = remaining // 3600
            m = (remaining % 3600) // 60
            s = remaining % 60
            return await interaction.followup.send(
                f"⏳ | Bạn phải chờ **{h}h {m}m {s}s** để nhận tiếp."
            )

        add_item(user, f"crate_{CRATE_ID}", 1)
        user["event_cd"] = now + COOLDOWN
        if not save_user(uid, user, upgraded_weapons):
            return await interaction.followup.send("❌ | Lỗi lưu dữ liệu, thử lại sau!")

        await interaction.followup.send(
            f"<:Soulcrate:1498617031501807646> | "
            f"Bạn nhận được **1x Soul Crate (ID: {CRATE_ID})**!"
        )


async def setup(bot):
    cog = Event(bot)
    await bot.add_cog(cog)

    # Luôn remove trước rồi add lại — tránh slash bị stale sau reload extension
    for cmd, name in (
        (cog.slash_event,  "event"),
        (cog.slash_echeck, "echeck"),
    ):
        bot.tree.remove_command(name)
        bot.tree.add_command(cmd)
