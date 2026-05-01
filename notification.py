import discord
from discord.ext import commands
import json
import os
import random
import asyncio  # ✅ thêm

# ───────────────────────────────────────────
# LOAD / SAVE DATA
# ───────────────────────────────────────────
def load_data():
    if not os.path.exists("listca.json"):
        return {"list": [], "selected": {}}
    try:
        with open("listca.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"list": [], "selected": {}}

def save_data(data):
    with open("listca.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ───────────────────────────────────────────
# CONFIG & ASSETS
# ───────────────────────────────────────────
GIF_LIST = [
    "https://cdn.discordapp.com/attachments/1493108452153888991/1497433752702292129/destiny1.gif?ex=69ed815b&is=69ec2fdb&hm=8264891806e8bf7ea3466d0a4ad038a3276d4050e6c30d751bc2adff9a16f61e&",
    "https://cdn.discordapp.com/attachments/1493108452153888991/1497433742300414013/destiny2.gif?ex=69ed8159&is=69ec2fd9&hm=4fe8c08a669d1ab3a03975aa68746982b91068e5de496da89718bc8e50d8bd1e&",
    "https://cdn.discordapp.com/attachments/1493108452153888991/1497433731521319073/destiny3.gif?ex=69ed8156&is=69ec2fd6&hm=e36b6e50d430cd60c0d8b77cc5ac02825d01754f2b848f62ada50e5b8b46a51e&",
    "https://cdn.discordapp.com/attachments/1493108452153888991/1497433720846811266/destiny4.gif?ex=69ed8154&is=69ec2fd4&hm=9ae0eb23ee1eee611a1e946d9ba4774e90c5f218dc3a476f041541d474dbf65e&",
    "https://cdn.discordapp.com/attachments/1493108452153888991/1497433710767636611/destiny5.gif?ex=69ed8151&is=69ec2fd1&hm=d2e7649bf0989a839d4672438f247c14a19aeec350d99e940e590cb41a20fa42&"
]

# ───────────────────────────────────────────
# COG NOTIFICATION
# ───────────────────────────────────────────
class Notification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─────────────────────────────
    # dtn helpn
    # ─────────────────────────────
    @commands.command(name="helpn")
    @commands.is_owner()
    async def help_notification(self, ctx):
        embed = discord.Embed(
            title="📖 Hướng dẫn Hệ thống Thông báo",
            description="Dưới đây là các lệnh dành riêng cho quản trị viên:",
            color=0x3498db
        )
        embed.add_field(name="`dtn set <id> <link>`", value="Thêm Channel ID và Link server vào danh sách.", inline=False)
        embed.add_field(name="`dtn listca`", value="Xem danh sách các kênh và tên ghi chú.", inline=False)
        embed.add_field(name="`dtn gca <index>`", value="Chọn kênh mục tiêu theo số thứ tự.", inline=False)
        embed.add_field(name="`dtn gcan <index> <tên>`", value="Đặt tên ghi chú cho server trong danh sách.", inline=False)
        embed.add_field(name="`dtn gcans <index>`", value="Xóa một server khỏi danh sách.", inline=False)
        embed.add_field(name="`dtn ca <title> <nội dung>`", value="Gửi thông báo dạng Embed (dùng `<chat>` để xuống dòng).", inline=False)
        embed.add_field(name="`dtn chat <nội dung>`", value="Gửi tin nhắn văn bản bình thường đến kênh đã chọn.", inline=False)

    # ─────────────────────────────
    # dtn set <channel_id> <server_link>
    # ─────────────────────────────
    @commands.command(name="set")
    @commands.is_owner()
    async def set_channel(self, ctx, channel_id: int, server_link: str):
        data = load_data()
        data["list"].append({
            "channel_id": channel_id,
            "server_link": server_link,
            "note": "Chưa đặt tên"
        })
        save_data(data)
        await ctx.send(f"✅ Đã thêm kênh `{channel_id}` vào danh sách thành công!")

    # ─────────────────────────────
    # dtn listca
    # ─────────────────────────────
    @commands.command(name="listca")
    @commands.is_owner()
    async def list_ca(self, ctx):
        data = load_data()
        lst = data.get("list", [])
        if not lst:
            return await ctx.send("❌ Dữ liệu listca hiện đang trống.")

        embed = discord.Embed(title="📌 Danh sách Channel đã lưu", color=0x9b59b6)
        text = ""
        for i, item in enumerate(lst, 1):
            note = item.get("note", "Chưa đặt tên")
            text += f"**{i}.** 🏷️ **{note}**\n╰ Channel: `{item['channel_id']}` | [Link Server]({item['server_link']})\n\n"
        
        embed.description = text
        await ctx.send(embed=embed)

    # ─────────────────────────────
    # dtn gca <index>
    # ─────────────────────────────
    @commands.command(name="gca")
    @commands.is_owner()
    async def choose_ca(self, ctx, index: int):
        data = load_data()
        lst = data.get("list", [])
        if index < 1 or index > len(lst):
            return await ctx.send("❌ Số thứ tự không hợp lệ!")

        data["selected"][str(ctx.author.id)] = index - 1
        save_data(data)
        await ctx.send(f"🎯 Đã chọn mục tiêu số `{index}`: `{lst[index-1]['channel_id']}`")

    # ─────────────────────────────
    # dtn gcan <index> <tên>
    # ─────────────────────────────
    @commands.command(name="gcan")
    @commands.is_owner()
    async def set_note(self, ctx, index: int, *, name: str):
        data = load_data()
        lst = data.get("list", [])
        if index < 1 or index > len(lst):
            return await ctx.send("❌ Số thứ tự không hợp lệ!")
        
        lst[index-1]["note"] = name
        save_data(data)
        await ctx.send(f"✅ Đã đặt tên cho mục số `{index}` là: **{name}**")

    # ─────────────────────────────
    # dtn gcans <index>
    # ─────────────────────────────
    @commands.command(name="gcans")
    @commands.is_owner()
    async def delete_entry(self, ctx, index: int):
        data = load_data()
        lst = data.get("list", [])
        if index < 1 or index > len(lst):
            return await ctx.send("❌ Số thứ tự không hợp lệ!")
        
        removed = lst.pop(index-1)
        selected_key = str(ctx.author.id)
        if selected_key in data["selected"] and data["selected"][selected_key] >= len(lst):
            data["selected"][selected_key] = 0
            
        save_data(data)
        await ctx.send(f"🗑️ Đã xóa server **{removed.get('note', removed['channel_id'])}** khỏi danh sách.")

    # ─────────────────────────────
    # dtn ca
    # ─────────────────────────────
    @commands.command(name="ca")
    @commands.is_owner()
    async def send_announce(self, ctx, title: str, *content):
        data = load_data()
        lst = data.get("list", [])
        selected = data.get("selected", {}).get(str(ctx.author.id), 0)

        if not lst:
            return await ctx.send("❌ Chưa có kênh nào trong danh sách. Hãy dùng `dtn set` trước.")

        if selected >= len(lst):
            selected = 0

        channel_id = lst[selected]["channel_id"]
        channel = self.bot.get_channel(channel_id)

        if not channel:
            return await ctx.send("❌ Không tìm thấy kênh hoặc Bot không có quyền truy cập.")

        raw_text = " ".join(content)
        lines = raw_text.split("<chat>")
        main_text = "\n".join([f"• {line.strip()}" for line in lines if line.strip()])
        
        random_gif = random.choice(GIF_LIST)

        embed = discord.Embed(
            title=f"<:Info:1496098636247863491> | {title.upper()} ",
            description=f"\n{main_text}\n\u200b", 
            color=0x2ecc71
        )
        
        embed.set_author(name="THÔNG BÁO HỆ THỐNG", icon_url=self.bot.user.display_avatar.url)
        embed.set_image(url=random_gif)
        embed.set_footer(text="Hình ảnh được hiển thị ngẫu nhiên từ bộ sưu tập Destiny.")
        embed.timestamp = discord.utils.utcnow()

        await channel.send(embed=embed)
        await ctx.send(f"✔️ Đã gửi thông báo đến kênh `{channel_id}`")

    # ─────────────────────────────
    # dtn cal (NEW)
    # ─────────────────────────────
    @commands.command(name="cal")
    @commands.is_owner()
    async def send_announce_all(self, ctx, title: str, *content):
        data = load_data()
        lst = data.get("list", [])

        if not lst:
            return await ctx.send("❌ Chưa có kênh nào trong danh sách.")

        raw_text = " ".join(content)
        lines = raw_text.split("<chat>")
        main_text = "\n".join([f"• {line.strip()}" for line in lines if line.strip()])

        success = 0
        fail = 0

        random_gif = random.choice(GIF_LIST)

        for item in lst:
            channel_id = item.get("channel_id")
            channel = self.bot.get_channel(channel_id)

            if not channel:
                fail += 1
                continue

            try:
                embed = discord.Embed(
                    title=f"<:Info:1496098636247863491> | {title.upper()} ",
                    description=f"\n{main_text}\n\u200b",
                    color=0x2ecc71
                )

                embed.set_author(
                    name="THÔNG BÁO HỆ THỐNG",
                    icon_url=self.bot.user.display_avatar.url
                )
                embed.set_image(url=random_gif)
                embed.set_footer(text="Hình ảnh được hiển thị ngẫu nhiên từ bộ sưu tập Destiny.")
                embed.timestamp = discord.utils.utcnow()

                await channel.send(embed=embed)
                success += 1

                await asyncio.sleep(1)

            except:
                fail += 1

        await ctx.send(f"📢 Gửi toàn bộ hoàn tất!\n✅ Thành công: {success}\n❌ Thất bại: {fail}")

    # ─────────────────────────────
    # dtn chat
    # ─────────────────────────────
    @commands.command(name="chat")
    @commands.is_owner()
    async def send_chat(self, ctx, *, text):
        data = load_data()
        lst = data.get("list", [])
        selected = data.get("selected", {}).get(str(ctx.author.id), 0)

        if not lst:
            return await ctx.send("❌ Danh sách kênh trống.")

        if selected >= len(lst):
            selected = 0

        channel_id = lst[selected]["channel_id"]
        channel = self.bot.get_channel(channel_id)

        if not channel:
            return await ctx.send("❌ Lỗi: Không thể kết nối với kênh.")

        await channel.send(text)
        await ctx.send("✔️ Tin nhắn đã được chuyển đi.")

# ───────────────────────────────────────────
# SETUP
# ───────────────────────────────────────────
async def setup(bot):
    await bot.add_cog(Notification(bot))