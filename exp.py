"""
===== FILE: exp.py =====
Hệ thống Level / XP / Profile / Background.
Độc lập hoàn toàn với RPG modules.

⚡ Thay đổi so với bản cũ (fix command conflict):
  - dtn use <id>               → dtn bg equip <id>
  - dtn buy <id>               → dtn bg buy <id>
  - dtn check <id>             → dtn bg check <id>
  - dtn shop (group)           → dtn bg shop
  - dtn shop background        → dtn bg shop

  dtn profile / dtn pf         → giữ nguyên (không conflict)
"""

import discord
from discord.ext import commands
import json
import os
import io
import time
import aiohttp
from PIL import Image, ImageDraw, ImageFont

# ═══════════════════════════════════════════════════════════
# PHẦN 1: CẤU HÌNH BACKGROUND SHOP
# ═══════════════════════════════════════════════════════════

BACKGROUNDS = {

    "9282": {"name": "Bãi biển",             "file": "IMG_20260416_047927.png",  "price": 6000},
    "3938": {"name": "Thảm cỏ ánh nắng",    "file": "IMG_20260416_017935.jpeg", "price": 6000},
    "7386": {"name": "Bãi biển xanh",        "file": "IMG_20260416_046937.png",  "price": 8000},
    "6392": {"name": "Cánh rừng hoàng hôn", "file": "IMG_20260416_639297.png",  "price": 6500},
    "2937": {"name": "Hoàng hôn bãi biển",  "file": "IMG_20260416_973963.png",  "price": 7000},
    "7480": {"name": "Núi rừng bầu trời",   "file": "IMG_20260416_182034.png",  "price": 6000},
    "7484": {"name": "Vách đá kỷ nguyên",   "file": "IMG_20260416_204146.jpeg", "price": 10000},
    "9733": {"name": "Thảo nguyên lavender", "file": "IMG_lavender1.png",        "price": 8000},
    "3938b":{"name": "Cánh đồng lavender",  "file": "IMG_lavender2.png",        "price": 6000},
    "4478": {"name": "Cổ thụ nghìn năm",    "file": "IMG_cothunghinnam.png",    "price": 8000},
    "7580": {"name": "Đại hồng thủy",       "file": "IMG_daihongthuy.png",      "price": 8000},
    "1393": {"name": "Pháo đài rừng sâu",   "file": "IMG_phaodairungsau.png",   "price": 8000},
    "9339": {"name": "Sắc cảnh hắc miêu",   "file": "IMG_saccanhhacmieu.png",   "price": 6500},
    "1836": {"name": "Ace hoa quyen",       "file": "IMG_ace.png",              "price": 12000},
    "4839": {"name": "Wang lin ",       "file": "IMG_vulam.png",              "price": 12000},
}

XP_COOLDOWN      = 5    # giây cooldown cộng XP từ on_message
COMMAND_COOLDOWN = 10   # giây cooldown lệnh profile

# ═══════════════════════════════════════════════════════════
# PHẦN 2: HÀM XỬ LÝ DỮ LIỆU JSON
# ═══════════════════════════════════════════════════════════

