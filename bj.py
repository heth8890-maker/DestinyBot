import random
import asyncio
import discord
from discord.ext import commands
from cash import get_balance, update_balance_safe

# =========================
# DATA & CONFIG
# =========================

CARDS = {
    "2":  ["<:2_nhep:1498334768561914129>",  "<:2_co:1498334764367614064>",  "<:2_bich:1498334760714637422>",  "<:2_ro:1498334758533599302>"],
    "3":  ["<:3_nhep:1498334755421163751>",  "<:3_co:1498334752162185268>",  "<:3_ro:1498334744855711774>",   "<:3_bich:1498334741974487222>"],
    "4":  ["<:4_ro:1498334738753257593>",    "<:4_co:1498334725562040370>",  "<:4_bich:1498334734730661948>", "<:4_nhep:1498334722953052282>"],
    "5":  ["<:5_ro:1498334720847773788>",    "<:5_bich:1498334717315911861>","<:5_co:1498334710143910099>",   "<:5_nhep:1498334707266355210>"],
    "6":  ["<:6_ro:1498334704469020713>",    "<:6_bich:1498334701805375578>","<:6_co:1498334693614031019>",   "<:6_nhep:1498334691030470826>"],
    "7":  ["<:7_ro:1498334686517264424>",    "<:7_bich:1498334677529001995>","<:7_co:1498334675188453538>",   "<:7_nhep:1498334672625602661>"],
    "8":  ["<:8_ro:1498334670318735440>",    "<:8_bich:1498334667743559690>","<:8_co:1498334665570914545>",   "<:8_nhep:1498334662660067388>"],
    "9":  ["<:9_ro:1498334653122215997>",    "<:9_bich:1498334647350726879>","<:9_co:1498334643731173466>",   "<:9_nhep:1498334641436885012>"],
    "10": ["<:10_ro:1498334638815318118>",   "<:10_bich:1498334636940460112>","<:10_co:1498334635250155530>", "<:10_nhep:1498334631982792714>"],
    "J":  ["<:J_ro:1498334630120656999>",    "<:J_bich:1498334628023635968>","<:J_co:1498334625183830097>",   "<:J_nhep:1498334622746939442>"],
    "Q":  ["<:Q_ro:1498334620209643601>",    "<:Q_bich:1498334618552897667>","<:Q_co:1498334616724176996>",   "<:Q_nhep:1498334613976907827>"],
    "K":  ["<:K_ro:1498334611300941945>",    "<:K_bich:1498334609492938845>","<:K_co:1498334606963904672>",   "<:K_nhep:1498334604074029126>"],
    # FIX #1: Added missing A_co (replace ID with actual emoji ID from your server)
    "A":  ["<:A_ro:1498334601192669327>",    "<:A_bich:1498334599376404720>","<:A_co:1498334597094834286>",   "<:A_nhep:1498334593986854973>"],
}

VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
}

RANKS = list(VALUES.keys())

# Equal probability: every rank has the same chance of appearing.

# FIX: Replace OPEN_CARD with new emoji
OPEN_CARD   = "<a:Opencard:1499321421841829998>"
HIDDEN_CARD = "<:Bai:1498341726392287232>"

HIT_EMOJI   = "👊"
STAND_EMOJI = "🛑"

GAME_TIMEOUT = 90   # seconds before auto-forfeit

COLOR_ACTIVE = 0x00BFFF
COLOR_WIN    = 0x57F287
COLOR_LOSE   = 0xED4245
COLOR_PUSH   = 0xFEE75C

# Global active-session store  { user_id: BlackjackGame }
active_games: dict = {}

# FIX #6: Global async lock for thread-safe economy writes
_balance_lock = asyncio.Lock()


# =========================
# HELPERS
# =========================

def draw_card() -> dict:
    rank  = random.choice(RANKS)          # Equal probability for every rank
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


# FIX #6: Thread-safe balance update wrapper
async def safe_update_balance_safe(uid: int, amount: int) -> None:
    async with _balance_lock:
        update_balance_safe(uid, amount)


