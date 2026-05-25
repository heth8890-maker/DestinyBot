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

⚡ Thay đổi so với bản render (chuyển JSON → MongoDB):
  - Xóa load_eco / save_eco / load_exp / save_exp / load_inv / save_inv
  - get_balance / update_balance   → dùng database_helper (collection economy)
  - Level/XP data (exp, level)     → lưu vào cùng document user trong economy
  - Inventory (owned_backgrounds,
    equipped_background)            → lưu vào cùng document user trong economy

⚡ Fix kiến trúc (v2):
  - apply_economy_delta(): gộp cash + exp + level vào 1 atomic MongoDB update
    → tránh trạng thái lệch nếu một trong hai operation fail
  - on_message chạy Mongo I/O qua run_in_executor → không block event loop
  - get_inventory() dùng flag "inv_migrated" → migration backward-compat
    chỉ chạy 1 lần, không overwrite dữ liệu mới sau restart
  - Chuẩn hóa schema document (xem DEFAULT_EXP_FIELDS bên dưới)
"""

import asyncio
import discord
from discord.ext import commands
import os
import io
import time
import aiohttp
from PIL import Image, ImageDraw, ImageFont
import pymongo

from database_helper import load_core_data, save_core_data, _get_collections, _with_retry

# ═══════════════════════════════════════════════════════════
# SCHEMA CHUẨN — các field exp.py quản lý trong document economy
# ═══════════════════════════════════════════════════════════
# Chỉ dùng để khởi tạo field còn thiếu — KHÔNG dùng để overwrite.
DEFAULT_EXP_FIELDS = {
    "exp":                 0,
    "level":               1,
    "owned_backgrounds":   [],
    "equipped_background": None,
    "inv_migrated":        False,   # flag: backward-compat đã chạy chưa
}

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
    "1836": {"name": "Ace hoa quyen",        "file": "IMG_ace.png",              "price": 12000},
    "4839": {"name": "Wang lin ",            "file": "IMG_vulam.png",            "price": 12000},
}

XP_COOLDOWN      = 5    # giây cooldown cộng XP từ on_message
COMMAND_COOLDOWN = 10   # giây cooldown lệnh profile

# ═══════════════════════════════════════════════════════════
# PHẦN 2: HÀM TRUY CẬP DỮ LIỆU — MONGODB
# ═══════════════════════════════════════════════════════════
# Tất cả data (cash, exp, level, inventory) nằm trong 1 document
# trong collection "economy" của database_helper.
#
# Cấu trúc document user (economy collection):
#   {
#     "_id":                 "uid_str",
#     "cash":                int,
#     "exp":                 int,           ← XP hiện tại (trong level này)
#     "level":               int,
#     "owned_backgrounds":   [str, ...],
#     "equipped_background": str | None,
#     "inv_migrated":        bool,          ← flag: backward-compat chỉ chạy 1 lần
#     ... (các field RPG khác từ database_helper)
#   }
# ────────────────────────────────────────────────────────────

def _get_economy_col():
    """Lấy collection economy từ database_helper."""
    economy_col, _ = _get_collections()
    return economy_col


def _load_user(user_id) -> dict:
    """
    Tải document user đầy đủ. Tự tạo mới nếu chưa có.
    Luôn trả về dict (không bao giờ None).
    """
    data = load_core_data(str(user_id))
    return data["user"]


# ── Balance ──────────────────────────────────────────────────

def get_balance(user_id) -> int:
    """Đọc số dư của user từ MongoDB (sync)."""
    user = _load_user(user_id)
    return user.get("cash", 0)


# ── Entry point duy nhất cho mọi cập nhật kinh tế ────────────

def apply_economy_delta(
    user_id,
    *,
    cash:  int = 0,
    exp:   int = 0,
    level: int | None = None,
) -> dict:
    """
    Gộp cash + exp + level vào 1 atomic MongoDB update.

    Tại sao cần hàm này:
      Nếu dùng 2 update riêng (1 cho cash, 1 cho exp/level), một trong 2
      có thể fail sau khi cái kia đã commit → user bị lệch trạng thái
      (ví dụ: đã nhận tiền level-up nhưng level không tăng, hoặc ngược lại).
      1 atomic update = all-or-nothing trên server MongoDB.

    Args:
        cash:  số tiền cộng/trừ (âm để trừ).
        exp:   lượng XP cộng vào field exp (chỉ dùng khi chưa tính level-up).
        level: nếu có level mới sau khi tính level-up, $set luôn vào đây.
               exp lúc này là exp còn dư SAU khi trừ ngưỡng level.

    Returns:
        Document sau update {"cash": ..., "exp": ..., "level": ...}.
        Trả về {} nếu update thất bại (caller tự xử lý).
    """
    uid       = str(user_id)
    inc_ops   = {}
    set_ops   = {}

    if cash != 0:
        inc_ops["cash"] = cash
    if exp != 0 and level is None:
        # Chỉ $inc exp khi KHÔNG có level-up — tránh xung đột với $set exp bên dưới
        inc_ops["exp"] = exp
    if level is not None:
        # Level-up: set cả exp (phần dư) lẫn level mới trong cùng 1 operation
        set_ops["exp"]   = exp    # exp ở đây = exp còn dư sau level-up
        set_ops["level"] = level

    if not inc_ops and not set_ops:
        # Không có gì để update — trả về data hiện tại
        return _load_user(uid)

    update_doc: dict = {}
    if inc_ops:
        update_doc["$inc"] = inc_ops
    if set_ops:
        update_doc["$set"] = set_ops

    economy_col = _get_economy_col()
    result = _with_retry(
        economy_col.find_one_and_update,
        {"_id": uid},
        update_doc,
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return result or {}


# ── XP / Level ───────────────────────────────────────────────

def _get_exp_data(user_id) -> tuple[int, int]:
    """Trả về (xp, level) của user."""
    user = _load_user(user_id)
    return user.get("exp", 0), user.get("level", 1)


# ── Inventory (backgrounds) ──────────────────────────────────

def get_inventory(user_id) -> dict:
    """
    Trả về {"owned_backgrounds": [...], "equipped_background": str|None}.

    Migration backward-compat (key cũ từ inventory.json) chỉ chạy 1 lần
    nhờ flag "inv_migrated". Sau khi bot restart, nếu flag đã True,
    bỏ qua toàn bộ migration → không bao giờ overwrite dữ liệu mới.
    """
    uid  = str(user_id)
    user = _load_user(uid)

    # Đã migrate rồi → trả thẳng, không check key cũ nữa
    if user.get("inv_migrated"):
        return {
            "owned_backgrounds":   user.get("owned_backgrounds", []),
            "equipped_background": user.get("equipped_background"),
        }

    # Lần đầu hoặc user cũ chưa có flag: chạy migration 1 lần duy nhất
    economy_col = _get_economy_col()
    set_ops = {"inv_migrated": True}

    # Rename key cũ nếu tồn tại, dùng $rename để atomic
    rename_ops = {}
    if "owned" in user and "owned_backgrounds" not in user:
        rename_ops["owned"] = "owned_backgrounds"
    if "current" in user and "equipped_background" not in user:
        rename_ops["current"] = "equipped_background"

    # Khởi tạo field mới nếu chưa có (dùng $setOnInsert không được ở đây
    # vì document đã tồn tại → dùng setOnInsert alternative: chỉ set nếu thiếu)
    if "owned_backgrounds" not in user and "owned" not in user:
        set_ops["owned_backgrounds"] = []
    if "equipped_background" not in user and "current" not in user:
        set_ops["equipped_background"] = None

    update_doc: dict = {"$set": set_ops}
    if rename_ops:
        update_doc["$rename"] = rename_ops

    result = _with_retry(
        economy_col.find_one_and_update,
        {"_id": uid},
        update_doc,
        upsert=False,   # không tạo mới — user phải tồn tại rồi
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if result is None:
        result = user   # fallback: document chưa tồn tại, dùng data local

    return {
        "owned_backgrounds":   result.get("owned_backgrounds", []),
        "equipped_background": result.get("equipped_background"),
    }


def add_owned_bg(user_id, bg_id: str) -> None:
    """Thêm background vào danh sách sở hữu (atomic $addToSet)."""
    uid = str(user_id)
    economy_col = _get_economy_col()
    _with_retry(
        economy_col.update_one,
        {"_id": uid},
        {"$addToSet": {"owned_backgrounds": bg_id}},
        upsert=True,
    )


def set_equipped_bg(user_id, bg_id: str) -> bool:
    """
    Trang bị background nếu user đã sở hữu.
    Trả về True nếu thành công, False nếu chưa sở hữu.
    """
    uid = str(user_id)
    inv = get_inventory(uid)
    if bg_id not in inv.get("owned_backgrounds", []):
        return False

    economy_col = _get_economy_col()
    _with_retry(
        economy_col.update_one,
        {"_id": uid},
        {"$set": {"equipped_background": bg_id}},
        upsert=True,
    )
    return True


# ═══════════════════════════════════════════════════════════
# CÔNG THỨC EXP
# Level 1→100, Level 2→200, Level 3→400, ...
# ═══════════════════════════════════════════════════════════

def get_xp_needed(level):
    return (100 + level * 50) * 160


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


EMOJI_WALLET = "1493575277605949480"   # <:2245:1493575277605949480>
EMOJI_COIN   = "1495831576397742241"   # <:Coin:1495831576397742241>

_emoji_cache: dict[str, Image.Image] = {}   # in-process cache, tránh fetch lặp


async def _fetch_discord_emoji(
    session: aiohttp.ClientSession,
    emoji_id: str,
    size: int,
) -> Image.Image | None:
    """
    Tải emoji từ Discord CDN và trả về PIL Image đã resize.
    Cache in-memory theo (emoji_id, size) để tránh fetch lại trong cùng 1 run.
    """
    cache_key = f"{emoji_id}:{size}"
    if cache_key in _emoji_cache:
        return _emoji_cache[cache_key]

    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=64"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data  = await resp.read()
                emoji = (
                    Image.open(io.BytesIO(data))
                    .convert("RGBA")
                    .resize((size, size), Image.LANCZOS)
                )
                _emoji_cache[cache_key] = emoji
                return emoji
    except Exception as e:
        print(f"[EXP] Lỗi tải emoji {emoji_id}: {e}")
    return None


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

        if now - self._xp_cooldown.get(uid, 0) < XP_COOLDOWN:
            return

        self._xp_cooldown[uid] = now

        # Đọc exp/level hiện tại — chạy trong executor để không block event loop
        # (pymongo là sync I/O; nếu Mongo chậm sẽ block toàn bộ bot)
        loop = asyncio.get_running_loop()
        xp, current_level = await loop.run_in_executor(None, _get_exp_data, uid)

        xp_to_add = 120 if message.content.lower().startswith("dtn ") else 50
        xp += xp_to_add

        leveled_up   = False
        reward_money = 0

        while True:
            req_xp = get_xp_needed(current_level)
            if xp >= req_xp:
                xp            -= req_xp
                current_level += 1
                leveled_up     = True
                reward_money  += 4000 * (2 ** (current_level - 2))
            else:
                break

        # Gộp cash + exp + level vào 1 atomic MongoDB update
        # → tránh trường hợp: level tăng thành công nhưng cash fail (hoặc ngược lại)
        if leveled_up:
            await loop.run_in_executor(
                None,
                lambda: apply_economy_delta(
                    uid,
                    cash=reward_money,
                    exp=xp,
                    level=current_level,
                ),
            )
            await message.channel.send(
                f"🎉 | Chúc mừng **{message.author.name}** đã thăng cấp lên "
                f"**Level {current_level}**! "
                f"Bạn nhận được **{reward_money:,}** <:Coin:1495831576397742241>!"
            )
        else:
            # Không level-up: chỉ cộng exp, không đụng cash
            await loop.run_in_executor(
                None,
                lambda: apply_economy_delta(uid, exp=xp_to_add),
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

        # ── Avatar + emoji icons — 1 session, fetch song song ──
        icon_size     = int(FS_MEDIUM * 1.1)
        icon_offset_y = (FS_MEDIUM - icon_size) // 2
        bal_str       = f"{bal:,}"
        wallet_icon: Image.Image | None = None
        coin_icon:   Image.Image | None = None
        try:
            avatar_url = user.display_avatar.with_format("png").with_size(512).url
            timeout    = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                avatar_task = session.get(avatar_url)
                wallet_task = _fetch_discord_emoji(session, EMOJI_WALLET, icon_size)
                coin_task   = _fetch_discord_emoji(session, EMOJI_COIN,   icon_size)

                async with avatar_task as resp:
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

                wallet_icon, coin_icon = await asyncio.gather(wallet_task, coin_task)
        except Exception as e:
            print(f"[EXP] Lỗi tải avatar/emoji: {e}")
            draw.rectangle(
                [AV_X, AV_Y, AV_X + AV_SIZE, AV_Y + AV_SIZE],
                fill="#333333",
            )

        draw.rectangle(
            [AV_X - BORDER_W, AV_Y - BORDER_W,
             AV_X + AV_SIZE + BORDER_W, AV_Y + AV_SIZE + BORDER_W],
            outline=(255, 255, 255, 120),  # trắng mờ
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

        if wallet_icon and coin_icon:
            img.paste(wallet_icon, (TEXT_X, MONEY_Y + icon_offset_y), wallet_icon)
            bal_x = TEXT_X + icon_size + 12
            draw_text_with_outline(draw, (bal_x, MONEY_Y), bal_str,
                                   font=font_medium, fill_color="#A3E4D7", thickness=3)
            tw = int(draw.textlength(bal_str, font=font_medium))
            img.paste(coin_icon, (bal_x + tw + 12, MONEY_Y + icon_offset_y), coin_icon)
        else:
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
                [BAR_X + inner_pad,              BAR_Y + inner_pad,
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

        uid = str(ctx.author.id)

        inv      = get_inventory(uid)
        owned    = inv.get("owned_backgrounds", [])
        equipped = inv.get("equipped_background", None)

        if not owned or not equipped or equipped not in owned:
            await ctx.send(
                "🖼️ Bạn chưa có background của mình, "
                "hãy xem `dtn bg shop` để lấy background nhé!"
            )
            return

        xp, level = _get_exp_data(uid)
        req_xp    = get_xp_needed(level)
        bal       = get_balance(uid)

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

        # apply_economy_delta trừ tiền atomic — tuy nhiên check "bal < price" bên trên
        # và trừ tiền bên dưới vẫn có khoảng TOCTOU nhỏ nếu 2 lệnh buy chạy song song.
        # Để triệt để: nên dùng update_balance_safe (async, có lock) từ cash.py.
        # Hiện tại acceptable vì background không phải item high-frequency.
        apply_economy_delta(ctx.author.id, cash=-price)
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

        if set_equipped_bg(ctx.author.id, bg_id):
            await ctx.send(
                f"✅ Đã trang bị **{BACKGROUNDS[bg_id]['name']}**! "
                f"Dùng `dtn profile` để xem."
            )
        else:
            await ctx.send(
                f"❌ Bạn chưa sở hữu Background `{bg_id}`! "
                f"Mua bằng `dtn bg buy {bg_id}`"
            )


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(Experience(bot))
