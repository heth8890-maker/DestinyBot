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
    add_item, remove_item,
    add_weapon, roll_weapon,
    CRATES, RARITY_COLOR, RARITY_LABEL,
)
# ✅ Dùng get_user / save_user từ rpg_database (MongoDB) thay vì load_data / save_data JSON
from rpg_database import get_user, save_user

from rpg_weapon import roll_rare_crate_weapon, roll_dark_crate_weapon
from rpg_quest import add_quest_progress
from cash import update_balance_safe, get_balance


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
    @crate.command(name="open")
    async def crate_open(self, ctx, crate_id: str):
        if crate_id not in CRATES:
            return await ctx.send(
                f"{ERR} | Crate `{crate_id}` không tồn tại. Xem: `dtn shop crate`"
            )

        crate_key = f"crate_{crate_id}"
        uid  = str(ctx.author.id)
        # ✅ Tải user từ MongoDB
        user, _ = get_user(uid)

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
        # ✅ Lưu cooldown + xóa crate lên MongoDB
        save_user(uid, user)

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
                # ✅ Lưu lên MongoDB
                save_user(uid, user)

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
                # ✅ Lưu lên MongoDB
                save_user(uid, user)
                return await msg.edit(content=f"<:Opensoulcrate:1498617029077499935> | Chúc mừng bạn đã mở ra **x{amount}** <:Linh_hoa:1498614127386562601> **Linh hoả**")

            # 3. TRÚNG COIN (64.4%)
            else:
                coins = random.randint(2000, 6000)
                from cash import update_balance_safe
                await update_balance_safe(ctx.author.id, coins)
                # ✅ update_balance tự lưu; user dict không thay đổi → không cần save_user
                return await msg.edit(content=f"<:Opensoulcrate:1498617029077499935> | Chúc mừng bạn đã mở ra **x{coins:,}** <:Coin:1495831576397742241> **Coin**")

        # ── [LOGIC CHO CÁC CRATE CÒN LẠI] ──
        if crate_id == "003":
            weapon = roll_dark_crate_weapon()
        elif crate_id == "002":
            weapon = roll_rare_crate_weapon()
        else:
            weapon = roll_weapon()

        # ✅ Tải lại user từ MongoDB (giống re-load JSON cũ, đảm bảo dữ liệu mới nhất)
        user, _ = get_user(uid)

        # 🔥 IMPORTANT: STACK BASE_ID ONLY — NEVER produce a UID from a crate
        add_weapon(user, weapon["id"], make_unique=False)

        # ✅ Lưu lên MongoDB
        save_user(uid, user)

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



async def setup(bot):
    await bot.add_cog(RPGCrate(bot))