def _load_json(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_eco():  return _load_json("economy.json")
def save_eco(d): _save_json("economy.json", d)
def load_exp():  return _load_json("level.json")
def save_exp(d): _save_json("level.json", d)
def load_inv():  return _load_json("inventory.json")
def save_inv(d): _save_json("inventory.json", d)


def get_balance(user_id):
    return load_eco().get(str(user_id), 0)


def update_balance(user_id, amount):
    data = load_eco()
    uid  = str(user_id)
    data[uid] = data.get(uid, 0) + amount
    save_eco(data)


def get_inventory(user_id):
    data = load_inv()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = {"owned_backgrounds": [], "equipped_background": None}
        save_inv(data)
    else:
        changed = False
        # Backward-compat: đổi tên key cũ
        if "owned" in data[uid] and "owned_backgrounds" not in data[uid]:
            data[uid]["owned_backgrounds"] = data[uid].pop("owned")
            changed = True
        if "current" in data[uid] and "equipped_background" not in data[uid]:
            data[uid]["equipped_background"] = data[uid].pop("current")
            changed = True
        if "owned_backgrounds" not in data[uid]:
            data[uid]["owned_backgrounds"] = []
            changed = True
        if "equipped_background" not in data[uid]:
            data[uid]["equipped_background"] = None
            changed = True
        if changed:
            save_inv(data)
    return data[uid]


def add_owned_bg(user_id, bg_id):
    data = load_inv()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = {"owned_backgrounds": [], "equipped_background": None}
    if bg_id not in data[uid]["owned_backgrounds"]:
        data[uid]["owned_backgrounds"].append(bg_id)
    save_inv(data)


def set_equipped_bg(user_id, bg_id):
    data = load_inv()
    uid  = str(user_id)
    if uid not in data:
        data[uid] = {"owned_backgrounds": [], "equipped_background": None}
    if bg_id in data[uid]["owned_backgrounds"]:
        data[uid]["equipped_background"] = bg_id
        save_inv(data)
        return True
    return False


# ═══════════════════════════════════════════════════════════
# CÔNG THỨC EXP
# Level 1→100, Level 2→200, Level 3→400, ...
# ═══════════════════════════════════════════════════════════

def get_xp_needed(level):
    return (100 + level * 50) * 240


# ═══════════════════════════════════════════════════════════
# PHẦN 3: VẼ ẢNH PROFILE (PILLOW)
# Canvas: 1400 x 700
# ═══════════════════════════════════════════════════════════

def _load_font(size):
    font_path = "NotoSans[wdth,wght].ttf"
    try:
        full_path = os.path.join(os.getcwd(), font_path)
        return ImageFont.truetype(full_path, size)
    except Exception as e:
        print(f"⚠️ Font error: {e}")
        return ImageFont.load_default()


def draw_text_with_outline(draw, pos, text, font, fill_color, outline_color="#000000", thickness=3):
    x, y = pos
    for dx in range(-thickness, thickness + 1):
        for dy in range(-thickness, thickness + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=fill_color)


# ═══════════════════════════════════════════════════════════
# PHẦN 4: COG CHÍNH
# ═══════════════════════════════════════════════════════════

class Experience(commands.Cog):
    """Hệ thống Cấp độ, Tiền tệ và Hình ảnh Hồ sơ"""

    def __init__(self, bot):
        self.bot = bot
        self._xp_cooldown:  dict = {}   # uid → timestamp (cộng XP)
        self._cmd_cooldown: dict = {}   # (uid, cmd_name) → timestamp

    # ── Cooldown helper ─────────────────────────────────────
    def _check_cmd_cooldown(self, user_id: int, cmd_name: str, seconds: float = COMMAND_COOLDOWN):
        """
        Trả về (allowed: bool, remaining: float).
        allowed=True  → cho phép chạy (lần đầu hoặc đã hết cooldown).
        allowed=False → đang cooldown.
        """
        key  = (str(user_id), cmd_name)
        now  = time.time()
        last = self._cmd_cooldown.get(key)

        if last is None or (now - last) >= seconds:
            self._cmd_cooldown[key] = now
            return True, 0.0
        else:
            remaining = round(seconds - (now - last), 1)
            return False, remaining

    # ── on_message: cộng XP ─────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        uid = str(message.author.id)
        now = time.time()

        if now - self._xp_cooldown.get(uid, 0) >= XP_COOLDOWN:
            self._xp_cooldown[uid] = now

            data = load_exp()
            if uid not in data:
                data[uid] = {"xp": 0, "level": 1}

            xp_to_add = 120 if message.content.lower().startswith("dtn ") else 50
            data[uid]["xp"] += xp_to_add

            leveled_up    = False
            current_level = data[uid]["level"]

            while True:
                req_xp = get_xp_needed(current_level)
                if data[uid]["xp"] >= req_xp:
                    data[uid]["xp"]    -= req_xp
                    data[uid]["level"] += 1
                    current_level       = data[uid]["level"]
                    leveled_up          = True
                    reward_money        = 4000 * (2 ** (current_level - 2))
                    update_balance(message.author.id, reward_money)
                else:
                    break

            save_exp(data)

            if leveled_up:
                reward = 24000 * (2 ** (current_level - 2))
                await message.channel.send(
                    f"🎉 | Chúc mừng **{message.author.name}** đã thăng cấp lên "
                    f"**Level {current_level}**! "
                    f"Bạn nhận được **{reward:,}** <:Coin:1495831576397742241>!"
                )

    # ── Vẽ ảnh profile ──────────────────────────────────────
    async def create_profile_image(self, user, level, xp, req_xp, bal, current_bg_id):
        """Dựng ảnh Profile bằng Pillow — canvas 1400×700."""

        # ── Constants ──
        IMG_W, IMG_H = 1400, 700
        AV_SIZE      = 320
        AV_MARGIN    = 60
        AV_X         = AV_MARGIN
        AV_Y         = AV_MARGIN
        BORDER_W     = 3

        # ── Fonts ──
        FS_USERNAME = int(AV_SIZE * 0.35)
        FS_LEVEL    = int(AV_SIZE * 0.30)
        FS_MEDIUM   = int(AV_SIZE * 0.20)
        FS_SMALL    = int(AV_SIZE * 0.15)
        FS_EXP      = int(AV_SIZE * 0.12)

        font_username = _load_font(FS_USERNAME)
        font_level    = _load_font(FS_LEVEL)
        font_medium   = _load_font(FS_MEDIUM)
        font_small    = _load_font(FS_SMALL)
        font_exp      = _load_font(FS_EXP)

        # ── Layout ──
        TEXT_X     = AV_X + AV_SIZE + 50
        USERNAME_Y = AV_Y + 30
        ID_Y       = USERNAME_Y + FS_USERNAME + 10
        TAG_Y      = ID_Y + FS_SMALL + 10
        MONEY_Y    = TAG_Y + FS_SMALL + 20
        LEVEL_Y    = AV_Y + AV_SIZE + 35

        # ── EXP bar ──
        BAR_HEIGHT         = 55
        BAR_WIDTH          = int(IMG_W * 0.85)
        BAR_X              = (IMG_W - BAR_WIDTH) // 2
        EXP_BOTTOM_MARGIN  = 60
        BAR_Y              = IMG_H - EXP_BOTTOM_MARGIN - BAR_HEIGHT

        # ── Background ──
        img = None
        if current_bg_id and current_bg_id in BACKGROUNDS:
            bg_filename = BACKGROUNDS[current_bg_id]["file"]
            if os.path.exists(bg_filename):
                try:
                    img = Image.open(bg_filename).convert("RGBA").resize((IMG_W, IMG_H))
                except Exception as e:
                    print(f"[EXP] Lỗi mở background '{bg_filename}': {e}")

        if img is None:
            img = Image.new("RGBA", (IMG_W, IMG_H), color="#101010")

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 60))
        img     = Image.alpha_composite(img, overlay)
        draw    = ImageDraw.Draw(img)

        # ── Avatar + viền vàng ──
        try:
            avatar_url = user.display_avatar.with_format("png").with_size(512).url
            timeout    = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        avatar_bytes = await resp.read()
                        avatar = (
                            Image.open(io.BytesIO(avatar_bytes))
                            .convert("RGBA")
                            .resize((AV_SIZE, AV_SIZE))
                        )
                        img.paste(avatar, (AV_X, AV_Y), avatar)
                    else:
                        draw.rectangle(
                            [AV_X, AV_Y, AV_X + AV_SIZE, AV_Y + AV_SIZE],
                            fill="#333333",
                        )
        except Exception as e:
            print(f"[EXP] Lỗi tải avatar: {e}")
            draw.rectangle(
                [AV_X, AV_Y, AV_X + AV_SIZE, AV_Y + AV_SIZE],
                fill="#333333",
            )

        draw.rectangle(
            [AV_X - BORDER_W, AV_Y - BORDER_W,
             AV_X + AV_SIZE + BORDER_W, AV_Y + AV_SIZE + BORDER_W],
            outline=(255, 255, 255, 120),  #trắng mờ
            width=BORDER_W,
        )

        # ── Khối văn bản bên phải ──
        draw_text_with_outline(draw, (TEXT_X, USERNAME_Y), str(user.name),
                               font=font_username, fill_color="#FFFFFF", thickness=5)
        draw_text_with_outline(draw, (TEXT_X, ID_Y), f"ID: {user.id}",
                               font=font_small, fill_color="#CCCCCC", thickness=2)
        draw_text_with_outline(draw, (TEXT_X, TAG_Y), "destinyGM user",
                               font=font_small, fill_color="#CCCCCC", thickness=2)

        # Icon tiền
        icon_size     = int(FS_MEDIUM * 1.1)
        icon_offset_y = (FS_MEDIUM - icon_size) // 2
        bal_str       = f"{bal:,}"
        try:
            wallet_icon = Image.open("2309.png").convert("RGBA").resize((icon_size, icon_size))
            money_icon  = Image.open("2246.png").convert("RGBA").resize((icon_size, icon_size))

            img.paste(wallet_icon, (TEXT_X, MONEY_Y + icon_offset_y), wallet_icon)
            bal_x = TEXT_X + icon_size + 12
            draw_text_with_outline(draw, (bal_x, MONEY_Y), bal_str,
                                   font=font_medium, fill_color="#A3E4D7", thickness=3)
            tw = int(draw.textlength(bal_str, font=font_medium))
            img.paste(money_icon, (bal_x + tw + 12, MONEY_Y + icon_offset_y), money_icon)
        except Exception:
            draw_text_with_outline(draw, (TEXT_X, MONEY_Y), f"Balance: {bal_str}",
                                   font=font_medium, fill_color="#A3E4D7", thickness=3)

        # ── Level ──
        draw_text_with_outline(draw, (AV_X, LEVEL_Y), f"Lv.{level}",
                               font=font_level, fill_color="#FFFFFF",
                               outline_color="#000000", thickness=8)

        # ── EXP bar ──
        draw.rectangle(
            [BAR_X, BAR_Y, BAR_X + BAR_WIDTH, BAR_Y + BAR_HEIGHT],
            outline="#2C3E50", width=5, fill="#17202A",
        )
        fill_width = min(int((xp / req_xp) * BAR_WIDTH), BAR_WIDTH) if req_xp > 0 else 0
        inner_pad  = 6
        if fill_width > inner_pad * 2:
            draw.rectangle(
                [BAR_X + inner_pad,          BAR_Y + inner_pad,
                 BAR_X + fill_width - inner_pad, BAR_Y + BAR_HEIGHT - inner_pad],
                fill="#2ECC71",
            )

        exp_text   = f"{xp:,} / {req_xp:,} EXP"
        exp_text_w = int(draw.textlength(exp_text, font=font_exp))
        exp_text_x = BAR_X + (BAR_WIDTH  - exp_text_w) // 2
        exp_text_y = BAR_Y + (BAR_HEIGHT - FS_EXP)     // 2
        draw_text_with_outline(draw, (exp_text_x, exp_text_y), exp_text,
                               font=font_exp, fill_color="#FFFDE7", thickness=3)

        # ── Xuất PNG ──
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    # ═══════════════════════════════════════════════════════
    # PHẦN 5: LỆNH
    # ═══════════════════════════════════════════════════════

    # ── dtn profile ──────────────────────────────────────────
    @commands.command(name="profile", aliases=["pf"])
    async def profile(self, ctx):
        """Hiển thị hồ sơ cá nhân."""
        allowed, remaining = self._check_cmd_cooldown(ctx.author.id, "profile")
        if not allowed:
            await ctx.send(
                f"⏳ {ctx.author.mention} vui lòng chờ **{remaining}s** "
                "trước khi dùng lại lệnh này."
            )
            return

        uid  = str(ctx.author.id)
        data = load_exp()
        if uid not in data:
            data[uid] = {"xp": 0, "level": 1}
            save_exp(data)

        inv      = get_inventory(ctx.author.id)
        owned    = inv.get("owned_backgrounds", [])
        equipped = inv.get("equipped_background", None)

        if not owned or not equipped or equipped not in owned:
            await ctx.send(
                "🖼️ Bạn chưa có background của mình, "
                "hãy xem `dtn bg shop` để lấy background nhé!"
            )
            return

        level  = data[uid]["level"]
        xp     = data[uid]["xp"]
        req_xp = get_xp_needed(level)
        bal    = get_balance(ctx.author.id)

        image_bytes = await self.create_profile_image(
            ctx.author, level, xp, req_xp, bal, equipped
        )
        await ctx.send(file=discord.File(fp=image_bytes, filename="profile.png"))

    # ── dtn bg (group) ───────────────────────────────────────
    @commands.group(name="bg", invoke_without_command=True)
    async def bg(self, ctx):
        """Hướng dẫn lệnh background."""
        await ctx.send(
            "🖼️ **Lệnh background:**\n"
            "• `dtn bg shop` — xem cửa hàng background\n"
            "• `dtn bg buy <id>` — mua background\n"
            "• `dtn bg check <id>` — xem trước hình nền\n"
            "• `dtn bg equip <id>` — trang bị background"
        )

    # ── dtn bg shop ──────────────────────────────────────────
    @bg.command(name="shop")
    async def bg_shop(self, ctx):
        """Cửa hàng background (phân trang 2 trang)."""
        bg_list     = list(BACKGROUNDS.items())
        PER_PAGE    = 10
        total_pages = 2

        def build_embed(page: int, inv_owned, inv_equipped):
            start = (page - 1) * PER_PAGE
            end   = start + PER_PAGE
            items = bg_list[start:end]

            embed = discord.Embed(
                title=f"🌿 Cửa Hàng Background Profile — Trang {page}/{total_pages}",
                description=(
                    "Dùng `dtn bg buy <id>` để mua\n"
                    "Dùng `dtn bg check <id>` để xem trước\n"
                    "Dùng `dtn bg equip <id>` để trang bị\n\n"
                ),
                color=discord.Color.green(),
            )

            for idx, (bg_id, info) in enumerate(items, start=start + 1):
                if bg_id == inv_equipped:
                    status = "✅ **Đang sử dụng**"
                elif bg_id in inv_owned:
                    status = "📦 Đã sở hữu"
                else:
                    status = "🛒 Chưa sở hữu"

                price_text = (
                    "Miễn phí" if info["price"] == 0
                    else f"{info['price']:,} <:Coin:1495831576397742241>"
                )
                embed.description += (
                    f"**{idx}. {info['name']}** — ID: `{bg_id}`\n"
                    f"└ Giá: {price_text} | {status}\n\n"
                )
            return embed

        class ShopView(discord.ui.View):
            def __init__(self, author_id, owned, equipped):
                super().__init__(timeout=60)
                self.author_id = author_id
                self.owned     = owned
                self.equipped  = equipped
                self.page      = 1
                self._update_buttons()

            def _update_buttons(self):
                self.prev_btn.disabled = (self.page == 1)
                self.next_btn.disabled = (self.page == total_pages)

            @discord.ui.button(label="◀ Trang trước", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message(
                        "❌ Đây không phải lệnh của bạn!", ephemeral=True
                    )
                    return
                self.page -= 1
                self._update_buttons()
                inv   = get_inventory(self.author_id)
                embed = build_embed(
                    self.page,
                    inv.get("owned_backgrounds", []),
                    inv.get("equipped_background"),
                )
                await interaction.response.edit_message(embed=embed, view=self)

            @discord.ui.button(label="Trang sau ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message(
                        "❌ Đây không phải lệnh của bạn!", ephemeral=True
                    )
                    return
                self.page += 1
                self._update_buttons()
                inv   = get_inventory(self.author_id)
                embed = build_embed(
                    self.page,
                    inv.get("owned_backgrounds", []),
                    inv.get("equipped_background"),
                )
                await interaction.response.edit_message(embed=embed, view=self)

        inv      = get_inventory(ctx.author.id)
        owned    = inv.get("owned_backgrounds", [])
        equipped = inv.get("equipped_background", None)

        view  = ShopView(ctx.author.id, owned, equipped)
        embed = build_embed(1, owned, equipped)
        await ctx.send(embed=embed, view=view)

    # ── dtn bg buy <id> ──────────────────────────────────────
    @bg.command(name="buy")
    async def bg_buy(self, ctx, bg_id: str = None):
        """Mua background từ shop."""
        if not bg_id:
            await ctx.send("❌ Vui lòng nhập ID! Ví dụ: `dtn bg buy 2928`")
            return
        if bg_id not in BACKGROUNDS:
            await ctx.send("❌ ID Background không tồn tại trong hệ thống!")
            return

        inv = get_inventory(ctx.author.id)
        if bg_id in inv.get("owned_backgrounds", []):
            await ctx.send("⚠️ Bạn đã sở hữu Background này rồi!")
            return

        bg_info = BACKGROUNDS[bg_id]
        price   = bg_info["price"]
        bal     = get_balance(ctx.author.id)

        if bal < price:
            await ctx.send(
                f"❌ Bạn không đủ tiền! Cần **{price:,}** "
                f"<:Coin:1495831576397742241> để mua."
            )
            return

        update_balance(ctx.author.id, -price)
        add_owned_bg(ctx.author.id, bg_id)

        await ctx.send(
            f"✅ Mua thành công **{bg_info['name']}** (ID: `{bg_id}`)!\n"
            f"Dùng `dtn bg equip {bg_id}` để trang bị ngay."
        )

    # ── dtn bg check <id> ────────────────────────────────────
    @bg.command(name="check")
    async def bg_check(self, ctx, bg_id: str = None):
        """Xem trước hình nền trước khi mua."""
        if not bg_id:
            await ctx.send("❌ Vui lòng nhập ID! Ví dụ: `dtn bg check 7386`")
            return
        if bg_id not in BACKGROUNDS:
            await ctx.send("❌ ID Background không tồn tại!")
            return

        bg_info   = BACKGROUNDS[bg_id]
        file_path = bg_info["file"]
        try:
            file = discord.File(file_path)
            await ctx.send(
                f"🖼️ Xem trước: **{bg_info['name']}** (ID: `{bg_id}`)",
                file=file,
            )
        except Exception:
            await ctx.send("❌ Hệ thống không tìm thấy file ảnh trên máy chủ!")

    # ── dtn bg equip <id> ────────────────────────────────────
    @bg.command(name="equip")
    async def bg_equip(self, ctx, bg_id: str = None):
        """Trang bị background cho profile."""
        if not bg_id:
            await ctx.send("❌ Vui lòng nhập ID! Ví dụ: `dtn bg equip 2927`")
            return
        if bg_id not in BACKGROUNDS:
            await ctx.send("❌ ID Background không tồn tại!")
            return

        inv = get_inventory(ctx.author.id)
        if bg_id not in inv.get("owned_backgrounds", []):
            await ctx.send(
                f"❌ Bạn chưa sở hữu Background `{bg_id}`! "
                f"Mua bằng `dtn bg buy {bg_id}`"
            )
            return

        if set_equipped_bg(ctx.author.id, bg_id):
            await ctx.send(
                f"✅ Đã trang bị **{BACKGROUNDS[bg_id]['name']}**! "
                f"Dùng `dtn profile` để xem."
            )
        else:
            await ctx.send("❌ Có lỗi xảy ra khi đổi background.")


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(Experience(bot))
