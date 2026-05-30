import discord
from discord.ext import commands
import random
from rpg_core import load_data, save_data, get_user, get_user_lock, get_item_by_id
from cash import update_balance  # Liên kết với cash.py


class RPGInventory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def inv(self, ctx):
        uid = str(ctx.author.id)

        async with get_user_lock(uid):
            data = load_data(uid)
            user = get_user(uid, data)

            if not user["inv"]:
                await ctx.send("Kho đồ trống.")
                return

            msg = f"======= {ctx.author.name} inv =======\n"

            for item_id, amount in user["inv"].items():
                if item_id.startswith("crate_"):
                    msg += f"`{item_id}` 📦 x{amount}\n"
                    continue

                item = get_item_by_id(item_id)
                if item:
                    msg += f"`{item_id}` {item['emoji']} x{amount}\n"

        await ctx.send(msg)

    @commands.command()
    async def sell(self, ctx, item_id: str, amount: str):
        uid = str(ctx.author.id)

        async with get_user_lock(uid):
            data = load_data(uid)
            user = get_user(uid, data)

            if item_id not in user["inv"]:
                await ctx.send("Bạn không có vật phẩm này.")
                return

            item = get_item_by_id(item_id)
            if not item:
                await ctx.send("Item không tồn tại.")
                return

            if amount.lower() == "all":
                qty = user["inv"][item_id]
            else:
                try:
                    qty = int(amount)
                except ValueError:
                    await ctx.send("Số lượng không hợp lệ.")
                    return

            if qty <= 0 or qty > user["inv"][item_id]:
                await ctx.send("Không đủ số lượng.")
                return

            # Tính tiền
            total = 0
            for _ in range(qty):
                if item["min"] == 0 and item["max"] == 0:
                    continue
                total += random.randint(item["min"], item["max"])

            # Trừ item
            user["inv"][item_id] -= qty
            if user["inv"][item_id] <= 0:
                del user["inv"][item_id]

            await save_data(data, uid)

        # Trả tiền cho user thông qua cash.py
        update_balance(ctx.author.id, total)

        await ctx.send(f"<:2246:1493575210132312095> | Đã bán {qty} {item['emoji']} {item['name']} và nhận {total} .")


async def setup(bot):
    await bot.add_cog(RPGInventory(bot))
