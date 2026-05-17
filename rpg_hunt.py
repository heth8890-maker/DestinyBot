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
    • 1%    → rơi ra crate (sub-roll xác định loại)
    • 1.75% → rơi ra coin (2 000 – 6 500)
- Tối đa 10 lần bonus / 6 tiếng, tự reset sau mỗi chu kỳ
- Thông báo bonus gửi là tin nhắn RIÊNG, không gắn vào embed

[SLASH COMMANDS]
- /hunt        → hunt bình thường
- /hunt log    → xem lịch sử
- /hunt bonus  → xem số lần bonus còn lại
"""

import time
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

# Import from rpg_core
from rpg_core import (
    get_item_by_id, get_weapon_by_id,
    roll_hunt_items, handle_egg,
    add_item, calc_hunt_cooldown, parse_effects,
    CRATES, get_base_id
)
from rpg_database import get_user, save_user, calc_hunt_exp, grant_weapon_exp
from rpg_addon import parse_effects_upgraded
from rpg_quest import add_quest_progress
from cash import update_balance_safe
from rpg_instance import decrease_durability

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
# ─────────────────────────────────────────────────────────
# PER-USER LOCK  (tránh race condition khi 2 lệnh đồng thời)
# ─────────────────────────────────────────────────────────
_USER_LOCKS: dict[str, asyncio.Lock] = {}

def _get_user_lock(uid: str) -> asyncio.Lock:
    if uid not in _USER_LOCKS:
        _USER_LOCKS[uid] = asyncio.Lock()
    return _USER_LOCKS[uid]


# ─────────────────────────────────────────────────────────
# SLASH CHECKS  (guild_only — prefix pipeline không áp dụng cho slash)
# ─────────────────────────────────────────────────────────
def _slash_guild_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Lệnh này chỉ dùng được trong server.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


# BONUS SYSTEM — CONSTANTS
# ─────────────────────────────────────────────────────────
CRATE_DROP_CHANCE   = 1       # %
COIN_DROP_CHANCE    = 1.75    # %
BONUS_MAX           = 10      # tối đa lần bonus mỗi chu kỳ
BONUS_RESET_SEC     = 6 * 3600  # 6 tiếng

# Bảng tỉ lệ crate (cộng dồn, tổng = 100%)
CRATE_POOL = [
    ("001", 67.9999),   # Common      — 68%
    ("002", 22),   # Rare        — 18%
    ("003",  5),   # Dark        —  3%
    ("004",  2),   # Soul        —  2%
    ("006",  1),   # (crate 006) —  9%
    ("009",  0.001)
]

# Tên fallback nếu CRATES chưa load được
_CRATE_FALLBACK = {
    "001": {"name": "Common Crate",  "emoji": "📦"},
    "002": {"name": "Rare Crate",    "emoji": "🟣"},
    "003": {"name": "Dark Crate",    "emoji": "🖤"},
    "004": {"name": "Soul Crate",    "emoji": "💠"},
    "006": {"name": "Crate 006",     "emoji": "🎁"},
     "009": {"name": "Crate 009",     "emoji": "🧺"},
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


def _roll_bonus_boosted(extra_chance: float) -> str | None:
    """
    Giống _roll_bonus nhưng mở rộng window theo treasure_hunt effect.
    extra_chance: giá trị thô từ effects (vd: 0.05), nhân *100 để ra %.
    Giữ nguyên tỉ lệ crate:coin trong window mới.
    Khi extra_chance = 0 → hoạt động y hệt _roll_bonus().
    """
    base_window  = CRATE_DROP_CHANCE + COIN_DROP_CHANCE   # 2.75
    total_window = base_window + extra_chance * 100

    roll = random.uniform(0, 100)
    if roll >= total_window:
        return None

    # Phân chia trong window giữ nguyên tỉ lệ gốc crate : coin
    crate_threshold = (CRATE_DROP_CHANCE / base_window) * total_window
    if roll < crate_threshold:
        return "crate"
    return "coin"


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
    info  = CRATES.get(crate_id) or _CRATE_FALLBACK.get(crate_id, {})
    emoji = info.get("emoji", "📦")
    name  = info.get("name",  f"Crate {crate_id}")
    return emoji, name


# ─────────────────────────────────────────────────────────
# EQUIPPED DISPLAY
# ─────────────────────────────────────────────────────────
def _equipped_display(equipped: list, user: dict | None = None) -> str:
    """Display 3 equipped slots with support for upgraded weapons."""
    lines  = []
    slots  = list(equipped) + [None] * (3 - len(equipped))
    wi_map = {
        wi["uid"]: wi
        for wi in (user or {}).get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    for i, wid in enumerate(slots[:3], 1):
        if wid is None:
            lines.append(f"  `[{i}]` — trống")
        elif wid in wi_map:
            wi = wi_map[wid]
            w  = get_weapon_by_id(wi.get("base_id", ""))
            nm = w["name"]  if w else wid
            em = w["emoji"] if w else "⚔️"
            lv     = wi.get("level", 1)
            broken = wi.get("broken", False)
            dur    = wi.get("durability", 0)
            dur_mx = wi.get("durability_max", 1)
            if broken:
                lines.append(f"  `[{i}]` {em} ~~**{nm}**~~ _(Lv {lv})_ **Broken**")
            else:
                lines.append(f"  `[{i}]` {em} **{nm}** _(Lv {lv} • {dur}/{dur_mx})_")
        else:
            w = get_weapon_by_id(get_base_id(wid))
            if w:
                lines.append(f"  `[{i}]` {w['emoji']} **{w['name']}**")
            else:
                lines.append(f"  `[{i}]` `{wid}`")

    return "\n".join(lines)


def _grant_exp_to_equipped(user: dict, found: list, equipped: list) -> None:
    """
    Grant EXP cho tất cả weapon đang equipped.
    Toàn bộ equipped đã là UID (đảm bảo bởi migrate_all_weapons_to_uid).
    Chỉ grant cho UID có dấu "-".
    Mutate user in-place. Không trả về gì.
    """
    if not found:
        return

    active = []
    for w in equipped:
        if w is None:
            continue
        if "-" not in str(w):
            print(f"⚠️  equipped weapon '{w}' is not a UID — skipping EXP")
            continue
        active.append(w)

    if not active:
        return

    total_exp = calc_hunt_exp(found)
    if total_exp <= 0:
        return

    exp_each = max(1, total_exp // len(active))
    for wid in active:
        grant_weapon_exp(user, wid, exp_each)


# ─────────────────────────────────────────────────────────
# INTERNAL: core hunt logic (shared prefix + slash)
# ─────────────────────────────────────────────────────────
async def _run_hunt(
    author_id: int,
    author_mention: str,
    display_name: str,
    send_fn,          # async (embed=) → None
    send_bonus_fn,    # async (content=) → None  (tin nhắn riêng)
) -> None:
    """
    Toàn bộ luồng hunt dùng chung cho prefix và slash.
    send_fn       : gửi embed kết quả
    send_bonus_fn : gửi tin nhắn bonus riêng
    """
    uid = str(author_id)

    async with _get_user_lock(uid):
        try:
            # ── Load data ───────────────────────────────────────
            try:
                user, _ = get_user(uid)
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to get user data: `{e}`")

            try:
                equipped = user.get("equipped", [])
                if not isinstance(equipped, list) or len(equipped) != 3:
                    equipped = [None, None, None]
                    user["equipped"] = equipped
            except Exception as e:
                return await send_fn(content=f"{ERR} | Invalid equipped weapons data: `{e}`")

            # ── Cooldown ────────────────────────────────────────
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
                    return await send_fn(content=f"{ERR} | Hồi chiêu còn **{remaining}s**.")
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to check cooldown: `{e}`")

            # ── Effects & items ─────────────────────────────────
            try:
                effects_full = parse_effects_upgraded(equipped, user)
            except Exception as e:
                print(f"⚠️  Warning: parse_effects_upgraded failed: {e}, using fallback")
                effects_full = parse_effects(equipped, user)

            try:
                found = roll_hunt_items(equipped, user)
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to roll hunt items: `{e}`")

            try:
                if found:
                    _grant_exp_to_equipped(user, found, equipped)
            except Exception as e:
                print(f"⚠️  Warning: weapon exp grant failed: {e}")

            try:
                just_broken = decrease_durability(user, equipped, effects_full)
            except Exception as e:
                just_broken = []
                print(f"⚠️  Warning: durability decrease failed: {e}")

            # ── Build embed ─────────────────────────────────────
            try:
                embed = discord.Embed(
                    title=f"{SKULL_EMOJI}  {display_name} đi săn!",
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
                    active_fx = [k for k, v in effects_full.items() if v]
                    if active_fx:
                        embed.set_footer(
                            text="<:Effect:1495466103047061679> Hiệu ứng: " + ", ".join(active_fx[:5])
                        )
                    else:
                        embed.set_footer(
                            text=f"Cooldown: {HUNT_CD_SEC}s  |  Trang bị weapon để tăng hiệu quả!"
                        )
                except Exception as e:
                    print(f"⚠️  Warning: footer failed: {e}")
                    embed.set_footer(text=f"Cooldown: {HUNT_CD_SEC}s")

            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to build response: `{e}`")

            # ── Hunt log & cooldown ─────────────────────────────
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

            # ── Bonus roll ──────────────────────────────────────
            bonus_msg = None
            coins     = None

            try:
                bonus     = _get_bonus_data(user, now)
                can_bonus = bonus["count"] < BONUS_MAX

                if can_bonus:
                    treasure_hunt_val = effects_full.get("treasure_hunt", 0.0)
                    bonus_type = _roll_bonus_boosted(treasure_hunt_val)

                    if bonus_type == "crate":
                        crate_id  = _roll_crate_id()
                        crate_key = f"crate_{crate_id}"
                        add_item(user, crate_key)
                        bonus["count"] += 1

                        emoji, name  = _crate_display(crate_id)
                        remaining    = BONUS_MAX - bonus["count"]
                        reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msg = (
                            f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra {emoji} **{name}**!\n"
                            f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                            f"(reset sau {reset_in_min} phút)"
                        )

                    elif bonus_type == "coin":
                        coins = random.randint(2000, 6500)
                        bonus["count"] += 1

                        remaining    = BONUS_MAX - bonus["count"]
                        reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msg = (
                            f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                            f"**{coins:,}** {COIN_EMOJI} **Coin**!\n"
                            f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                            f"(reset sau {reset_in_min} phút)"
                        )

            except Exception as e:
                print(f"⚠️  Warning: bonus roll failed: {e}")

            # ── Save ────────────────────────────────────────────
            try:
                ok = save_user(uid, user)
                if not ok:
                    return await send_fn(content=f"{ERR} | Failed to save data.")
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to save data: `{e}`")

            if coins is not None:
                try:
                    await update_balance_safe(author_id, coins)
                except Exception as e:
                    print(f"⚠️  Warning: update_balance_safe (bonus coin) failed: {e}")

            # ── Quest ───────────────────────────────────────────
            try:
                add_quest_progress(author_id, "hunts")
                if found:
                    add_quest_progress(author_id, "items_collected", len(found))
                    rare_count = sum(
                        1 for it in found
                        if it.get("rarity") in ("rare", "epic", "legendary")
                    )
                    if rare_count > 0:
                        add_quest_progress(author_id, "rare_collected", rare_count)
            except Exception as e:
                print(f"⚠️  Warning: quest progress update failed: {e}")

            # ── Send ────────────────────────────────────────────
            try:
                await send_fn(embed=embed)
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to send response: `{e}`")

            if bonus_msg:
                try:
                    await send_bonus_fn(content=bonus_msg)
                except Exception as e:
                    print(f"⚠️  Warning: failed to send bonus message: {e}")

        except Exception as e:
            print(f"❌ CRITICAL ERROR in hunt command: {type(e).__name__}: {e}")
            try:
                await send_fn(content=f"❌ **Critical Error**: `{e}`\nPlease contact the bot owner.")
            except Exception as send_err:
                print(f"❌ Failed to send error message: {send_err}")


async def _run_hunt_bonus(author_id: int, display_name: str, send_fn) -> None:
    """Core logic hunt bonus — dùng chung prefix + slash."""
    try:
        uid    = str(author_id)
        user, _ = get_user(uid)
        now    = int(time.time())

        old_reset  = user.get("hunt_bonus", {}).get("reset_at", 0)
        bonus      = _get_bonus_data(user, now)
        needs_save = (bonus["reset_at"] != old_reset)

        remaining    = BONUS_MAX - bonus["count"]
        reset_in_sec = max(0, bonus["reset_at"] - now)
        h, rem       = divmod(reset_in_sec, 3600)
        m            = rem // 60

        if needs_save:
            try:
                save_user(uid, user)
            except Exception as e:
                print(f"⚠️  Warning: hunt_bonus save failed: {e}")

        await send_fn(
            content=(
                f"🎁 **Bonus hunt của {display_name}**\n"
                f"• Đã dùng: **{bonus['count']}/{BONUS_MAX}** lần\n"
                f"• Còn lại: **{remaining}** lần\n"
                f"• Reset sau: **{h}h {m}m**"
            )
        )

    except Exception as e:
        print(f"❌ Error in hunt_bonus: {type(e).__name__}: {e}")
        await send_fn(content=f"❌ Error: `{e}`")


# ─────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────
class RPGHunt(commands.Cog):
    """Hunt command with comprehensive error handling."""

    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════
    # PREFIX COMMANDS
    # ══════════════════════════════════════════════════════

    @commands.group(name="hunt", aliases=["h"], invoke_without_command=True)
    async def hunt(self, ctx):
        """dtn hunt — đi săn vật phẩm."""
        await _run_hunt(
            author_id      = ctx.author.id,
            author_mention = ctx.author.mention,
            display_name   = ctx.author.display_name,
            send_fn        = lambda content=None, embed=None, **_: ctx.send(content=content, embed=embed),
            send_bonus_fn  = lambda content=None, **_: ctx.send(content=content),
        )

    @hunt.command(name="bonus")
    async def hunt_bonus(self, ctx):
        """dtn hunt bonus — xem số lần bonus còn lại trong chu kỳ."""
        await _run_hunt_bonus(
            author_id    = ctx.author.id,
            display_name = ctx.author.display_name,
            send_fn      = lambda content=None, **_: ctx.send(content=content),
        )

    # ══════════════════════════════════════════════════════
    # SLASH COMMANDS
    # ══════════════════════════════════════════════════════

    hunt_group = app_commands.Group(
        name="hunt",
        description="Đi săn vật phẩm",
    )

    @hunt_group.command(name="go", description="Đi săn vật phẩm")
    @_slash_guild_only()
    async def slash_hunt(self, interaction: discord.Interaction):
        """/hunt go"""
        await interaction.response.defer()

        async def _send(content=None, embed=None, **_):
            await interaction.followup.send(content=content, embed=embed)

        async def _send_bonus(content=None, **_):
            await interaction.followup.send(content=content)

        await _run_hunt(
            author_id      = interaction.user.id,
            author_mention = interaction.user.mention,
            display_name   = interaction.user.display_name,
            send_fn        = _send,
            send_bonus_fn  = _send_bonus,
        )

    @hunt_group.command(name="bonus", description="Xem số lần bonus hunt còn lại trong chu kỳ")
    @_slash_guild_only()
    async def slash_hunt_bonus(self, interaction: discord.Interaction):
        """/hunt bonus"""
        await interaction.response.defer()

        async def _send(content=None, **_):
            await interaction.followup.send(content=content)

        await _run_hunt_bonus(
            author_id    = interaction.user.id,
            display_name = interaction.user.display_name,
            send_fn      = _send,
        )


# ─────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────
async def setup(bot):
    """Setup Cog."""
    cog = RPGHunt(bot)
    await bot.add_cog(cog)
    # Xoá command cũ trước (idempotent khi reload)
    bot.tree.remove_command("hunt", type=discord.AppCommandType.chat_input)
    bot.tree.add_command(cog.hunt_group)


async def teardown(bot):
    """Teardown Cog — dọn slash command khi unload/reload."""
    bot.tree.remove_command("hunt", type=discord.AppCommandType.chat_input)
