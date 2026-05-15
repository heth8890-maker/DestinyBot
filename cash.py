# cash.py — Economy & Minigames Cog
# Đã chuyển hoàn toàn sang MongoDB (database_helper.py)
# Xóa toàn bộ JSON economy (load_eco / save_eco / economy.json)

import discord
from discord.ext import commands
import asyncio
import random
from datetime import datetime, timezone, timedelta

from database_helper import load_core_data, save_core_data

# ✅ FIX Lỗi 2: import đúng từ rpg_database + rpg_core thay vì rpg_crate (JSON cũ)
from rpg_database import get_user, save_user
from rpg_core import add_item, CRATES

# ───────────────────────────────────────────
# HẰNG SỐ CHUNG
# ───────────────────────────────────────────

MAX_ALL_BET   = 250_000
ICON_COIN     = "<:Coin:1495831576397742241>"
SPIN_WIN_RATE = 0.45   # Thay đổi bằng lệnh setrate

SLOT_NORMAL = [
    "<:2648:1494626284226089032>",
    "<:2645:1494623915400495174>",
    "<:2646:1494623890628939837>",
    "<:2647:1494623871222157483>",
    "<:Cumeo:1494623802716459130>",
    "<:Candy:1492085760520622120>",
    "<a:2727:1494975153216421919>",
]
SLOT_X3  = "<a:X3:1494626126331514900>"
SLOT_X5  = "<a:X5:1494624600766808254>"
SLOT_ALL = SLOT_NORMAL + [SLOT_X3, SLOT_X5]

# ───────────────────────────────────────────
# ECONOMY — MONGODB
# ───────────────────────────────────────────

# Per-user async lock — tránh race condition khi nhiều lệnh cùng đụng 1 user
_user_locks: dict[str, asyncio.Lock] = {}


def get_user_lock(user_id) -> asyncio.Lock:
    uid = str(user_id)
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]


def get_balance(user_id) -> int:
    """Đọc số dư của user từ MongoDB. Sync, chỉ dùng để kiểm tra trước khi xử lý."""
    data = load_core_data(str(user_id))
    return data["user"].get("cash", 0)


async def update_balance_safe(user_id, amount: int, require: int = 0) -> int | None:
    """
    Cộng/trừ tiền an toàn (có Lock + lưu MongoDB).
    Dùng số dương để cộng, số âm để trừ.

    require > 0  → kiểm tra balance >= require BÊN TRONG lock trước khi trừ.
                   Nếu không đủ trả về None (không trừ, không lưu).
                   Dùng để chống TOCTOU race condition khi trừ tiền.

    Trả về số dư mới sau khi cập nhật, hoặc None nếu không đủ tiền.
    """
    uid = str(user_id)
    async with get_user_lock(uid):
        data = load_core_data(uid)
        user = data["user"]
        current = user.get("cash", 0)
        if require > 0 and current < require:
            return None
        user["cash"] = current + amount
        save_core_data(uid, user)   # ✅ FIX: truyền user doc, không phải wrapper data
        return user["cash"]


# ───────────────────────────────────────────
# VIEW XÁC NHẬN — LỆNH PAY
# ───────────────────────────────────────────

class ConfirmPay(discord.ui.View):
    def __init__(self, ctx, member, amount):
        super().__init__(timeout=30)
        self.ctx    = ctx
        self.member = member
        self.amount = amount
        self.message = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Không phải bạn!", ephemeral=True)

        # require=amount → guard bên trong lock, chống race condition
        result = await update_balance_safe(self.ctx.author.id, -self.amount, require=self.amount)
        if result is None:
            return await interaction.response.edit_message(
                content="❌ Bạn không đủ tiền để thực hiện giao dịch.",
                embed=None, view=None
            )

        await update_balance_safe(self.member.id, self.amount)
        self.stop()

        done_embed = discord.Embed(
            description="✅ Đã giao dịch",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=done_embed, view=None)

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Không phải bạn!", ephemeral=True)

        self.stop()
        await interaction.response.edit_message(
            content="❌ Giao dịch đã hủy.",
            embed=None, view=None
        )


