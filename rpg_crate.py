"""
===== FILE: rpg_crate.py =====
Discord Cog cho hệ thống Crate.
Tách từ rpg_game.py để giảm độ phức tạp.

HỆ MỚI:
- Crate chỉ spawn BASE_ID (stackable)
- KHÔNG tạo UID tại đây
- UID chỉ xuất hiện ở hệ nâng cấp / enchant / upgrade

Commands:
  dtn crate <id>             — xem chi tiết & drop rate crate
  dtn crate buy <id> [amount]
  dtn crate open <id> [amount|all]
"""

import asyncio
import time
import random

import discord
from discord.ext import commands
from discord import app_commands

from rpg_core import (
    add_item, remove_item,
    add_weapon, roll_weapon,
    CRATES, RARITY_COLOR, RARITY_LABEL,
)
# ✅ Dùng get_user / save_user từ rpg_database (MongoDB) thay vì load_data / save_data JSON
from rpg_database import get_user, save_user

from rpg_weapon_data import (
    roll_rare_crate_weapon,
    roll_dark_crate_weapon,
    roll_paradise_crate_weapon,
    roll_book_of_godly_weapon,
)
from rpg_quest import add_quest_progress
from rpg_instance import PASSIVE_INDEX, resolve_passive
from cash import update_balance_safe, get_balance


COIN_EMOJI  = "<:Coin:1495831576397742241>"
CHEST_EMOJI = "<:2925:1495277191867400284>"
ERR         = "<:X_:1495466670616219819>"
OK          = "<:Tick:1495466684520206528>"
LIGHT_ICON  = "<a:Light:1505457919188008980>"

# Rarity tiers that trigger a congratulation banner
_CONGRAT_TIERS: dict[str, str] = {
    "legendary": f"{LIGHT_ICON} **Congratulation!** {LIGHT_ICON}",
    "special":   f"{LIGHT_ICON} **Congratulation!** {LIGHT_ICON}",
    "mythical":  f"{LIGHT_ICON} **Congratulation!!!** {LIGHT_ICON}",
}


def _congrat_line(rarity: str) -> str | None:
    """Return the congratulation banner for high-rarity drops, or None."""
    return _CONGRAT_TIERS.get(rarity.lower())

# Open icon shown on each result line, keyed by crate_id.
# Soul crate (004) uses its own icon inline — not in this table.
CRATE_OPEN_ICON: dict[str, str] = {
    "001": "<:Uncomon:1495277191867400284>",
    "002": "<:Craterare:1496191910765920406>",
    "003": "<:Darkcrateopen:1498988761936302210>",
    "006": "<:Paradise_crate_open:1505052527157051454>",
    "009": "<:Paradise_crate_open:1505052527157051454>",
}

CRATE_OPEN_COOLDOWN = 9   # seconds
CRATE_OPEN_MAX      = 12   # silent cap per batch


def _rarity_tier(rarity: str) -> str:
    return RARITY_LABEL.get(rarity, rarity)


def _parse_amount(raw: str, owned: int) -> int:
    """
    Parse the [amount|all] argument.
    - "all"  → min(owned, CRATE_OPEN_MAX)
    - int    → min(int, CRATE_OPEN_MAX)
    - bad    → 1
    Silently clamps; never raises.
    """
    if raw.lower() == "all":
        count = owned
    else:
        try:
            count = int(raw)
        except ValueError:
            count = 1
    # Silently cap at 6, then cap at what the player actually owns
    return max(1, min(count, CRATE_OPEN_MAX, owned))


def _get_passive_emoji(user: dict, new_uid: str) -> str:
    """
    Look up the passive emoji for a freshly-added weapon instance.
    Returns empty string on any failure so the output line still sends.
    """
    wi = next(
        (w for w in user.get("weapon_instances", []) if w.get("uid") == new_uid),
        None,
    )
    if not wi:
        return ""
    passive_stored = wi.get("passive")
    if not passive_stored:
        return ""
    resolved = resolve_passive(passive_stored)
    if not resolved:
        return ""
    return resolved.get("emoji", "")


