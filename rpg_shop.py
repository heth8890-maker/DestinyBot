"""
===== FILE: rpg_shop.py =====
Discord Cog shop. Tách từ rpg_game.py.

COMMAND MAP
──────────────────────────────────────────────────────────
  dtn shop crate
  dtn shop item
  dtn shop weapon                      — ★ weapon shop (reset 6h, 10 slot)
  dtn shopbuy <slot>                   — ★ mua weapon từ shop
──────────────────────────────────────────────────────────
"""

import discord
from discord.ext import commands

from rpg_core import (
    get_weapon_by_id,
    ITEMS, WEAPONS, CRATES, RARITY_COLOR,
)
from rpg_database import get_user, save_user
from rpg_weapon import (
    RARE_CRATE_WEAPONS,
    DARK_CRATE_WEAPON,
    _rarity_tier,
)
from rpg_addon import (
    load_weapon_shop,
    seconds_to_shop_reset,
    get_shop_slot,
    fmt_effect_val,
    mark_shop_slot_sold,
    add_weapon,
)
from rpg_game import (
    COIN_EMOJI,
    ERR,
    OK,
    _split_field,
    _paginate_fields,
    _send_paged,
    PageView,
)
from cash import update_balance_safe, get_balance


# ═══════════════════════════════════════════════════════════
# SHOP CRATE — CONFIG (scalable pagination)
# ─────────────────────────────────────────────────────────
# ★ THÊM CRATE MỚI:
#   1. Thêm entry vào CRATES (rpg_weapon.py / rpg_core.py)
#   2. Thêm 1 dòng vào _CRATE_POOL  (weapon pool bình thường)
#        HOẶC thêm 1 dòng vào _CUSTOM_DROPS (nếu có bảng drop đặc biệt)
#   3. (Tuỳ chọn) Thêm tên rút gọn vào _SHORT nếu tên quá dài
#   → Pagination tự động điều chỉnh, không cần sửa thêm.
# ═══════════════════════════════════════════════════════════

# Tên rút gọn để embed không bị vỡ
_SHORT: dict[str, str] = {
    "Gậy giám mục của thánh Nicholas": "Gậy Giám Mục",
    "Sự cứu rỗi của Hades":            "Sự Cứu Rỗi",
    "Tách trà thư giãn":               "Tách Trà",
    "Chiếc kéo của Apolo":             "Kéo Apolo",
    "Đuôi tắc kè hoa":                 "Đuôi TKH",
    "Ngôi sao may mắn":                "Sao May Mắn",
    # Thêm tên rút gọn mới tại đây nếu cần
}

# Weapon pool cho từng crate — thêm crate mới: 1 dòng
_CRATE_POOL: dict[str, list] = {
    "001": WEAPONS,
    "002": RARE_CRATE_WEAPONS,
    "003": DARK_CRATE_WEAPON,
    # "005": FUTURE_CRATE_WEAPONS,  ← ví dụ thêm sau
}

# Crate có bảng drop đặc biệt (không dùng weapon pool)
_CUSTOM_DROPS: dict[str, str] = {
    "004": (
        "  <a:4467:1498612536470409328> **Tam Hoả Thống Soái** — 0.3%  _★ Special_\n"
        "  <a:Hongiapbatdiet:1498612522272686101> **Hồn Giáp Bất Diệt** — 0.3%  _★ Special_\n"
        "  <a:Linhdiemsathan:1498612530094805123> **Linh Diệm Sát Thần** — 0.3%  _★ Special_\n"
        "  <:Linh_hoa:1498614127386562601> **Linh Hoả** (x4–18) — 35%\n"
        "  <:Coin:1495831576397742241> **Coin** (2,000–6,000) — 64.4%"
    ),
    # "006": "custom drop text cho crate 006",
}

CRATE_PAGE_SIZE = 2   # số crate hiển thị mỗi trang


