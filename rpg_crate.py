"""
===== FILE: rpg_crate.py =====
Discord Cog cho hệ thống Crate.
Tách từ rpg_game.py để giảm độ phức tạp.

HỆ MỚI:
- Crate chỉ spawn BASE_ID (stackable)
- KHÔNG tạo UID tại đây
- UID chỉ xuất hiện ở hệ nâng cấp / enchant / upgrade

Commands:
  dtn crate buy <id> [amount]
  dtn crate open <id>
"""

import asyncio
import time
import random

import discord
from discord.ext import commands

from rpg_core import (
    load_data, save_data, get_user,
    add_item, remove_item,
    add_weapon, roll_weapon,
    CRATES, RARITY_COLOR, RARITY_LABEL,
)
from rpg_weapon import roll_rare_crate_weapon, roll_dark_crate_weapon
from rpg_quest import add_quest_progress
from cash import update_balance, get_balance


COIN_EMOJI  = "<:Coin:1495831576397742241>"
CHEST_EMOJI = "<:2925:1495277191867400284>"
ERR  = "<:X_:1495466670616219819>"
OK   = "<:Tick:1495466684520206528>"


def _rarity_tier(rarity: str) -> str:
    return RARITY_LABEL.get(rarity, rarity)


class RPGCrate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="crate", invoke_without_command=True)
    async def crate(self, ctx):
        await ctx.send(
            f"{CHEST_EMOJI} **Lệnh crate:**\n"
            "• `dtn crate buy <id> [amount]` — mua crate\n"
            "• `dtn crate open <id>` — mở crate nhận weapon\n"
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

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        update_balance(ctx.author.id, -price)

        # 👉 STACK ITEM CRATE (base_id)
        crate_key = f"crate_{crate_id}"
        add_item(user, crate_key, amount)

        await save_data(data)

        await ctx.send(
            f"{OK} | Đã mua **{amount}x** {crate['emoji']} {crate['name']} "
            f"→ trừ **{price:,}** {COIN_EMOJI}."
        )

    # =========================
    # OPEN CRATE
    # =========================
    @crate.command(name="open")
    async def crate_open(self, ctx, crate_id: str):
        if crate_id not in CRATES:
            return await ctx.send(
                f"{ERR} | Crate `{crate_id}` không tồn tại. Xem: `dtn shop crate`"
            )

        crate_key = f"crate_{crate_id}"
        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        if user["inv"].get(crate_key, 0) <= 0:
            return await ctx.send(
                f"{ERR} | Bạn không có {CRATES[crate_id]['name']}. "
                f"Mua bằng `dtn crate buy {crate_id}`."
            )

        now = time.time()
        if now < user.get("crate_cd", 0):
            remaining = int(user["crate_cd"] - now)
            return await ctx.send(f"⏳ Đang cooldown, còn **{remaining}s**.")

        user["crate_cd"] = now + 2

        # remove 1 crate (stack system)
        remove_item(user, crate_key)
        await save_data(data)

        crate = CRATES[crate_id]

        frames = ["◻◻◻◻◻", "·◻◻◻◻", "··◻◻◻", "···◻◻", "····◻", "·····"]
        msg = await ctx.send(f"{crate['emoji']} **Đang mở crate...** {frames[0]}")

        for frame in frames[1:]:
            await asyncio.sleep(0.45)
            await msg.edit(content=f"{crate['emoji']} **Đang mở crate...** {frame}")

        await asyncio.sleep(0.3)

        # ── [LOGIC MỞ RIÊNG CHO SOUL CRATE] ──
        if crate_id == "004":
            roll = random.uniform(0, 100)

            # 1. TRÚNG SPECIAL WEAPON (0.6%)
            if roll <= 0.6:
                special_pool = ["5001", "5002", "5003"]
                w_id = random.choice(special_pool)
                add_weapon(user, w_id, make_unique=False)
                await save_data(data)

                from rpg_core import get_weapon_entity
                entity = get_weapon_entity(user, w_id)
                embed = entity.build_embed()
                embed.title = f"<:Opensoulcrate:1498617029077499935> | fire of soul <:Linh_hoa:1498614127386562601>"
                embed.color = 0xCCFFCC  # Xanh lá nhạt
                embed.description = f"Chúc mừng {ctx.author.mention} đã triệu hồi thành công vũ khí từ **Soul Crate**!"
                return await msg.edit(content=None, embed=embed)

            # 2. TRÚNG LINH HOẢ (35%)
            elif roll <= 35.6:
                amount = random.randint(4, 18)
                add_item(user, "5200", amount)
                await save_data(data)
                return await msg.edit(content=f"<:Opensoulcrate:1498617029077499935> | Chúc mừng bạn đã mở ra **x{amount}** <:Linh_hoa:1498614127386562601> **Linh hoả**")

            # 3. TRÚNG COIN (64.4%)
            else:
                coins = random.randint(2000, 6000)
                from cash import update_balance
                update_balance(ctx.author.id, coins)
                await save_data(data)
                return await msg.edit(content=f"<:Opensoulcrate:1498617029077499935> | Chúc mừng bạn đã mở ra **x{coins:,}** <:Coin:1495831576397742241> **Coin**")

        # ── [LOGIC CHO CÁC CRATE CÒN LẠI] ──
        if crate_id == "003":
            weapon = roll_dark_crate_weapon()
        elif crate_id == "002":
            weapon = roll_rare_crate_weapon()
        else:
            weapon = roll_weapon()

        data = load_data()
        user = get_user(uid, data)

        # 🔥 IMPORTANT: STACK BASE_ID ONLY — NEVER produce a UID from a crate
        add_weapon(user, weapon["id"], make_unique=False)

        await save_data(data)

        add_quest_progress(ctx.author.id, "crates_opened")

        rarity_color = RARITY_COLOR.get(weapon["rarity"], 0x5865F2)

        embed = discord.Embed(
            title=f"{CHEST_EMOJI} Crate đã mở!",
            description=(
                f"**{ctx.author.mention}** nhận được:\n\n"
                f"{weapon['emoji']}  **{weapon['name']}**\n"
                f"{_rarity_tier(weapon['rarity'])}  |  Tỉ lệ: **{weapon['chance']}%**"
            ),
            color=rarity_color,
        )

        effects = weapon.get("effects", {})
        if effects:
            fx_lines = "\n".join(f"• `{k}`: `+{v}`" for k, v in effects.items())
            embed.add_field(
                name="<:Effect:1495466103047061679> | Hiệu ứng",
                value=fx_lines,
                inline=False,
            )
        else:
            embed.add_field(
                name="<:Effect:1495466103047061679> | Hiệu ứng",
                value="Không có hiệu ứng.",
                inline=False,
            )

        embed.add_field(
            name="📖 Mô tả",
            value=weapon.get("description", "—"),
            inline=False,
        )

        embed.set_footer(
            text=f"dtn weapon {weapon['id']} status │ dtn weapon equip {weapon['id']}"
        )

        await msg.edit(content=None, embed=embed)

    # ─── LỆNH XEM SHOP EVENT ───
    @commands.command(name="eshop")
    async def event_shop(self, ctx):
        embed = discord.Embed(
            title="<:Shop:1495464183037165763> Shop event",
            description=(
                "Dùng Linh hoả để đổi Soul Crate hiếm!\n"
                "Ma Hỏa Thống Soái 0.3% | Linh Diệm Sát Thần 0.3% | "
                "Hồn Giáp Bất Diệt 0.3% | Linh Hoả 35% | 64.4% 2000 6000 Coin"
            ),
            color=0xCCFFCC
        )

        embed.add_field(
            name="<:Soulcrate:1498617031501807646> | Soul Crate (ID: 004)",
            value=(
                "**Giá:** 25x <:Linh_hoa:1498614127386562601> Linh hoả\n"
                "**Lệnh mua:** `dtn ebuy 004 [số lượng]`"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    # ─── LỆNH MUA (ebuy / eventbuy) ───
    @commands.command(name="eventbuy", aliases=["ebuy"])
    async def event_buy(self, ctx, item_id: str = None, amount: int = 1):
        if not item_id or item_id != "004":
            return await ctx.send(f"{ERR} | ID vật phẩm không đúng. Sử dụng: `dtn ebuy 004 [số lượng]`")

        if amount <= 0:
            return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")

        data = load_data()
        uid = str(ctx.author.id)
        user = get_user(uid, data)

        # Cấu hình ID
        currency_id = "5200"  # ID Linh hoả
        crate_id = "004"     # ID Soul Crate
        cost_per_unit = 25
        total_cost = cost_per_unit * amount

        # Kiểm tra Linh hoả (5200) trong kho
        user_inv = user.get("inv", {})
        if user_inv.get(currency_id, 0) < total_cost:
            missing = total_cost - user_inv.get(currency_id, 0)
            return await ctx.send(f"{ERR} | Bạn thiếu **{missing}** <:Linh_hoa:1498614127386562601> Linh hoả (ID: 5200) để thực hiện giao dịch này.")

        # Trừ Linh hoả và thêm Soul Crate
        from rpg_core import add_item
        user["inv"][currency_id] -= total_cost
        add_item(user, f"crate_{crate_id}", amount)

        await save_data(data)
        await ctx.send(f"{OK} | Chúc mừng bạn đã đổi thành công **{total_cost}** Linh hoả lấy **{amount}x** <:Soulcrate:1498617031501807646> **Soul Crate**!")


async def setup(bot):
    await bot.add_cog(RPGCrate(bot))