class RPGCrate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="crate", invoke_without_command=True)
    async def crate(self, ctx, crate_id: str = None):
        # ── dtn crate <id> → chi tiết crate + drop rate + lệnh nhanh ──
        if crate_id is not None:
            if crate_id not in CRATES:
                return await ctx.send(
                    f"{ERR} | Crate `{crate_id}` không tồn tại. Xem: `dtn shop crate`"
                )

            crate_data = CRATES[crate_id]
            uid  = str(ctx.author.id)
            user, _ = get_user(uid)
            owned = user["inv"].get(f"crate_{crate_id}", 0)

            # Màu embed theo rarity
            rarity = crate_data.get("rarity", "common")
            color  = RARITY_COLOR.get(rarity, 0x7289DA)

            embed = discord.Embed(
                title=f"{crate_data['emoji']}  {crate_data['name']}",
                description=crate_data["description"],
                color=color,
            )
            embed.add_field(
                name="<:2245:1493575277605949480> | Price",
                value=(
                    "**Không bán** _(drop từ Crate of Paradise 006)_"
                    if crate_id == "009"
                    else f"**{crate_data['price']:,}** {COIN_EMOJI}"
                ),
                inline=True,
            )
            embed.add_field(
                name="Commands",
                value=(
                    f"`dtn crate buy {crate_id}` — mua 1 crate\n"
                    f"`dtn crate buy {crate_id} <n>` — mua n crate\n"
                    f"`dtn crate open {crate_id}` — mở 1 crate\n"
                    f"`dtn crate open {crate_id} all` — mở tất cả "
                    f"*(tối đa {CRATE_OPEN_MAX})*"
                ),
                inline=False,
            )
            embed.set_footer(text=f"ID: {crate_id}  •  dtn shop crate — xem tất cả crate")
            return await ctx.send(embed=embed)

        # ── dtn crate → help ──
        await ctx.send(
            f"{CHEST_EMOJI} **Lệnh crate:**\n"
            "• `dtn crate <id>` — xem chi tiết & drop rate crate\n"
            "• `dtn crate buy <id> [amount]` — mua crate\n"
            "• `dtn crate open <id> [amount|all]` — mở crate nhận weapon\n"
            "• `dtn shop crate` — xem danh sách crate & drop rate"
        )

    # =========================
    # BUY CRATE
    # =========================
    @crate.command(name="buy")
    async def crate_buy(self, ctx, crate_id: str, amount: int = 1):
        if crate_id not in CRATES:
            return await ctx.send(
                f"{ERR} | Crate `{crate_id}` không tồn tại. Xem: `dtn shop crate`"
            )

        # Crate 009 (Book of Godly) chỉ drop từ Crate of Paradise — không bán trực tiếp
        if crate_id == "009":
            return await ctx.send(
                f"{ERR} | **Book of Godly** không thể mua trực tiếp.\n"
                f"Mở <:Paradise_crate:1505052530613289080> **Crate of Paradise** (ID: `006`) để có cơ hội nhận."
            )

        if amount < 1:
            return await ctx.send(f"{ERR} | Số lượng phải >= 1.")

        crate = CRATES[crate_id]
        price = crate["price"] * amount
        bal   = get_balance(ctx.author.id)

        if bal < price:
            return await ctx.send(
                f"{ERR} | Không đủ tiền. Cần **{price:,}** {COIN_EMOJI} "
                f"(bạn có **{bal:,}** {COIN_EMOJI})."
            )

        uid  = str(ctx.author.id)

        await update_balance_safe(ctx.author.id, -price)

        # ✅ Tải user SAU khi trừ tiền — tránh save_user overwrite lại cash cũ
        user, _ = get_user(uid)

        # 👉 STACK ITEM CRATE (base_id)
        crate_key = f"crate_{crate_id}"
        add_item(user, crate_key, amount)

        # ✅ Lưu lên MongoDB
        save_user(uid, user)

        await ctx.send(
            f"{OK} | Đã mua **{amount}x** {crate['emoji']} {crate['name']} "
            f"→ trừ **{price:,}** {COIN_EMOJI}."
        )

    # =========================
    # OPEN CRATE
    # =========================
    @staticmethod
    async def _play_open_animation(ctx, crate_data: dict) -> discord.Message:
        """
        Gửi tin nhắn animation mở crate, edit qua 6 frame rồi trả về Message.
        Caller có thể dùng message đó làm header kết quả.
        """
        frames = ["◻◻◻◻◻", "·◻◻◻◻", "··◻◻◻", "···◻◻", "····◻", "·····"]
        msg = await ctx.send(
            f"{crate_data['emoji']} **Đang mở crate...** {frames[0]}"
        )
        for frame in frames[1:]:
            await asyncio.sleep(0.45)
            await msg.edit(content=f"{crate_data['emoji']} **Đang mở crate...** {frame}")
        await asyncio.sleep(0.3)
        return msg

    @crate.command(name="open")
    async def crate_open(self, ctx, crate_id: str, raw_amount: str = "1"):
        if crate_id not in CRATES:
            return await ctx.send(
                f"{ERR} | Crate `{crate_id}` không tồn tại. Xem: `dtn shop crate`"
            )

        crate_key = f"crate_{crate_id}"
        uid       = str(ctx.author.id)

        # ── Pre-flight: inventory & cooldown check (once for the entire batch) ──
        user, _ = get_user(uid)

        owned = user["inv"].get(crate_key, 0)
        if owned <= 0:
            return await ctx.send(
                f"{ERR} | Bạn không có {CRATES[crate_id]['name']}. "
                f"Mua bằng `dtn crate buy {crate_id}`."
            )

        now = time.time()
        if now < user.get("crate_cd", 0):
            remaining = int(user["crate_cd"] - now)
            return await ctx.send(f"⏳ Đang cooldown, còn **{remaining}s**.")

        # ── Resolve final count (silently capped at 6 and at owned) ──
        count = _parse_amount(raw_amount, owned)

        # ── Deduct ALL crates + set cooldown upfront, then save once ──
        user["crate_cd"] = now + CRATE_OPEN_COOLDOWN
        for _ in range(count):
            remove_item(user, crate_key)
        save_user(uid, user)

        author_tag = ctx.author.mention

        # ══════════════════════════════════════════════════
        # SOUL CRATE (id "004") — keep existing result logic
        # ══════════════════════════════════════════════════
        if crate_id == "004":
            # Animation mở crate, sau đó edit thành header
            anim_msg = await self._play_open_animation(ctx, CRATES[crate_id])
            await anim_msg.edit(content=f"{author_tag} | opens a weapon crate")

            for _ in range(count):
                # Reload fresh state each iteration so add_weapon / add_item
                # accumulate correctly across the batch
                user, _ = get_user(uid)
                roll = random.uniform(0, 100)

                # 1. Special weapon (0.6%)
                if roll <= 0.6:
                    special_pool = ["5001", "5002", "5003"]
                    w_id = random.choice(special_pool)
                    add_weapon(user, w_id)
                    save_user(uid, user)
                    add_quest_progress(ctx.author.id, "crates_opened")

                    from rpg_core import get_weapon_entity
                    entity = get_weapon_entity(user, w_id)
                    embed = entity.build_embed()
                    embed.title = "<:Opensoulcrate:1498617029077499935> | fire of soul <:Linh_hoa:1498614127386562601>"
                    embed.color = 0xCCFFCC
                    embed.description = (
                        f"Chúc mừng {ctx.author.mention} đã triệu hồi thành công "
                        f"vũ khí từ **Soul Crate**!"
                    )
                    await ctx.send(embed=embed)
                    # Special weapon → congrat banner
                    await ctx.send(
                        f"{LIGHT_ICON} **Congratulation!** {LIGHT_ICON}\n"
                        f"{ctx.author.mention}"
                    )

                # 2. Linh hoả (35%)
                elif roll <= 35.6:
                    amount_linh = random.randint(4, 18)
                    add_item(user, "5200", amount_linh)
                    save_user(uid, user)
                    add_quest_progress(ctx.author.id, "crates_opened")
                    await ctx.send(
                        f"<:Opensoulcrate:1498617029077499935> | "
                        f"Chúc mừng bạn đã mở ra **x{amount_linh}** "
                        f"<:Linh_hoa:1498614127386562601> **Linh hoả**"
                    )

                # 3. Coin (64.4%)
                else:
                    coins = random.randint(2000, 6000)
                    # update_balance_safe self-saves; no save_user needed here
                    await update_balance_safe(ctx.author.id, coins)
                    add_quest_progress(ctx.author.id, "crates_opened")
                    await ctx.send(
                        f"<:Opensoulcrate:1498617029077499935> | "
                        f"Chúc mừng bạn đã mở ra **x{coins:,}** "
                        f"{COIN_EMOJI} **Coin**"
                    )

                await asyncio.sleep(0.125)

            return

        # ══════════════════════════════════════════════════
        # PARADISE CRATE (id "006")
        # ══════════════════════════════════════════════════
        if crate_id == "006":
            anim_msg = await self._play_open_animation(ctx, CRATES[crate_id])
            await anim_msg.edit(content=f"{author_tag} | opens a **Crate of Paradise**")

            for _ in range(count):
                user, _ = get_user(uid)
                weapon = roll_paradise_crate_weapon()

                # Special: roll ra Book of Godly → mở ngay crate 009
                if weapon["id"] == "006_book":
                    godly_weapon = roll_book_of_godly_weapon()
                    new_uid = add_weapon(user, godly_weapon["id"])
                    passive_emoji = _get_passive_emoji(user, new_uid)
                    save_user(uid, user)
                    add_quest_progress(ctx.author.id, "crates_opened")
                    rarity_label = RARITY_LABEL.get(godly_weapon["rarity"], godly_weapon["rarity"])
                    await ctx.send(
                        f"<a:Book_open:1505164965932306512> | **BOOK OF GODLY**  → {rarity_label} "
                        f"{new_uid} {godly_weapon['emoji']} {passive_emoji} "
                        f"{godly_weapon['chance']:.2f}%"
                    )
                    congrat = _congrat_line(godly_weapon.get("rarity", ""))
                    if congrat:
                        await ctx.send(f"{congrat}\n{ctx.author.mention}")
                else:
                    new_uid = add_weapon(user, weapon["id"])
                    passive_emoji = _get_passive_emoji(user, new_uid)
                    save_user(uid, user)
                    add_quest_progress(ctx.author.id, "crates_opened")
                    rarity_label = RARITY_LABEL.get(weapon["rarity"], weapon["rarity"])
                    await ctx.send(
                        f"<:Paradise_crate_open:1505052527157051454> | and finds a "
                        f"{rarity_label} {new_uid} {weapon['emoji']} {passive_emoji} "
                        f"{weapon['chance']}%"
                    )
                    congrat = _congrat_line(weapon.get("rarity", ""))
                    if congrat:
                        await ctx.send(f"{congrat}\n{ctx.author.mention}")
                await asyncio.sleep(0.125)
            return

        # ══════════════════════════════════════════════════
        # BOOK OF GODLY (id "009") — mở trực tiếp nếu có trong inv
        # ══════════════════════════════════════════════════
        if crate_id == "009":
            anim_msg = await self._play_open_animation(ctx, CRATES[crate_id])
            await anim_msg.edit(content=f"{author_tag} | opens a **Book of Godly**")

            for _ in range(count):
                user, _ = get_user(uid)
                weapon = roll_book_of_godly_weapon()
                new_uid = add_weapon(user, weapon["id"])
                passive_emoji = _get_passive_emoji(user, new_uid)
                save_user(uid, user)
                add_quest_progress(ctx.author.id, "crates_opened")
                rarity_label = RARITY_LABEL.get(weapon["rarity"], weapon["rarity"])
                await ctx.send(
                    f"<:Paradise_crate_open:1505052527157051454> | "
                    f"<a:Book_open:1505164965932306512> | **MYTHICAL** {rarity_label} "
                    f"{new_uid} {weapon['emoji']} {passive_emoji} "
                    f"{weapon['chance']:.2f}%"
                )
                # Book of Godly luôn là mythical → congrat!!!
                await ctx.send(
                    f"{LIGHT_ICON} **Congratulation!!!** {LIGHT_ICON}\n"
                    f"{ctx.author.mention}"
                )
                await asyncio.sleep(0.125)
            return

        # ══════════════════════════════════════════════════
        # ALL OTHER CRATES — plain-text output, no embeds
        # ══════════════════════════════════════════════════

        # Animation mở crate, sau đó edit thành header kết quả
        anim_msg = await self._play_open_animation(ctx, CRATES[crate_id])
        await anim_msg.edit(content=f"{author_tag} | opens a weapon crate")

        for _ in range(count):
            # Reload each iteration — same pattern as original; ensures
            # weapon_instances list is fresh before add_weapon writes to it
            user, _ = get_user(uid)

            if crate_id == "003":
                weapon = roll_dark_crate_weapon()
            elif crate_id == "002":
                weapon = roll_rare_crate_weapon()
            else:
                weapon = roll_weapon()

            new_uid = add_weapon(user, weapon["id"])

            # Resolve passive emoji from the newly-created weapon instance.
            # The instance is already in user["weapon_instances"] at this point
            # because add_weapon mutates user in-place before returning the UID.
            passive_emoji = _get_passive_emoji(user, new_uid)

            save_user(uid, user)
            add_quest_progress(ctx.author.id, "crates_opened")

            rarity_label = RARITY_LABEL.get(weapon["rarity"], weapon["rarity"])
            weapon_emoji = weapon["emoji"]
            drop_rate    = weapon["chance"]
            open_icon    = CRATE_OPEN_ICON.get(crate_id, CHEST_EMOJI)

            await ctx.send(
                f"{open_icon} | and finds a "
                f"{rarity_label} {new_uid} {weapon_emoji} {passive_emoji} {drop_rate}%"
            )
            congrat = _congrat_line(weapon.get("rarity", ""))
            if congrat:
                await ctx.send(f"{congrat}\n{ctx.author.mention}")
            await asyncio.sleep(0.125)

    # ══════════════════════════════════════════════════════════════
    # SLASH COMMANDS — mirror của prefix commands, hybrid style
    # Sync một lần trong on_ready của bot chính.
    # ══════════════════════════════════════════════════════════════

    @app_commands.command(name="crate", description="Xem chi tiết & drop rate của một crate")
    @app_commands.describe(crate_id="ID của crate (vd: 001, 002, 006...)")
    async def slash_crate(self, interaction: discord.Interaction, crate_id: str):
        ctx = await commands.Context.from_interaction(interaction)
        await self.crate(ctx, crate_id)

    @app_commands.command(name="crate_buy", description="Mua crate")
    @app_commands.describe(
        crate_id="ID của crate cần mua",
        amount="Số lượng muốn mua (mặc định: 1)",
    )
    async def slash_crate_buy(
        self,
        interaction: discord.Interaction,
        crate_id: str,
        amount: int = 1,
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.crate_buy(ctx, crate_id, amount)

    @app_commands.command(name="crate_open", description="Mở crate để nhận weapon")
    @app_commands.describe(
        crate_id="ID của crate cần mở",
        amount="Số lượng muốn mở, hoặc 'all' (mặc định: 1)",
    )
    async def slash_crate_open(
        self,
        interaction: discord.Interaction,
        crate_id: str,
        amount: str = "1",
    ):
        ctx = await commands.Context.from_interaction(interaction)
        await self.crate_open(ctx, crate_id, amount)


async def setup(bot):
    await bot.add_cog(RPGCrate(bot))
