"""
===== FILE: rpg_question.py =====
Discord Cog cho hệ thống Daily Quest.
Tách từ rpg_game.py.

Commands:
  dtn quest          — xem 1-2 quest hôm nay
  dtn quest reward   — nhận thưởng
"""

import discord
from discord.ext import commands

from rpg_quest import (
    get_current_quests,
    reset_quest,
    should_reset_quest,
    add_quest_progress,
    claim_quest_reward,
)
from cash import update_balance, get_balance

COIN_EMOJI = "<:Coin:1495831576397742241>"
ERR  = "<:X_:1495466670616219819>"
OK   = "<:Tick:1495466684520206528>"


def _progress_bar(progress: int, target: int, length: int = 10) -> str:
    filled = int((progress / target) * length) if target > 0 else 0
    filled = min(filled, length)
    return "█" * filled + "░" * (length - filled)


class RPGQuestion(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="quest", invoke_without_command=True)
    async def quest(self, ctx):
        """Xem nhiệm vụ hàng ngày (1–2 quest)."""
        uid = ctx.author.id

        if should_reset_quest(uid):
            reset_quest(uid)

        quests = get_current_quests(uid)
        if not quests:
            reset_quest(uid)
            quests = get_current_quests(uid)
        if not quests:
            return await ctx.send(f"{ERR} | Không thể tải quest. Thử lại sau.")

        all_claimed    = all(q["claimed"]   for q in quests)
        any_completed  = any(q["completed"] and not q["claimed"] for q in quests)

        embed = discord.Embed(
            title=f"<:Info:1496098636247863491> | Daily Quest — {ctx.author.display_name}",
            description=f"Hôm nay có **{len(quests)}** nhiệm vụ. Reset mỗi 24h.",
            color=0x4CAF50 if all_claimed else (0xFFC107 if any_completed else 0x5865F2),
        )

        for q in quests:
            pct = int(q["progress"] / q["target"] * 100) if q["target"] > 0 else 0
            bar = _progress_bar(q["progress"], q["target"])

            if q["claimed"]:
                status = "<:2245:1493575277605949480> | Đã nhận thưởng"
            elif q["completed"]:
                status = f"<:Tick:1495466684520206528> Hoàn thành! Dùng `dtn quest reward`"
            else:
                status = f"🔄 {q['progress']}/{q['target']} ({pct}%)"

            # ✅ SỬA DUY NHẤT Ở ĐÂY
            icon = "<:Tick:1495466684520206528>" if q["completed"] else ("🏆" if q["claimed"] else "🔲")

            embed.add_field(
                name=f"{icon} {q['name']}",
                value=(
                    f"{q['description']}\n"
                    f"`{bar}` {pct}%\n"
                    f"<:2245:1493575277605949480> | {q['reward']:,} {COIN_EMOJI}  •  {status}"
                ),
                inline=False,
            )

        pending_reward = sum(q["reward"] for q in quests if q["completed"] and not q["claimed"])
        if pending_reward > 0:
            embed.set_footer(
                text=f"Phần thưởng chờ nhận: {pending_reward:,} {COIN_EMOJI}  │  dtn quest reward"
            )
        else:
            embed.set_footer(text="Số dư: {:,} {}".format(get_balance(uid), COIN_EMOJI))

        await ctx.send(embed=embed)

    @quest.command(name="reward")
    async def quest_reward(self, ctx):
        """Nhận tất cả phần thưởng từ quest đã hoàn thành."""
        ok, msg, reward = claim_quest_reward(ctx.author.id)
        if ok:
            update_balance(ctx.author.id, reward)
            embed = discord.Embed(
                title="<:Coin:1495831576397742241> | Nhận Phần Thưởng!",
                description=f"{msg}\n\n<:2245:1493575277605949480> +**{reward:,}** {COIN_EMOJI}",
                color=0xFFD700,
            )
            embed.set_footer(
                text=f"Số dư: {get_balance(ctx.author.id):,} {COIN_EMOJI}  │  dtn quest để xem quest còn lại"
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{ERR} | {msg}")


async def setup(bot):
    await bot.add_cog(RPGQuestion(bot))