def _total_crate_pages() -> int:
    return max(1, (len(CRATES) + CRATE_PAGE_SIZE - 1) // CRATE_PAGE_SIZE)


def _build_crate_page_embed(page: int) -> discord.Embed:
    """Tạo embed cho trang <page> của shop crate (0-indexed)."""
    crates_list = list(CRATES.items())
    total_pages = _total_crate_pages()
    page        = max(0, min(page, total_pages - 1))

    start      = page * CRATE_PAGE_SIZE
    page_items = crates_list[start : start + CRATE_PAGE_SIZE]

    embed = discord.Embed(
        title="<:Shop:1495464183037165763> Shop Crate",
        description=(
            "Mua crate để nhận weapon ngẫu nhiên!\n"
            "Trang bị weapon giúp: tăng ô hunt, giảm cooldown, "
            "tăng % giá bán, tăng tỉ lệ rare.\n\n"
            f" PAGE **{page + 1}** / **{total_pages}**"
        ),
        color=0xFF5722,
    )

    for crate_id, crate in page_items:
        if crate_id in _CUSTOM_DROPS:
            drop_text = _CUSTOM_DROPS[crate_id]
        else:
            pool = _CRATE_POOL.get(crate_id, WEAPONS)
            drop_text = "\n".join(
                f"  {w['emoji']} **{_SHORT.get(w['name'], w['name'])}** "
                f"— {w['chance']}%  _{_rarity_tier(w['rarity'])}_"
                for w in pool
            )
        embed.add_field(
            name=f"{crate['emoji']} {crate['name']}  |  ID: `{crate_id}`",
            value=(
                f"<:2245:1493575277605949480> Giá: **{crate['price']:,}** {COIN_EMOJI}\n\n"
                "**Bảng drop rate:**\n" + drop_text
            ),
            inline=False,
        )

    embed.set_footer(text="dtn crate buy <id> [amount]  |  dtn crate open <id>")
    return embed


class CrateShopView(discord.ui.View):
    """
    View phân trang cho shop crate.
    Tự động disable nút khi ở trang đầu/cuối.
    Timeout 60s — sau đó các nút bị khoá tự động.
    """

    def __init__(self, page: int = 0):
        super().__init__(timeout=60)
        self.page = max(0, min(page, _total_crate_pages() - 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        total = _total_crate_pages()
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= total - 1)

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_build_crate_page_embed(self.page), view=self
        )

    @discord.ui.button(label="Tiếp ▶", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_build_crate_page_embed(self.page), view=self
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


# ═══════════════════════════════════════════════════════════
# COG: SHOP  (crate + item + ★ weapon shop)
# ═══════════════════════════════════════════════════════════

class RPGShop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="shop", invoke_without_command=True)
    async def shop(self, ctx):
        await ctx.send(
            "<:Shop:1495464183037165763> **Shop:**\n"
            "• `dtn shop crate`  — mua crate / xem drop rate\n"
            "• `dtn shop item`   — danh sách vật phẩm & giá bán\n"
            "• `dtn shop weapon` — ★ weapon shop (reset 6h, 10 slot ngẫu nhiên)\n"
            "• `dtn shopbuy <slot>` — ★ mua weapon từ slot"
        )

    @shop.command(name="crate")
    async def shop_crate(self, ctx):
        view  = CrateShopView(page=0)
        embed = _build_crate_page_embed(0)
        await ctx.send(embed=embed, view=view)

    @shop.command(name="item")
    async def shop_item(self, ctx):
        rarity_order = ["common", "uncommon", "rare", "epic", "legendary", "legend"]
        grouped: dict[str, list] = {r: [] for r in rarity_order}
        for item in ITEMS:
            r = item["rarity"]
            if r not in grouped:
                grouped[r] = []
            grouped[r].append(item)

        fields: list[tuple] = []
        for rarity in rarity_order:
            items_in_tier = grouped.get(rarity, [])
            if not items_in_tier:
                continue
            lines = []
            for item in items_in_tier:
                pr = (
                    f"{item['min']:,} – {item['max']:,}"
                    if item["min"] != item["max"] else f"{item['min']:,}"
                )
                lines.append(
                    f"{item['emoji']} `{item['id']}` **{item['name']}** — {pr} {COIN_EMOJI}"
                )
            fields += _split_field(_rarity_tier(rarity), lines)

        title  = "<:2851:1495250164116492469> Danh sách Vật phẩm"
        desc   = "Vật phẩm kiếm được qua `dtn hunt`. Bán bằng `dtn sell item <id>`."
        footer = "Giá thực tế ngẫu nhiên trong range. Weapon trang bị tăng giá bán."
        embeds = _paginate_fields(fields, title=title, description=desc, color=0x5865F2, footer=footer)
        await _send_paged(ctx, embeds)

    @shop.command(name="weapon")
    async def shop_weapon(self, ctx):
        """Xem 10 weapon đang bán (reset mỗi 6 tiếng)."""
        shop      = load_weapon_shop()
        remaining = seconds_to_shop_reset()
        h, m      = divmod(remaining // 60, 60)

        desc = (
            f"⏳️ |  Reset sau **{h}h {m}m**  •  Dùng `dtn shopbuy <slot>` để mua.\n"
            f"<:Coin:1495831576397742241> |  Giá = base × (100% − drop rate) × 80%"
        )
        title = "<:Hamer:1495462570469888069> |  Weapon Shop"

        # Build all slot fields (logic cũ giữ nguyên)
        slot_fields: list[tuple] = []
        for s in shop["slots"]:
            w             = get_weapon_by_id(s["weapon_id"])
            effects       = w.get("effects", {}) if w else {}
            # ─ FIX: Lấy emoji từ WEAPONS thay vì emoji lưu cũ trong shop ─
            current_emoji = w.get("emoji", s['emoji']) if w else s['emoji']
            eff_str       = " | ".join(
                fmt_effect_val(k, v) for k, v in effects.items()
            ) or "—"
            rarity_e = _rarity_tier(s["rarity"])
            slot_fields.append((
                f"`[{s['slot']:02d}]` {current_emoji} {s['name']}  {rarity_e}",
                (
                    f"<:2245:1493575277605949480> | **{s['price']:,}** {COIN_EMOJI}  "
                    f"_(drop rate: {s['drop_rate']}%)_\n"
                    f"ID: `{s['weapon_id']}`\n"
                    f"<:Effect:1495466103047061679> {eff_str}"
                ),
                True,   # inline=True giữ nguyên layout 2 cột
            ))

        # Phân trang: 6 slot mỗi trang (số chẵn giữ đẹp layout inline 2 cột)
        _SLOTS_PER_PAGE = 6
        chunks = [
            slot_fields[i : i + _SLOTS_PER_PAGE]
            for i in range(0, len(slot_fields), _SLOTS_PER_PAGE)
        ]
        total  = len(chunks)
        embeds = []
        for i, chunk in enumerate(chunks):
            page_tag = f" • Trang {i + 1}/{total}" if total > 1 else ""
            embed = discord.Embed(
                title=title + page_tag,
                description=desc,
                color=0xE74C3C,
            )
            for fname, fvalue, finline in chunk:
                embed.add_field(name=fname, value=fvalue, inline=finline)
            embeds.append(embed)

        await _send_paged(ctx, embeds)


# ═══════════════════════════════════════════════════════════
# COG: SHOP BUY
# ═══════════════════════════════════════════════════════════

class RPGShopBuy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="shopbuy")
    async def shopbuy(self, ctx, slot: int):
        """Mua weapon từ slot trong Weapon Shop. `dtn shopbuy <slot>`"""
        slot_data = get_shop_slot(slot)
        if not slot_data:
            return await ctx.send(f"{ERR} | Slot `{slot}` không tồn tại. Xem `dtn shop weapon`.")

        price = slot_data["price"]
        bal   = get_balance(ctx.author.id)
        if bal < price:
            return await ctx.send(
                f"{ERR} | Không đủ tiền. Cần **{price:,}** {COIN_EMOJI} "
                f"(bạn có **{bal:,}** {COIN_EMOJI})."
            )

        uid  = str(ctx.author.id)
        user, upgraded_weapons = get_user(uid)

        await update_balance_safe(ctx.author.id, -price)
        # ── ALWAYS stackable: shop must never produce a UID ──────────────────
        add_weapon(user, slot_data["weapon_id"], make_unique=False)
        mark_shop_slot_sold(slot)
        if not save_user(uid, user, upgraded_weapons):
            return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

        w     = get_weapon_by_id(slot_data["weapon_id"])
        color = RARITY_COLOR.get(slot_data["rarity"], 0x5865F2)
        # ─ FIX: Lấy emoji từ WEAPONS thay vì emoji lưu cũ trong slot_data ─
        current_emoji = w.get("emoji", slot_data['emoji']) if w else slot_data['emoji']
        embed = discord.Embed(title="🛒 Mua weapon Thành Công!", color=color)
        embed.add_field(name="Vũ Khí", value=f"{current_emoji} **{slot_data['name']}**", inline=True)
        embed.add_field(name="ID",     value=f"`{slot_data['weapon_id']}`",                   inline=True)
        embed.add_field(name="Đã trả", value=f"{price:,} {COIN_EMOJI}",                       inline=True)
        if w:
            eff_str = " | ".join(
                fmt_effect_val(k, v) for k, v in w.get("effects", {}).items()
            ) or "—"
            embed.add_field(
                name="<:Effect:1495466103047061679> | Hiệu ứng",
                value=eff_str, inline=False,
            )
        embed.set_footer(text=f"Số dư: {get_balance(ctx.author.id):,} {COIN_EMOJI}")
        await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGShop(bot))
    await bot.add_cog(RPGShopBuy(bot))
