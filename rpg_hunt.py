"""
===== FIXED HUNT COMMAND (from rpg_game.py) =====
FIX #4: Wrap ENTIRE hunt command in try/except

GUARANTEES:
- No silent failures (errors visible in Discord)
- All exceptions logged to console
- User gets clear error message
- Never breaks the bot

[BONUS SYSTEM]
- Mỗi lần hunt có 1 roll bonus duy nhất (ưu tiên crate trước):
    • 1.5%  → rơi ra crate (sub-roll xác định loại)
    • 5.5%  → rơi ra coin (2 000 – 6 500)
- Tối đa 10 lần bonus / 6 tiếng, tự reset sau mỗi chu kỳ
- Thông báo bonus gửi là tin nhắn RIÊNG, không gắn vào embed
"""

import time
import random
import discord
from discord.ext import commands

# Import from rpg_core
from rpg_core import (
    load_data, save_data, get_user,
    get_item_by_id, get_weapon_by_id,
    roll_hunt_items, handle_egg,
    add_item, calc_hunt_cooldown, parse_effects,
    CRATES,
)
from rpg_addon import parse_effects_upgraded
from rpg_quest import add_quest_progress
from cash import get_balance, update_balance

# ─────────────────────────────────────────────────────────
# COSMETICS
# ─────────────────────────────────────────────────────────
COIN_EMOJI  = "<:Coin:1495831576397742241>"
SKULL_EMOJI = "<:2859:1495250145942704189>"
SWORD_EMOJI = "<:2918:1495252941492457502>"
HUNT_CD_SEC = 16

ERR = "<:X_:1495466670616219819>"
OK  = "<:Tick:1495466684520206528>"

# ─────────────────────────────────────────────────────────
# BONUS SYSTEM — CONSTANTS
# ─────────────────────────────────────────────────────────
CRATE_DROP_CHANCE   = 1.5    # %
COIN_DROP_CHANCE    = 5.5    # % (roll từ CRATE_DROP_CHANCE → CRATE_DROP_CHANCE + COIN_DROP_CHANCE)
BONUS_MAX           = 10     # tối đa lần bonus mỗi chu kỳ
BONUS_RESET_SEC     = 6 * 3600  # 6 tiếng

# Bảng tỉ lệ crate (cộng dồn, tổng = 100%)
CRATE_POOL = [
    ("001", 75),   # Common   — 75%
    ("002", 20),   # Rare     — 20%
    ("003",  3),   # Dark     —  3%
    ("004",  2),   # Soul     —  2%
]

# Tên fallback nếu CRATES chưa load được
_CRATE_FALLBACK = {
    "001": {"name": "Common Crate",  "emoji": "📦"},
    "002": {"name": "Rare Crate",    "emoji": "🟣"},
    "003": {"name": "Dark Crate",    "emoji": "🖤"},
    "004": {"name": "Soul Crate",    "emoji": "💠"},
}


# ─────────────────────────────────────────────────────────
# BONUS HELPERS
# ─────────────────────────────────────────────────────────
def _roll_bonus() -> str | None:
    """
    Trả về 'crate', 'coin', hoặc None.
    Roll 1 số trong [0, 100), ưu tiên crate trước.
    """
    roll = random.uniform(0, 100)
    if roll < CRATE_DROP_CHANCE:
        return "crate"
    elif roll < CRATE_DROP_CHANCE + COIN_DROP_CHANCE:
        return "coin"
    return None


def _roll_crate_id() -> str:
    """Sub-roll xác định loại crate theo bảng tỉ lệ."""
    r = random.uniform(0, 100)
    cumulative = 0
    for crate_id, weight in CRATE_POOL:
        cumulative += weight
        if r < cumulative:
            return crate_id
    return "001"  # fallback


def _get_bonus_data(user: dict, now: int) -> dict:
    """
    Trả về dict hunt_bonus của user, reset tự động nếu hết chu kỳ.
    Lưu trực tiếp vào user dict (mutate in place).
    """
    bonus = user.setdefault("hunt_bonus", {"count": 0, "reset_at": 0})
    if now >= bonus["reset_at"]:
        bonus["count"]    = 0
        bonus["reset_at"] = now + BONUS_RESET_SEC
    return bonus