# ───────────────────────────────────────────
# VIEW XÁC NHẬN — LỆNH GIVE
# ───────────────────────────────────────────

class ConfirmGive(discord.ui.View):
    def __init__(self, ctx, member, amount):
        super().__init__(timeout=30)
        self.ctx    = ctx
        self.member = member
        self.amount = amount
        self.message = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Không phải bạn!", ephemeral=True)

        await update_balance_safe(self.member.id, self.amount)
        self.stop()

        done_embed = discord.Embed(
            description="✅ Đã giao dịch",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=done_embed, view=None)

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Không phải bạn!", ephemeral=True)

        self.stop()
        await interaction.response.edit_message(
            content="❌ Giao dịch đã hủy.",
            embed=None, view=None
        )


# ───────────────────────────────────────────
# COG CHÍNH
# ───────────────────────────────────────────

class Cash(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── LỆNH BAL ──────────────────────────
    @commands.command(name="bal", aliases=["cash"])
    @commands.cooldown(1, 12, commands.BucketType.user)
    async def balance(self, ctx):
        bal = get_balance(ctx.author.id)
        await ctx.send(
            f"<:2245:1493575277605949480> | {ctx.author.name} có: "
            f"__{bal:,}__ {ICON_COIN}"
        )

    # ── LỆNH DAILY ────────────────────────
    @commands.command(name="daily")
    async def daily(self, ctx):
        uid    = str(ctx.author.id)
        vn_tz  = timezone(timedelta(hours=7))
        now_vn = datetime.now(vn_tz)
        today  = now_vn.date()

        # ── Xử lý tiền & streak trong 1 lock ──
        async with get_user_lock(uid):
            data = load_core_data(uid)
            user = data["user"]

            last_daily  = user.get("daily_date")
            last_streak = int(user.get("daily_streak", 0))

            last_date = None
            if last_daily:
                try:
                    last_date = datetime.strptime(last_daily, "%Y-%m-%d").date()
                except Exception:
                    last_date = None

            # Kiểm tra đã nhận hôm nay chưa
            if last_date == today:
                reset_time   = datetime(now_vn.year, now_vn.month, now_vn.day, tzinfo=vn_tz) + timedelta(days=1)
                seconds_left = int((reset_time - now_vn).total_seconds())
                h, rem = divmod(seconds_left, 3600)
                m, s   = divmod(rem, 60)
                return await ctx.send(
                    f"❌ Bạn đã nhận daily hôm nay rồi! Reset sau **{h}h {m}m {s}s**."
                )

            # Tính streak & tiền
            streak = (last_streak + 1) if last_date and (today - last_date).days == 1 else 1
            base   = 2000
            bonus  = (streak - 1) * 200
            total  = base + bonus

            # Cập nhật cash trong user doc
            user["cash"]         = user.get("cash", 0) + total
            user["daily_date"]   = today.strftime("%Y-%m-%d")
            user["daily_streak"] = streak
            save_core_data(uid, user)   # ✅ FIX: truyền user doc, không phải wrapper data

        # ── Tặng rương (RPG data — MongoDB) ──
        # get_user / save_user là sync — không dùng await
        rpg_user, _ = get_user(uid)
        crate_item_id = "001"
        crate_key     = f"crate_{crate_item_id}"
        add_item(rpg_user, crate_key, 1)
        save_user(uid, rpg_user)

        crate_info = CRATES.get(crate_item_id)
        if crate_info is None:
            return await ctx.send("❌ Lỗi nội bộ: không tìm thấy thông tin crate.")

        await ctx.send(
            f"📅 | {ctx.author.name} nhận **{total:,}** {ICON_COIN} daily!\n"
            f"{crate_info['emoji']} | Nhận thêm: **1x {crate_info['name']}**\n"
            f"🔥 | Streak: **{streak} ngày** (+{bonus:,})"
        )

    # ── LỆNH GIVE (Creator) ───────────────
    @commands.command(name="give")
    @commands.is_owner()
    async def give(self, ctx, member: discord.Member, amount: int):
        if amount <= 0:
            return await ctx.send("❌ Số tiền phải lớn hơn 0...")
        if member.bot:
            return await ctx.send("❌ Không thể give cho bot...")

        embed = discord.Embed(
            title="🎁 Xác nhận tặng tiền",
            description=(
                "*Khi đồng ý người chơi này sẽ nhận được số tiền đó, "
                "và sẽ không thể hủy số tiền khi đã xác nhận. "
                "Xác nhận người chơi sẽ nhận số tiền tương ứng, "
                "hủy để hủy yêu cầu giao dịch này.*"
            ),
            color=discord.Color.blue()
        )
        embed.add_field(name="Người nhận", value=member.mention, inline=True)
        embed.add_field(name="Số tiền", value=f"{amount:,} {ICON_COIN}", inline=True)

        view         = ConfirmGive(ctx, member, amount)
        view.message = await ctx.send(embed=embed, view=view)

    # ── LỆNH PAY ──────────────────────────
    @commands.command(name="pay")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pay(self, ctx, member: discord.Member, amount: int):
        if member == ctx.author:
            return await ctx.send("❌ Bạn không thể chuyển tiền cho chính mình.")
        if member.bot:
            return await ctx.send("❌ Không thể chuyển tiền cho bot.")
        if amount <= 0:
            return await ctx.send("❌ Số tiền phải lớn hơn 0.")

        bal = get_balance(ctx.author.id)
        if bal < amount:
            return await ctx.send(f"❌ Bạn không đủ tiền (Số dư: {bal:,} {ICON_COIN}).")

        embed = discord.Embed(
            title="Xác nhận chuyển tiền",
            description=(
                f"Bạn muốn chuyển **{amount:,}** {ICON_COIN} cho {member.mention}?\n\n"
                f"-# Khi xác nhận, giao dịch sẽ không thể hoàn tác được — "
                f"số tiền của bạn sẽ được chuyển đi ngay lập tức."
            ),
            color=discord.Color.blue()
        )
        embed.add_field(name="Số tiền", value=f"{amount:,} {ICON_COIN}", inline=True)

        view         = ConfirmPay(ctx, member, amount)
        view.message = await ctx.send(embed=embed, view=view)

    # ── LỆNH SETRATE (Admin) ──────────────
    @commands.command(name="setrate")
    @commands.has_permissions(administrator=True)
    async def setrate(self, ctx, game: str, rate: float):
        global SPIN_WIN_RATE

        if game.lower() != "spin":
            return await ctx.send("❌ Hiện chỉ hỗ trợ: `setrate spin [tỉ lệ]`")
        if not (0 <= rate <= 100):
            return await ctx.send("❌ Tỉ lệ phải từ 0 đến 100 (%).")

        SPIN_WIN_RATE = rate / 100
        await ctx.send(f"✅ Đã cập nhật tỉ lệ thắng **spin** thành **{rate}%**.")

    # ── LỆNH SPIN ─────────────────────────
    @commands.command(name="spin", aliases=["sp"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def spin(self, ctx, amount: str):
        bal = get_balance(ctx.author.id)

        if amount.lower() == "all":
            amount = min(bal, MAX_ALL_BET)
        else:
            try:
                amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Số tiền không hợp lệ...")

        if amount <= 0:
            return await ctx.send("❌ Số tiền cược phải lớn hơn 0...")
        if amount > MAX_ALL_BET:
            return await ctx.send(f"❌ Cược tối đa **{MAX_ALL_BET:,}** {ICON_COIN} mỗi lần.")
        if bal < amount:
            return await ctx.send("❌ Bạn không đủ tiền để cược...")

        # require=amount → trừ tiền + guard balance âm bên trong lock
        deducted = await update_balance_safe(ctx.author.id, -amount, require=amount)
        if deducted is None:
            return await ctx.send("❌ Bạn không đủ tiền để cược...")

        is_win = random.random() < SPIN_WIN_RATE

        gif_win  = "https://media3.giphy.com/media/v1.Y2lkPTZjMDliOTUyYm9yc2E3NW16bGlvdGw3ZmJpZmdmZ2p1azB2M3pyanluMHptcmowMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/b2HBZe6VYZ639q3aw2/giphy.gif"
        gif_lose = "https://media0.giphy.com/media/v1.Y2lkPTZjMDliOTUyd20zYWFmOXR3YmRwaHBkMzgyMjM3ajRhdjY5NnJhbTY4OHNmOTEweCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/m0iIMU7JeJJ1CIGRL2/giphy.gif"
        selected_gif = gif_win if is_win else gif_lose

        await ctx.send(f"🎰 {ctx.author.name} đã gửi {amount:,} {ICON_COIN} để quay spin...")

        embed = discord.Embed(
            title=f"🎰 {ctx.author.name} ĐANG QUAY SPIN... 🎰",
            color=discord.Color.gold()
        )
        embed.set_image(url=selected_gif)
        message = await ctx.send(embed=embed)

        await asyncio.sleep(3.0)

        if is_win:
            winnings = amount * 2
            new_bal  = await update_balance_safe(ctx.author.id, winnings)
            result_text = (
                f"**{ctx.author.mention} Winnings!** "
                f"Bạn đã thắng và nhận được: **{winnings:,}** {ICON_COIN}! "
            )
            color   = discord.Color.green()
            new_gif = "https://cdn.discordapp.com/attachments/1491821822562406654/1493994918962790490/ezgif.com-animated-gif-maker_2.gif?ex=69e0feb1&is=69dfad31&hm=18951d7a4a4569346c8cb278b35ee5cdeea0c6fea39b767725cdd88f721fbd39&"
        else:
            new_bal = get_balance(ctx.author.id)
            result_text = (
                f"**{ctx.author.mention} Loss!** "
                f"Bạn đã mất số tiền ban đầu đã cược. "
            )
            color   = discord.Color.red()
            new_gif = "https://cdn.discordapp.com/attachments/1491821822562406654/1493994928311767152/ezgif.com-animated-gif-maker_1.gif?ex=69e0feb3&is=69dfad33&hm=e5c15640fbf3072d04fbaec9e6182704c3ce5e346e0f4ba302d01b543255f9d8&"

        result_embed = discord.Embed(
            title="| KẾT QUẢ SPIN... |",
            description=result_text,
            color=color
        )
        result_embed.set_image(url=new_gif)
        await message.edit(embed=result_embed)

        await asyncio.sleep(60)
        try:
            await message.delete()
        except Exception:
            pass

    # ── LỆNH SLOT ─────────────────────────
    @commands.command(name="slot", aliases=["s", "sl"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def slot(self, ctx, amount: str):
        bal = get_balance(ctx.author.id)

        if amount.lower() == "all":
            amount = min(bal, MAX_ALL_BET)
        else:
            try:
                amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Số tiền không hợp lệ...")

        if amount <= 0:
            return await ctx.send("❌ Số tiền cược phải lớn hơn 0.")
        if amount > MAX_ALL_BET:
            return await ctx.send(f"❌ Cược tối đa **{MAX_ALL_BET:,}** {ICON_COIN} mỗi lần.")
        if bal < amount:
            return await ctx.send(f"❌ Bạn không đủ tiền (Số dư: {bal:,}).")

        # require=amount → trừ tiền + guard balance âm bên trong lock
        deducted = await update_balance_safe(ctx.author.id, -amount, require=amount)
        if deducted is None:
            return await ctx.send("❌ Bạn không đủ tiền để cược...")

        # ── CẤU HÌNH ICON ──
        CHANGE   = "<a:Changev4:1494983646505861161>"
        NEW_ICON = "<a:2727:1494975153216421919>"
        ICON2730 = "<a:2730:1494968430892290229>"
        ICONS = [
            "<:2648:1494626284226089032>",
            "<:2645:1494623915400495174>",
            "<:2646:1494623890628939837>",
            "<:2647:1494623871222157483>",
            "<:Cumeo:1494623802716459130>",
            "<:Candy:1492085760520622120>",
            NEW_ICON,
            ICON2730,
        ]
        X3       = "<a:X3:1494626126331514900>"
        X5       = "<a:X5:1494624600766808254>"
        COIN     = "<:Coin:1495831576397742241>"
        ALL_ICONS = ICONS + [X3, X5]

        def build_slot_embed(a, b, c, description_text="", color=discord.Color.gold()):
            embed = discord.Embed(title="🎰 SLOT MACHINE 🎰", color=color)
            slot_display = (
                "╔══════════════════╗\n"
                f"║    {a}  {b}  {c}    ║\n"
                "╚══════════════════╝"
            )
            embed.description = f"{slot_display}\n\n{description_text}"
            return embed

        await ctx.send(f"🎰 **{ctx.author.display_name}** đã gửi **{amount:,}** {COIN} để quay slot...")

        msg = await ctx.send(embed=build_slot_embed(CHANGE, CHANGE, CHANGE))

        await asyncio.sleep(1.3)
        await msg.edit(embed=build_slot_embed(CHANGE, CHANGE, CHANGE))

        # ── TÍNH KẾT QUẢ ──
        roll = random.random()

        if roll < 0.01:
            final = [X5, X5, X5]; multi = 5
        elif roll < 0.03:
            final = [X3, X3, X3]; multi = 3
        elif roll < 0.07:
            final = [NEW_ICON, NEW_ICON, NEW_ICON]; multi = 3.5
        elif roll < 0.13:
            final = [ICON2730, ICON2730, ICON2730]; multi = 2.5
        elif roll < 0.45:
            icon  = random.choice([i for i in ICONS if i not in (NEW_ICON, ICON2730)])
            final = [icon, icon, icon]; multi = 2
        else:
            if random.random() < 0.56:
                pair_icon = random.choice(ALL_ICONS)
                third     = random.choice([i for i in ALL_ICONS if i != pair_icon])
                final     = [pair_icon, pair_icon, third] if random.random() < 0.5 else [third, pair_icon, pair_icon]
            else:
                final = [random.choice(ALL_ICONS) for _ in range(3)]
                while final[0] == final[1] == final[2]:
                    final = [random.choice(ALL_ICONS) for _ in range(3)]
            multi = 0

        # ── DỪNG TỪNG Ô ──
        await asyncio.sleep(0.5)
        await msg.edit(embed=build_slot_embed(final[0], CHANGE, CHANGE))
        await asyncio.sleep(0.5)
        await msg.edit(embed=build_slot_embed(final[0], final[1], CHANGE))
        await asyncio.sleep(0.5)

        # ── KẾT QUẢ CUỐI ──
        if multi > 0:
            win_amt = int(amount * multi)
            await update_balance_safe(ctx.author.id, win_amt)
            result_line = (
                f"🎰 | {ctx.author.mention} **WINNINGS!**\n"
                f"Số tiền cược bạn đã **(x{multi})** {COIN} và nhận được **{win_amt:,}**"
            )
            final_color = discord.Color.green()
        else:
            result_line = (
                f"🎰 | {ctx.author.mention} **LOSE!**\n"
                f"Bạn đã mất số cược ban đầu của mình. Hãy gõ `{ctx.prefix}slot` để tiếp tục trò chơi!"
            )
            final_color = discord.Color.red()

        await msg.edit(embed=build_slot_embed(final[0], final[1], final[2], result_line, final_color))


async def setup(bot):
    await bot.add_cog(Cash(bot))
