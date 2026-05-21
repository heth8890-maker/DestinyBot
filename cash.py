# cash.py — Economy & Minigames Cog (gộp cash + blackjack)
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
# HẰNG SỐ CHUNG — ECONOMY
# ───────────────────────────────────────────

MAX_ALL_BET   = 250_000
ICON_COIN     = "<:Coin:1495831576397742241>"


# ───────────────────────────────────────────
# HẰNG SỐ — BLACKJACK
# ───────────────────────────────────────────

CARDS = {
    "2":  ["<a:2_nhep:1501223722466672731>",  "<a:2_co:1501223719987839137>",   "<a:2_bich:1501223717454352405>",  "<a:2_ro:1501223711393710090>"],
    "3":  ["<a:3_nhep:1501223707778224178>",  "<a:3_co:1501223704603131914>",   "<a:3_bich:1501223701713129715>",  "<a:3_ro:1501223699393810442>"],
    "4":  ["<a:4_nhep:1501223691558588466>",  "<a:4_co:1501223686643122286>",   "<a:4_bich:1501223694184222720>",  "<a:4_ro:1501223688916439072>"],
    "5":  ["<a:5_nhep:1501223675905708153>",  "<a:5_co:1501223678598320221>",   "<a:5_bich:1501223681735524554>",  "<a:5_ro:1501223684126539986>"],
    "6":  ["<a:6_nhep:1501223662487867492>",  "<a:6_co:1501223666434703474>",   "<a:6_bich:1501223669639413831>",  "<a:6_ro:1501223673292390551>"],
    "7":  ["<a:7_nhep:1501223761452597368>",  "<a:7_co:1501228368312668230>",   "<a:7_bich:1501223764472369152>",  "<a:7_ro:1501223767110848563>"],
    "8":  ["<a:8_nhep:1501223757862142083>",  "<a:8_co:1501223747162607736>",   "<a:8_bich:1501223752371933286>",  "<a:8_ro:1501223755010019360>"],
    "9":  ["<a:9_nhep:1501223738874789978>",  "<a:9_co:1501223741387178004>",   "<a:9_bich:1501223733451292832>",  "<a:9_ro:1501223744436179025>"],
    "10": ["<a:10_nhep:1501223727315161158>", "<a:10_co:1501223730515542157>",  "<a:10_bich:1501229502049812590>", "<a:10_ro:1501223736505008128>"],
    "J":  ["<a:J_nhep:1501223781845438606>",  "<a:J_co:1501223786677141784>",   "<a:J_bich:1501223789009047623>",  "<a:J_ro:1501223790938554480>"],
    "Q":  ["<a:Q_nhep:1501228375317024939>",  "<a:Q_co:1501223774224257135>",   "<a:Q_bich:1501223776807948419>",  "<a:Q_ro:1501223779211411546>"],
    "K":  ["<a:K_nhep:1501223769610522766>",  "<a:K_co:1501228373236650094>",   "<a:K_bich:1501223784370405517>",  "<a:K_ro:1501223771862728774>"],
    "A":  ["<a:A_nhep:1501223805459370106>",  "<a:A_co:1501223796756058174>",   "<a:A_bich:1501223802904907868>",  "<a:A_ro:1501223800170086430>"],
}

VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
}

RANKS = list(VALUES.keys())

HIDDEN_CARD  = "<:Hidden:1501252466446958683>"
HIT_EMOJI    = "👊"
STAND_EMOJI  = "🛑"

MAX_BET      = 250_000
GAME_TIMEOUT = 90

COLOR_ACTIVE = 0x00BFFF
COLOR_WIN    = 0x57F287
COLOR_LOSE   = 0xED4245
COLOR_PUSH   = 0xFEE75C

# Global active-session store  { user_id: BlackjackGame }
active_games: dict = {}

# Slot icons
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
# ECONOMY — MONGODB (dùng chung cho tất cả cog)
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
# BLACKJACK — HELPERS
# ───────────────────────────────────────────

def draw_card() -> dict:
    rank  = random.choice(RANKS)
    emoji = random.choice(CARDS[rank])
    return {"rank": rank, "emoji": emoji}