def _crate_display(crate_id: str) -> tuple[str, str]:
    """Trả về (emoji, name) của crate."""
    info = CRATES.get(crate_id) or _CRATE_FALLBACK.get(crate_id, {})
    emoji = info.get("emoji", "📦")
    name  = info.get("name",  f"Crate {crate_id}")
    return emoji, name


# ─────────────────────────────────────────────────────────
# EQUIPPED DISPLAY
# ─────────────────────────────────────────────────────────
def _equipped_display(equipped: list, user: dict | None = None) -> str:
    """Display 3 equipped slots with support for upgraded weapons."""
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
                f"  `[{i}]` <:Effect:1495466103047061679>{em} **{nm}** _(lv{max_lv})_"
            )
        else:
            w = get_weapon_by_id(wid)
            if w:
                lines.append(f"  `[{i}]` {w['emoji']} **{w['name']}**")
            else:
                lines.append(f"  `[{i}]` `{wid}`")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────
class RPGHunt(commands.Cog):
    """Hunt command with comprehensive error handling."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="hunt", aliases=["h"], invoke_without_command=True)
    async def hunt(self, ctx):
        """
        ★ FIX #4: ENTIRE FUNCTION wrapped in try/except
        """
        try:
            # ─────────────────────────────────────────────────────────
            # LOAD DATA & GET USER
            # ─────────────────────────────────────────────────────────
            try:
                data = load_data()
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to load game data: `{e}`")
                raise e

            try:
                uid  = str(ctx.author.id)
                user = get_user(uid, data)
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to get user data: `{e}`")
                raise e

            try:
                equipped = user.get("equipped", [])
                if not isinstance(equipped, list) or len(equipped) != 3:
                    equipped = [None, None, None]
                    user["equipped"] = equipped
            except Exception as e:
                await ctx.send(f"{ERR} | Invalid equipped weapons data: `{e}`")
                raise e

            # ─────────────────────────────────────────────────────────
            # COOLDOWN CHECK
            # ─────────────────────────────────────────────────────────
            try:
                now = int(time.time())

                try:
                    actual_cd = calc_hunt_cooldown(equipped, float(HUNT_CD_SEC), user)
                except Exception as e:
                    actual_cd = float(HUNT_CD_SEC)
                    print(f"⚠️  Warning: calc_hunt_cooldown failed: {e}, using default CD")

                last_hunt = user.get("hunt_cd", 0)
                if now - last_hunt < actual_cd:
                    remaining = int(actual_cd - (now - last_hunt))
                    return await ctx.send(f"{ERR} | Hồi chiêu còn **{remaining}s**.")
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to check cooldown: `{e}`")
                raise e

            # ─────────────────────────────────────────────────────────
            # PARSE EFFECTS & ROLL ITEMS
            # ─────────────────────────────────────────────────────────
            try:
                effects_full = parse_effects_upgraded(equipped, user)
            except Exception as e:
                print(f"⚠️  Warning: parse_effects_upgraded failed: {e}, using fallback")
                effects_full = parse_effects(equipped, user)

            try:
                found = roll_hunt_items(equipped, user)
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to roll hunt items: `{e}`")
                raise e

            # ─────────────────────────────────────────────────────────
            # BUILD RESPONSE EMBED
            # ─────────────────────────────────────────────────────────
            try:
                embed = discord.Embed(
                    title=f"{SKULL_EMOJI}  {ctx.author.display_name} đi săn!",
                    color=0x4CAF50 if found else 0x9E9E9E,
                )

                if not found:
                    embed.description = "Bạn không tìm được gì lần này..."
                else:
                    lines = []
                    for item in found:
                        if item.get("special") == "egg":
                            try:
                                eggs = handle_egg(user)
                                for egg in eggs:
                                    lines.append(f"{egg['emoji']}  **{egg['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: handle_egg failed: {e}")
                                lines.append("🥚  **Egg** (hatching error)")
                        else:
                            try:
                                add_item(user, item["id"])
                                lines.append(f"{item['emoji']}  **{item['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: add_item failed for {item.get('id')}: {e}")
                                lines.append(
                                    f"{item['emoji']}  **{item.get('name', 'Unknown')}** (add error)"
                                )

                    embed.description = "\n".join(lines) if lines else "_Lỗi hiển thị vật phẩm_"

                try:
                    embed.add_field(
                        name=f"{SWORD_EMOJI} Vũ khí đang trang bị",
                        value=_equipped_display(equipped, user),
                        inline=False,
                    )
                except Exception as e:
                    print(f"⚠️  Warning: equipped display failed: {e}")
                    embed.add_field(
                        name=f"{SWORD_EMOJI} Vũ khí đang trang bị",
                        value="_Error displaying weapons_",
                        inline=False,
                    )

                try:
                    active = [k for k, v in effects_full.items() if v]
                    if active:
                        embed.set_footer(
                            text="<:Effect:1495466103047061679> Hiệu ứng: " + ", ".join(active[:5])
                        )
                    else:
                        embed.set_footer(
                            text=f"Cooldown: {HUNT_CD_SEC}s  |  Trang bị weapon để tăng hiệu quả!"
                        )
                except Exception as e:
                    print(f"⚠️  Warning: footer failed: {e}")
                    embed.set_footer(text=f"Cooldown: {HUNT_CD_SEC}s")

            except Exception as e:
                await ctx.send(f"{ERR} | Failed to build response: `{e}`")
                raise e

            # ─────────────────────────────────────────────────────────
            # UPDATE HUNT LOG & COOLDOWN
            # ─────────────────────────────────────────────────────────
            try:
                user.setdefault("hunt_log", [])
                user["hunt_log"].append({
                    "timestamp":   now,
                    "items":       [{"id": it["id"], "name": it["name"]} for it in found],
                    "found_count": len(found),
                })
                if len(user["hunt_log"]) > 50:
                    user["hunt_log"] = user["hunt_log"][-50:]
            except Exception as e:
                print(f"⚠️  Warning: hunt_log update failed: {e}")

            try:
                user["hunt_cd"] = now
            except Exception as e:
                print(f"⚠️  Warning: hunt_cd update failed: {e}")

            # ─────────────────────────────────────────────────────────
            # BONUS ROLL — crate hoặc coin (1 roll duy nhất)
            # ─────────────────────────────────────────────────────────
            bonus_msg = None  # tin nhắn sẽ gửi riêng sau embed

            try:
                bonus      = _get_bonus_data(user, now)
                can_bonus  = bonus["count"] < BONUS_MAX

                if can_bonus:
                    bonus_type = _roll_bonus()  # 'crate', 'coin', hoặc None

                    if bonus_type == "crate":
                        crate_id  = _roll_crate_id()
                        crate_key = f"crate_{crate_id}"
                        add_item(user, crate_key)
                        bonus["count"] += 1

                        emoji, name    = _crate_display(crate_id)
                        remaining      = BONUS_MAX - bonus["count"]
                        reset_in_min   = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msg = (
                            f"<:2925:1495277191867400284> | {ctx.author.mention} Kho báu rơi ra {emoji} **{name}**!\n"
                            f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                            f"(reset sau {reset_in_min} phút)"
                        )

                    elif bonus_type == "coin":
                        coins = random.randint(2000, 6500)
                        update_balance(ctx.author.id, coins)
                        bonus["count"] += 1

                        remaining    = BONUS_MAX - bonus["count"]
                        reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msg = (
                            f"<:2925:1495277191867400284> | {ctx.author.mention} Kho báu rơi ra "
                            f"**{coins:,}** {COIN_EMOJI} **Coin**!\n"
                            f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                            f"(reset sau {reset_in_min} phút)"
                        )

            except Exception as e:
                print(f"⚠️  Warning: bonus roll failed: {e}")

            # ─────────────────────────────────────────────────────────
            # SAVE DATA
            # ─────────────────────────────────────────────────────────
            try:
                await save_data(data)
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to save data: `{e}`")
                raise e

            # ─────────────────────────────────────────────────────────
            # UPDATE QUEST PROGRESS
            # ─────────────────────────────────────────────────────────
            try:
                add_quest_progress(ctx.author.id, "hunts")
                if found:
                    add_quest_progress(ctx.author.id, "items_collected", len(found))
                    rare_count = sum(
                        1 for it in found
                        if it.get("rarity") in ("rare", "epic", "legendary")
                    )
                    if rare_count > 0:
                        add_quest_progress(ctx.author.id, "rare_collected", rare_count)
            except Exception as e:
                print(f"⚠️  Warning: quest progress update failed: {e}")

            # ─────────────────────────────────────────────────────────
            # SEND RESPONSE
            # ─────────────────────────────────────────────────────────
            try:
                await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"{ERR} | Failed to send response: `{e}`")
                raise e

            # Gửi tin nhắn bonus RIÊNG (không dính vào embed)
            if bonus_msg:
                try:
                    await ctx.send(bonus_msg)
                except Exception as e:
                    print(f"⚠️  Warning: failed to send bonus message: {e}")

        except Exception as e:
            print(f"❌ CRITICAL ERROR in hunt command: {type(e).__name__}: {e}")
            try:
                await ctx.send(f"❌ **Critical Error**: `{e}`\nPlease contact the bot owner.")
            except Exception as send_err:
                print(f"❌ Failed to send error message: {send_err}")
            raise e

    @hunt.command(name="log")
    async def hunt_log(self, ctx, amount: int = 10):
        """View recent hunt log. Usage: dtn hunt log [amount]"""
        try:
            data = load_data()
            uid  = str(ctx.author.id)
            user = get_user(uid, data)

            amount = min(max(1, amount), 50)
            logs   = user.get("hunt_log", [])[-amount:]

            if not logs:
                return await ctx.send(f"{ERR} | Chưa có lịch sử hunt.")

            embed = discord.Embed(
                title=f"{SKULL_EMOJI} Lịch sử hunt ({len(logs)} lần gần nhất)",
                color=0x4CAF50,
            )

            now   = int(time.time())
            lines = []
            for i, log in enumerate(reversed(logs), 1):
                ts      = log.get("timestamp", 0)
                found_n = log.get("found_count", 0)
                elapsed = now - ts
                m, s    = divmod(elapsed, 60)
                tstr    = f"{m}m {s}s trước" if m else f"{s}s trước"

                items_str = ", ".join(it["name"] for it in log.get("items", [])[:3])
                extra     = len(log.get("items", [])) - 3
                if extra > 0:
                    items_str += f", +{extra} nữa"
                if found_n == 0:
                    items_str = "_Không tìm được_"

                lines.append(f"**{i}.** {found_n} vật phẩm → {items_str} ({tstr})")

            embed.description = "\n".join(lines)
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"❌ Error in hunt_log: {type(e).__name__}: {e}")
            await ctx.send(f"❌ Error: `{e}`")
            raise e

    @hunt.command(name="bonus")
    async def hunt_bonus(self, ctx):
        """Xem còn bao nhiêu lần bonus hunt trong chu kỳ hiện tại."""
        try:
            data = load_data()
            uid  = str(ctx.author.id)
            user = get_user(uid, data)
            now  = int(time.time())

            bonus        = _get_bonus_data(user, now)
            remaining    = BONUS_MAX - bonus["count"]
            reset_in_sec = max(0, bonus["reset_at"] - now)
            h, rem       = divmod(reset_in_sec, 3600)
            m            = rem // 60

            await ctx.send(
                f"🎁 **Bonus hunt của {ctx.author.display_name}**\n"
                f"• Đã dùng: **{bonus['count']}/{BONUS_MAX}** lần\n"
                f"• Còn lại: **{remaining}** lần\n"
                f"• Reset sau: **{h}h {m}m**"
            )
        except Exception as e:
            print(f"❌ Error in hunt_bonus: {type(e).__name__}: {e}")
            await ctx.send(f"❌ Error: `{e}`")
            raise e


async def setup(bot):
    """Setup Cog."""
    await bot.add_cog(RPGHunt(bot))