# =========================
# GAME CLASS
# =========================

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

    async def _build_embed(
        self,
        *,
        dealer_reveal: bool = False,
        animate_side: str | None = None,
    ) -> discord.Embed:

        user   = self.ctx.author
        avatar = user.display_avatar.url
        name   = user.display_name

        color_map = {
            "win":     COLOR_WIN,
            "lose":    COLOR_LOSE,
            "push":    COLOR_PUSH,
            "timeout": COLOR_LOSE,
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
            first_rank   = self.dealer_hand[0]["rank"] if self.dealer_hand else "?"
            first_val    = VALUES.get(first_rank, 0) if self.dealer_hand else 0
            d_header     = f"Dealer [{first_val}+?]"
            first_emoji  = self.dealer_hand[0]["emoji"] if self.dealer_hand else ""
            d_cards      = f"{first_emoji} {HIDDEN_CARD}"

        if animate_side == "dealer":
            d_cards += f"  {OPEN_CARD}"

        embed.add_field(name=d_header, value=d_cards or "\u200b", inline=False)

        # ── Player row ────────────────────────────────────────────────────────
        p_score  = calc_score(self.player_hand)
        p_header = f"{name} [{p_score}]"
        p_cards  = hand_str(self.player_hand)

        if animate_side == "player":
            p_cards += f"  {OPEN_CARD}"

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
            await safe_update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH — cả hai cùng vượt 21! (refund)"

        elif p_score > 21:
            # Player busted (dealer did not)
            self._outcome     = "lose"
            self._status_text = f"🎲 ~ {name} LOST {self.bet:,} coins!"

        elif p_natural and d_natural:
            # FIX #9: Both natural → push
            await safe_update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH — both Blackjack! (refund)"

        elif p_natural:
            # Natural blackjack → 2.5x payout
            payout = int(self.bet * 2.5)
            await safe_update_balance_safe(uid, payout)
            self._outcome     = "win"
            self._status_text = f"🎲 ~ ♠ BLACKJACK! {name} WON {payout - self.bet:,} coins!"

        elif p_score == 21:
            # Non-natural 21 (3+ cards) → also 2.5x bonus payout
            payout = int(self.bet * 2.5)
            await safe_update_balance_safe(uid, payout)
            self._outcome     = "win"
            self._status_text = f"🎲 ~  21 ĐIỂM! {name} WON {payout - self.bet:,} coins!"

        elif d_score > 21 or p_score > d_score:
            # Normal win → return bet + profit (bet × 2 total)
            await safe_update_balance_safe(uid, self.bet * 2)
            self._outcome     = "win"
            self._status_text = f"🎲 ~ {name} WON {self.bet:,} coins!"

        elif p_score < d_score:
            # Dealer wins
            self._outcome     = "lose"
            self._status_text = f"🎲 ~ {name} LOST {self.bet:,} coins!"

        else:
            # Push → refund bet only
            await safe_update_balance_safe(uid, self.bet)
            self._outcome     = "push"
            self._status_text = "🎲 ~ PUSH (refund bet)"

    # ── Game phases ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # Deduct the bet upfront; refunded on win/push via _resolve_payout.
        await safe_update_balance_safe(self.ctx.author.id, -self.bet)

        for _ in range(2):
            self.player_hand.append(draw_card())
            self.dealer_hand.append(draw_card())

        embed = await self._build_embed()
        self.message = await self.ctx.send(embed=embed)

        # Even if starting hand is 21, player must press Stand to confirm.

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
            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add",
                    timeout=GAME_TIMEOUT,
                    check=_check,
                )
            except asyncio.TimeoutError:
                await self._timeout_forfeit()
                return

            # FIX #5: Skip if lock is already held (spam protection)
            if self._lock.locked():
                try:
                    # FIX #3: Correct remove_reaction API
                    await self.message.remove_reaction(reaction.emoji, user)
                except Exception:
                    pass
                continue

            async with self._lock:
                # Re-check is_over after acquiring lock (race condition guard)
                if self.is_over:
                    break

                emoji = str(reaction.emoji)

                # ── Cancel detection ─────────────────────────────────────────
                # Give a 150 ms window: if the player removes the reaction
                # before we process it, treat the action as cancelled.
                _cancelled = False
                try:
                    await self.bot.wait_for(
                        "reaction_remove",
                        timeout=0.15,
                        check=lambda r, u: (
                            u.id == self.ctx.author.id
                            and r.message.id == self.message.id
                            and str(r.emoji) == emoji
                        ),
                    )
                    _cancelled = True
                except asyncio.TimeoutError:
                    pass   # Not removed → proceed normally

                if _cancelled:
                    continue  # Player cancelled — wait for next reaction

                # Remove the reaction so the player can re-use the same emoji
                try:
                    await self.message.remove_reaction(reaction.emoji, user)
                except Exception:
                    pass

                if emoji == HIT_EMOJI:
                    await self._player_hit()
                elif emoji == STAND_EMOJI:
                    self.is_over = True
                    await self.dealer_turn()
                    break

    async def _player_hit(self) -> None:
        try:
            embed = await self._build_embed(animate_side="player")
            await self.message.edit(embed=embed)
            await asyncio.sleep(0.35)
        except Exception:
            pass

        self.player_hand.append(draw_card())
        p_score = calc_score(self.player_hand)

        if p_score > 21:
            # Player busted → still run dealer turn to determine final result
            self.is_over = True
            await self.dealer_turn()
        else:
            # Update display; player continues (even if 21 — must press Stand)
            try:
                embed = await self._build_embed()
                await self.message.edit(embed=embed)
            except Exception:
                pass

    async def dealer_turn(self) -> None:
        self.is_over = True

        try:
            embed = await self._build_embed(dealer_reveal=True)
            await self.message.edit(embed=embed)
            await asyncio.sleep(0.6)
        except Exception:
            pass

        # Dealer stands on 14+ — draws while score < 14
        while True:
            d_score = calc_score(self.dealer_hand)

            if d_score > 21:
                break   # dealer busted
            if d_score >= 16:
                break   # stand on 14+

            # Dealer must draw → animate the deal
            try:
                embed = await self._build_embed(dealer_reveal=True, animate_side="dealer")
                await self.message.edit(embed=embed)
                await asyncio.sleep(0.4)
            except Exception:
                pass

            self.dealer_hand.append(draw_card())

            try:
                embed = await self._build_embed(dealer_reveal=True)
                await self.message.edit(embed=embed)
                await asyncio.sleep(0.4)
            except Exception:
                pass

        await self.end_game()

    async def end_game(self) -> None:
        self.is_over = True
        await self._resolve_payout()

        try:
            embed = await self._build_embed(dealer_reveal=True)
            await self.message.edit(embed=embed)
        except Exception:
            pass

        try:
            await self.message.clear_reactions()
        except Exception:
            pass

        # FIX #7: Always pop — moved here as authoritative cleanup point
        active_games.pop(self.ctx.author.id, None)

    async def _timeout_forfeit(self) -> None:
        """Player did not respond within GAME_TIMEOUT seconds."""
        self.is_over = True
        await self._resolve_payout(timed_out=True)

        # FIX #8: Do NOT reveal dealer hand on timeout
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

        # FIX #7: Always release session lock
        active_games.pop(self.ctx.author.id, None)


# =========================
# COMMAND COG
# =========================

class BlackjackCog(commands.Cog, name="Blackjack"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # FIX #10: Cooldown — 1 use per 3 seconds per user, prevents command spam
    @commands.cooldown(1, 3, commands.BucketType.user)
    @commands.command(name="bj", aliases=["blackjack"])
    async def blackjack(self, ctx: commands.Context, bet: str):
        """Play a game of Blackjack.  Usage: dtn bj <amount> | dtn bj all"""
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

        if bet_clean == "all":
            if balance <= 0:
                return await ctx.send("❌ You have no coins to bet!", delete_after=6)
            bet_amount = balance
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
            # Safety: refund bet on unexpected crash
            try:
                await safe_update_balance_safe(user_id, bet_amount)
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
            # FIX #7: Always clean up session — even if end_game already did it
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


async def setup(bot: commands.Bot):
    await bot.add_cog(BlackjackCog(bot))