def calc_score(hand: list) -> int:
    score = sum(VALUES[c["rank"]] for c in hand)
    aces  = sum(1 for c in hand if c["rank"] == "A")
    while score > 21 and aces:
        score -= 10
        aces  -= 1
    return score


def hand_str(hand: list) -> str:
    return " ".join(c["emoji"] for c in hand)


def is_natural(hand: list) -> bool:
    """True if hand is a 2-card 21 (natural blackjack)."""
    return len(hand) == 2 and calc_score(hand) == 21


# ───────────────────────────────────────────
# BLACKJACK — GAME CLASS
# ───────────────────────────────────────────

class BlackjackGame:
    def __init__(self, ctx: commands.Context, bet: int):
        self.ctx          = ctx
        self.bot          = ctx.bot
        self.bet          = bet
        self.player_hand: list = []
        self.dealer_hand: list = []
        self.message: discord.Message | None = None
        self.is_over      = False
        self._lock        = asyncio.Lock()
        self._outcome: str | None = None
        self._status_text = ""

    # ── Embed builder ──────────────────────────────────────────────────────────

    async def _build_embed(self, *, dealer_reveal: bool = False) -> discord.Embed:
        user   = self.ctx.author
        avatar = user.display_avatar.url
        name   = user.display_name

        color_map = {
            "win":     COLOR_WIN,
            "lose":    COLOR_LOSE,
            "push":    None,
            "timeout": None,
        }
        color = color_map.get(self._outcome, COLOR_ACTIVE)  # type: ignore[arg-type]

        embed = discord.Embed(color=color)
        embed.set_author(
            name=f"{name}, you bet {self.bet:,} to play blackjack",
            icon_url=avatar,
        )

        # ── Dealer row ────────────────────────────────────────────────────────
        if dealer_reveal:
            d_score  = calc_score(self.dealer_hand)
            d_header = f"Dealer [{d_score}]"
            d_cards  = hand_str(self.dealer_hand)
        else:
            first_rank  = self.dealer_hand[0]["rank"] if self.dealer_hand else "?"
            first_val   = VALUES.get(first_rank, 0) if self.dealer_hand else 0
            d_header    = f"Dealer [{first_val}+?]"
            first_emoji = self.dealer_hand[0]["emoji"] if self.dealer_hand else ""
            d_cards     = f"{first_emoji} {HIDDEN_CARD}"

        embed.add_field(name=d_header, value=d_cards or "\u200b", inline=False)

        # ── Player row ────────────────────────────────────────────────────────
        p_score  = calc_score(self.player_hand)
        p_header = f"{name} [{p_score}]"
        p_cards  = hand_str(self.player_hand)

        embed.add_field(name=p_header, value=p_cards or "\u200b", inline=False)

        status = self._status_text if self._status_text else "🎲 ~ game is active"
        embed.set_footer(text=status)

        return embed

    # ── Payout resolution ──────────────────────────────────────────────────────

    async def _resolve_payout(self, *, timed_out: bool = False) -> None:
        """Calculate outcome, apply balance change, set status line. Called once."""
        name = self.ctx.author.display_name
        uid  = self.ctx.author.id

        if timed_out:
            self._outcome     = "timeout"
            self._status_text = "🎲 ~ game has ended (timeout)"
            return

        p_score = calc_score(self.player_hand)
        d_score = calc_score(self.dealer_hand)

        p_natural = is_natural(self.player_hand)
        d_natural = is_natural(self.dealer_hand)

        if p_score > 21 and d_score > 21:
            # Both busted → push, refund bet
            await update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH — cả hai cùng vượt 21! (refund)"

        elif p_score > 21:
            # Player busted (dealer did not)
            self._outcome     = "lose"
            self._status_text = f"🎲 ~ {name} LOST {self.bet:,} coins!"

        elif p_natural and d_natural:
            # Both natural → push
            await update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH — both Blackjack! (refund)"

        elif p_natural:
            # Natural blackjack → 2.5x payout
            payout = int(self.bet * 2.5)
            await update_balance_safe(uid, payout)
            self._outcome     = "win"
            self._status_text = f"🎲 ~ ♠ BLACKJACK! {name} WON {payout - self.bet:,} coins!"

        elif p_score == 21:
            # Non-natural 21 (3+ cards) → also 2.5x bonus payout
            payout = int(self.bet * 2.5)
            await update_balance_safe(uid, payout)
            self._outcome     = "win"
            self._status_text = f"🎲 ~  21 ĐIỂM! {name} WON {payout - self.bet:,} coins!"

        elif d_score > 21 or p_score > d_score:
            # Normal win → return bet + profit (bet × 2 total)
            await update_balance_safe(uid, self.bet * 2)
            self._outcome     = "win"
            self._status_text = f"🎲 ~ {name} WON {self.bet:,} coins!"

        elif p_score < d_score:
            # Dealer wins
            self._outcome     = "lose"
            self._status_text = f"🎲 ~ {name} LOST {self.bet:,} coins!"

        else:
            # Push → refund bet only
            await update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH (refund bet)"

    # ── Game phases ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # Deduct the bet upfront; refunded on win/push via _resolve_payout.
        await update_balance_safe(self.ctx.author.id, -self.bet, require=self.bet)

        for _ in range(2):
            self.player_hand.append(draw_card())
            self.dealer_hand.append(draw_card())

        embed = await self._build_embed()
        self.message = await self.ctx.send(embed=embed)

        try:
            await self.message.add_reaction(HIT_EMOJI)
            await self.message.add_reaction(STAND_EMOJI)
        except Exception:
            pass

        await self._game_loop()

    async def _game_loop(self) -> None:
        def _check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                user.id == self.ctx.author.id
                and reaction.message.id == self.message.id
                and str(reaction.emoji) in (HIT_EMOJI, STAND_EMOJI)
            )

        while not self.is_over:
            # Listen for BOTH reaction_add and reaction_remove simultaneously.
            # Whichever fires first (add OR remove) counts as pressing the button.
            task_add = asyncio.ensure_future(
                self.bot.wait_for("reaction_add", check=_check)
            )
            task_remove = asyncio.ensure_future(
                self.bot.wait_for("reaction_remove", check=_check)
            )

            done, pending = await asyncio.wait(
                {task_add, task_remove},
                timeout=GAME_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever task did not fire
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if not done:
                # Timeout — neither event fired in time
                await self._timeout_forfeit()
                return

            try:
                reaction, user = done.pop().result()
            except Exception:
                continue

            emoji = str(reaction.emoji)

            # Spam protection: skip if lock already held
            if self._lock.locked():
                continue

            async with self._lock:
                # Race-condition guard
                if self.is_over:
                    break

                if emoji == HIT_EMOJI:
                    await self._player_hit()
                elif emoji == STAND_EMOJI:
                    self.is_over = True
                    await self.end_game()
                    break

    async def _player_hit(self) -> None:
        self.player_hand.append(draw_card())
        p_score = calc_score(self.player_hand)

        if p_score > 21:
            # Player busted → go straight to end_game (draws dealer + resolves in one edit)
            self.is_over = True
            await self.end_game()
        else:
            try:
                embed = await self._build_embed()
                await self.message.edit(embed=embed)
            except Exception:
                pass

    async def end_game(self) -> None:
        """Draw remaining dealer cards, resolve payout, then do ONE final embed edit."""
        self.is_over = True

        # Draw all needed dealer cards
        while True:
            d_score = calc_score(self.dealer_hand)
            if d_score > 21 or d_score >= 16:
                break
            self.dealer_hand.append(draw_card())

        # Resolve payout first so embed gets the correct color + status text
        await self._resolve_payout()

        # Single edit with full reveal + outcome
        try:
            embed = await self._build_embed(dealer_reveal=True)
            await self.message.edit(embed=embed)
        except Exception:
            pass

        try:
            await self.message.clear_reactions()
        except Exception:
            pass

        active_games.pop(self.ctx.author.id, None)

    async def _timeout_forfeit(self) -> None:
        """Player did not respond within GAME_TIMEOUT seconds."""
        self.is_over = True
        await self._resolve_payout(timed_out=True)

        try:
            embed = await self._build_embed(dealer_reveal=False)
            await self.message.edit(embed=embed)
        except Exception:
            pass

        try:
            await self.message.clear_reactions()
        except Exception:
            pass

        try:
            await self.ctx.send(
                f"⏰ {self.ctx.author.mention}, your blackjack game timed out after "
                f"{GAME_TIMEOUT}s. Start a new game with `dtn bj <bet>`.",
                delete_after=10,
            )
        except Exception:
            pass

        active_games.pop(self.ctx.author.id, None)


# ───────────────────────────────────────────
# COG — ECONOMY & MINIGAMES
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


# ───────────────────────────────────────────
# COG — BLACKJACK
# ───────────────────────────────────────────

class BlackjackCog(commands.Cog, name="Blackjack"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.cooldown(1, 12, commands.BucketType.user)
    @commands.command(name="bj", aliases=["blackjack"])
    async def blackjack(self, ctx: commands.Context, bet: str):
        """Play a game of Blackjack.  Usage: dtn bj <amount> | dtn bj all | dtn bj al"""
        user_id = ctx.author.id

        # ── Guard: existing session ──────────────────────────────────────────
        if user_id in active_games:
            return await ctx.send(
                f"❌ {ctx.author.mention}, Trò chơi blackjack đang active! "
                "Finish it first or wait for it to time out.",
                delete_after=9,
            )

        balance = get_balance(user_id)

        # ── Parse bet ─────────────────────────────────────────────────────────
        bet_clean = bet.strip().lower()

        if bet_clean == "al":
            # [YC3] "al" (1 chữ l) → đặt đúng 1 coin
            bet_amount = 1

        elif bet_clean == "all":
            if balance <= 0:
                return await ctx.send("❌ You have no coins to bet!", delete_after=6)
            # Cap "all" at MAX_BET
            bet_amount = min(balance, MAX_BET)

        else:
            bet_clean = bet_clean.replace(",", "").replace(".", "")
            if not bet_clean.isdigit():
                return await ctx.send(
                    "❌ |  Hãy đặt số cược bạn vào trò chơi với lệnh `dtn bj <số cược>`!",
                    delete_after=6,
                )
            bet_amount = int(bet_clean)
            if bet_amount <= 0:
                return await ctx.send("❌ Bet must be greater than 0!", delete_after=6)

            # [YC2] Nếu đặt > MAX_BET → tự clamp xuống MAX_BET, không báo lỗi
            if bet_amount > MAX_BET:
                bet_amount = MAX_BET

            if balance < bet_amount:
                return await ctx.send(
                    f"❌ | Không đủ số cược! Túi tiền hiện có: **{balance:,}** coins.",
                    delete_after=8,
                )

        # Guard: balance phải đủ để đặt (kể cả sau khi clamp)
        if balance < bet_amount:
            return await ctx.send(
                f"❌ | Không đủ số cược! Túi tiền hiện có: **{balance:,}** coins.",
                delete_after=8,
            )

        # ── Register session before game starts ───────────────────────────────
        game = BlackjackGame(ctx, bet_amount)
        active_games[user_id] = game

        try:
            await game.start()
        except Exception as exc:
            print(f"[Blackjack] Unhandled error for {ctx.author}: {exc}")
            try:
                await update_balance_safe(user_id, bet_amount)
            except Exception:
                pass
            try:
                await ctx.send(
                    f"⚠️ {ctx.author.mention}, Một lỗi đã xảy ra. "
                    "Số tiền cược đã được hoàn lại!",
                    delete_after=10,
                )
            except Exception:
                pass
        finally:
            active_games.pop(user_id, None)

    @blackjack.error
    async def blackjack_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ {ctx.author.mention}, chờ **{error.retry_after:.1f}s** trước khi chơi lại!",
                delete_after=5,
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ Thiếu số cược! Dùng: `dtn bj <số cược>` hoặc `dtn bj all`",
                delete_after=6,
            )


# ───────────────────────────────────────────
# SETUP
# ───────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(Cash(bot))
    await bot.add_cog(BlackjackCog(bot))
