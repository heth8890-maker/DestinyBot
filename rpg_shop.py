"""
===== FILE: rpg_shop.py =====
Discord Cog shop. Tách từ rpg_game.py.

COMMAND MAP
──────────────────────────────────────────────────────────
  dtn shop crate
  dtn shop item
  dtn shop weapon                      — ★ weapon shop (reset 6h, 10 slot)
  dtn shop buy <slot>                  — ★ mua weapon từ weapon shop
  dtn shop event                       — xem shop event (Soul Crate)
  dtn ebuy 004 [amount]               — mua Soul Crate bằng Linh hoả (MongoDB)

SLASH COMMANDS
──────────────────────────────────────────────────────────
  /shop crate
  /shop item
  /shop weapon
  /shop buy slot:<int>
  /shop event
  /ebuy item_id:<str> amount:<int>
──────────────────────────────────────────────────────────
"""

import discord
from discord import app_commands
from discord.ext import commands

from rpg_core import (
    get_weapon_by_id,
    ITEMS, WEAPONS, CRATES, RARITY_COLOR,
    add_weapon,
    add_item,
)
from database_helper import load_core_data, save_core_data
from rpg_database import get_user, save_user
from rpg_weapon_data import (
    RARE_CRATE_WEAPONS,
    DARK_CRATE_WEAPON,
    PARADISE_CRATE_WEAPONS,
    BOOK_OF_GODLY_WEAPONS,
    _rarity_tier,
)
from rpg_addon import (
    load_weapon_shop,
    seconds_to_shop_reset,
    get_shop_slot,
    fmt_effect_val,
    mark_shop_slot_sold,
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
    "Đinh ba của Poisedon":            "Đinh Ba Poisedon",
    "Thần thời gian Chronus":          "Chronus",
    "Surtr bản ngã hoàng kim":         "Surtr Hoàng Kim",
    "Lôi thần Indra":                  "Indra",
    # Thêm tên rút gọn mới tại đây nếu cần
}

# Weapon pool cho từng crate — thêm crate mới: 1 dòng
_CRATE_POOL: dict[str, list] = {
    "001": WEAPONS,
    "002": RARE_CRATE_WEAPONS,
    "003": DARK_CRATE_WEAPON,
    "005": PARADISE_CRATE_WEAPONS,
    # "005": FUTURE_CRATE_WEAPONS,  ← ví dụ thêm sau
}

# Crate có bảng drop đặc biệt (không dùng weapon pool)
_CUSTOM_DROPS: dict[str, str] = {
    "004": (
        "  <a:4574:1499013628672610334> **Tam Hoả Thống Soái** — 0.3%  _★ Special_\n"
        "  <a:4572:1499013638319505530> **Hồn Giáp Bất Diệt** — 0.3%  _★ Special_\n"
        "  <a:4573:1499013635555463198> **Linh Diệm Sát Thần** — 0.3%  _★ Special_\n"
        "  <:Linh_hoa:1498614127386562601> **Linh Hoả** (x4–18) — 35%\n"
        "  <:Coin:1495831576397742241> **Coin** (2,000–6,000) — 64.4%"
    ),
    "006": "  <a:4572:1499013638319505530> **Hồn Giáp Bất Diệt** — 100%  _★ Special_",
    "007": "  <a:4573:1499013635555463198> **Linh Diệm Sát Thần** — 100%  _★ Special_",
    "008": "  <a:4574:1499013628672610334> **Tam Hoả Thống Soái** — 100%  _★ Special_",
    "009": (
        "  <a:5610:1505051859537104906> **Lôi thần Indra** — 33.33%  _Mythical_\n"
        "  <a:5611:1505052271182872576> **Thần thời gian Chronus** — 33.33%  _Mythical_\n"
        "  <a:5612:1505052278753595402> **Surtr bản ngã hoàng kim** — 33.33%  _Mythical_\n"
        "  ⚠ _Không thể mua trực tiếp — chỉ drop từ Crate of Paradise (006)_"
    ),
}

CRATE_PAGE_SIZE = 2   # số crate hiển thị mỗi trang (cho crate thường)

# ★ Nhóm crate hiển thị INLINE trên cùng 1 trang (hàng ngang)
# Thêm/bớt ID tại đây nếu muốn gộp thêm crate vào trang đặc biệt
_INLINE_GROUP: list[str] = ["006", "007", "008"]


def _build_crate_pages() -> list[list[tuple[str, dict]]]:
    """
    Tạo danh sách các trang, mỗi trang là list[(crate_id, crate)].
    - Các crate thuộc _INLINE_GROUP được gộp chung 1 trang (inline).
    - Các crate còn lại chia theo CRATE_PAGE_SIZE như bình thường.
    """
    crates_list = list(CRATES.items())

    inline_page  : list[tuple[str, dict]] = []
    normal_items : list[tuple[str, dict]] = []

    for crate_id, crate in crates_list:
        if crate_id in _INLINE_GROUP:
            inline_page.append((crate_id, crate))
        else:
            normal_items.append((crate_id, crate))

    # Chia normal_items theo CRATE_PAGE_SIZE
    normal_pages: list[list[tuple[str, dict]]] = []
    for i in range(0, max(1, len(normal_items)), CRATE_PAGE_SIZE):
        chunk = normal_items[i : i + CRATE_PAGE_SIZE]
        if chunk:
            normal_pages.append(chunk)

    # Chèn trang inline vào đúng vị trí (theo thứ tự xuất hiện của ID đầu tiên trong _INLINE_GROUP)
    # Tìm index của crate đầu tiên trong _INLINE_GROUP trong danh sách gốc
    all_ids = [cid for cid, _ in crates_list]
    insert_at = len(normal_pages)  # mặc định cuối
    for crate_id in _INLINE_GROUP:
        if crate_id in all_ids:
            pos = all_ids.index(crate_id)
            # Đếm có bao nhiêu normal_items nằm trước pos này
            normal_before = sum(
                1 for cid, _ in crates_list[:pos] if cid not in _INLINE_GROUP
            )
            insert_at = normal_before // CRATE_PAGE_SIZE
            break

    if inline_page:
        normal_pages.insert(insert_at, inline_page)

    return normal_pages if normal_pages else [[]]


def _total_crate_pages() -> int:
    return len(_build_crate_pages())


def _build_crate_page_embed(page: int) -> discord.Embed:
    """Tạo embed cho trang <page> của shop crate (0-indexed)."""
    pages       = _build_crate_pages()
    total_pages = len(pages)
    page        = max(0, min(page, total_pages - 1))
    page_items  = pages[page]

    # Trang này có phải trang inline không?
    is_inline_page = all(crate_id in _INLINE_GROUP for crate_id, _ in page_items)

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
            # Crate 100% (pool chỉ 1 weapon): hiển thị gọn trên 1 dòng
            if len(pool) == 1:
                w = pool[0]
                drop_text = (
                    f"{w['emoji']} **{_SHORT.get(w['name'], w['name'])}**"
                    f" — {w['chance']}%  _{_rarity_tier(w['rarity'])}_"
                )
            else:
                drop_text = "\n".join(
                    f"  {w['emoji']} **{_SHORT.get(w['name'], w['name'])}** "
                    f"— {w['chance']}%  _{_rarity_tier(w['rarity'])}_"
                    for w in pool
                )

        embed.add_field(
            name=f"{crate['emoji']} {crate['name']}  |  ID: `{crate_id}`",
            value=(
                f"<:2245:1493575277605949480> Giá: "
                + (
                    "**Không bán** _(drop từ Crate of Paradise 006)_"
                    if crate_id == "009"
                    else f"**{crate['price']:,}** {COIN_EMOJI}"
                )
                + "\n\n**Drop rate:**\n" + drop_text
            ),
            inline=is_inline_page,  # inline=True → hàng ngang; False → hàng dọc
        )

    embed.set_footer(text="dtn crate buy <id> [amount]  |  dtn crate open <id>")
    return embed


class CrateShopView(discord.ui.View):
    """
    View phân trang cho shop crate.
    Tự động disable nút khi ở trang đầu/cuối.
    Timeout 60s — sau đó các nút bị khoá tự động.
    Chỉ author gốc mới được bấm nút.
    """

    def __init__(self, page: int = 0, author_id: int | None = None):
        super().__init__(timeout=60)
        self.page      = max(0, min(page, _total_crate_pages() - 1))
        self.author_id = author_id
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        total = _total_crate_pages()
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= total - 1)

    async def _check_author(self, interaction: discord.Interaction) -> bool:
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Chỉ người dùng lệnh mới được bấm nút này.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_author(interaction):
            return
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_build_crate_page_embed(self.page), view=self
        )

    @discord.ui.button(label="Tiếp ▶", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_author(interaction):
            return
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_build_crate_page_embed(self.page), view=self
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


# ═══════════════════════════════════════════════════════════
# HELPERS — dùng chung cho prefix & slash
# ═══════════════════════════════════════════════════════════

def _build_shop_help_text() -> str:
    return (
        "<:Shop:1495464183037165763> **Shop:**\n"
        "• `dtn shop crate`        — mua crate / xem drop rate\n"
        "• `dtn shop item`         — danh sách vật phẩm & giá bán\n"
        "• `dtn shop weapon`       — ★ weapon shop (reset 6h, 10 slot)\n"
        "• `dtn shop event`        — shop event Soul Crate"
    )


async def _do_shop_crate(send_fn, author_id: int | None = None):
    """Gửi embed shop crate. send_fn nhận (embed=, view=)."""
    view  = CrateShopView(page=0, author_id=author_id)
    embed = _build_crate_page_embed(0)
    await send_fn(embed=embed, view=view)


def _build_shop_item_embeds() -> list[discord.Embed]:
    """Trả về danh sách embed item shop (sync, dùng chung prefix & slash)."""
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
    return _paginate_fields(fields, title=title, description=desc, color=0x5865F2, footer=footer)


def _build_weapon_shop_embeds() -> list[discord.Embed]:
    """Trả về danh sách embed weapon shop (dùng chung cho prefix & slash)."""
    shop      = load_weapon_shop()
    remaining = seconds_to_shop_reset()
    h, m      = divmod(remaining // 60, 60)

    desc = (
        f"⏳️ |  Reset sau **{h}h {m}m**  •  Dùng `dtn shop buy <slot>` hoặc `/shop buy` để mua.\n"
        f"<:Coin:1495831576397742241> |  Giá = base × (100% − drop rate) × 80%"
    )
    title = "<:Hamer:1495462570469888069> |  Weapon Shop"

    slot_fields: list[tuple] = []
    for s in shop["slots"]:
        w             = get_weapon_by_id(s["weapon_id"])
        effects       = w.get("effects", {}) if w else {}
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
            True,
        ))

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
    return embeds


def _build_event_embed() -> discord.Embed:
    embed = discord.Embed(
        title="<:Shop:1495464183037165763> Shop Event",
        description=(
            "Dùng Linh hoả để đổi Soul Crate hiếm!\n"
            "Ma Hỏa Thống Soái 0.3% | Linh Diệm Sát Thần 0.3% | "
            "Hồn Giáp Bất Diệt 0.3% | Linh Hoả 35% | 64.4% 2000–6000 Coin"
        ),
        color=0xCCFFCC,
    )
    embed.add_field(
        name="<:Soulcrate:1498617031501807646> | Soul Crate (ID: 004)",
        value=(
            "**Giá:** 25x <:Linh_hoa:1498614127386562601> Linh hoả\n"
            "**Lệnh mua:** `dtn ebuy 004 [số lượng]`  hoặc  `/ebuy item_id:004`"
        ),
        inline=False,
    )
    return embed


# ═══════════════════════════════════════════════════════════
# COG: SHOP  (crate + item + weapon shop + event shop)
# ═══════════════════════════════════════════════════════════

class RPGShop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ──────────────────────────────────────────────────────
    # PREFIX COMMANDS
    # ──────────────────────────────────────────────────────

    # ─── HELP ───
    @commands.group(name="shop", invoke_without_command=True)
    async def shop(self, ctx):
        await ctx.send(_build_shop_help_text())

    # ─── CRATE ───
    @shop.command(name="crate")
    async def shop_crate(self, ctx):
        await _do_shop_crate(ctx.send, author_id=ctx.author.id)

    # ─── ITEM ───
    @shop.command(name="item")
    async def shop_item(self, ctx):
        await _send_paged(ctx, _build_shop_item_embeds())

    # ─── WEAPON SHOP ───
    @shop.command(name="weapon")
    async def shop_weapon(self, ctx):
        """Xem 10 weapon đang bán (reset mỗi 6 tiếng)."""
        await _send_paged(ctx, _build_weapon_shop_embeds())

    # ─── BUY (weapon shop) ───
    @shop.command(name="buy")
    async def shop_buy(self, ctx, slot: int):
        """Mua weapon từ slot trong Weapon Shop. `dtn shop buy <slot>`"""
        await _handle_shop_buy(ctx.author, slot, ctx.send)

    # ─── EVENT SHOP (xem) ───
    @shop.command(name="event")
    async def shop_event(self, ctx):
        """Xem shop event Soul Crate. `dtn shop event`"""
        await ctx.send(embed=_build_event_embed())

    # ──────────────────────────────────────────────────────
    # SLASH COMMANDS
    # ──────────────────────────────────────────────────────

    shop_slash = app_commands.Group(
        name="shop",
        description="Các lệnh shop RPG",
        guild_only=True,
    )

    @shop_slash.command(name="crate", description="Xem shop crate và bảng drop rate")
    async def slash_shop_crate(self, interaction: discord.Interaction):
        view  = CrateShopView(page=0, author_id=interaction.user.id)
        embed = _build_crate_page_embed(0)
        await interaction.response.send_message(embed=embed, view=view)

    @shop_slash.command(name="item", description="Xem danh sách vật phẩm và giá bán")
    async def slash_shop_item(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embeds = _build_shop_item_embeds()
        if not embeds:
            await interaction.followup.send("Không có dữ liệu item.", ephemeral=True)
            return
        await interaction.followup.send(embed=embeds[0])
        for e in embeds[1:]:
            await interaction.followup.send(embed=e)

    @shop_slash.command(name="weapon", description="Xem 10 weapon đang bán (reset mỗi 6 tiếng)")
    async def slash_shop_weapon(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embeds = _build_weapon_shop_embeds()
        if not embeds:
            await interaction.followup.send("Weapon shop hiện trống.", ephemeral=True)
            return
        await interaction.followup.send(embed=embeds[0])
        for e in embeds[1:]:
            await interaction.followup.send(embed=e)

    @shop_slash.command(name="buy", description="Mua weapon từ slot trong Weapon Shop")
    @app_commands.describe(slot="Số thứ tự slot (xem /shop weapon)")
    async def slash_shop_buy(self, interaction: discord.Interaction, slot: int):
        await interaction.response.defer()
        await _handle_shop_buy(
            interaction.user,
            slot,
            interaction.followup.send,
        )

    @shop_slash.command(name="event", description="Xem shop event Soul Crate")
    async def slash_shop_event(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_event_embed())


# ═══════════════════════════════════════════════════════════
# HELPER — xử lý logic mua weapon (dùng chung prefix & slash)
# ═══════════════════════════════════════════════════════════

async def _handle_shop_buy(member: discord.Member | discord.User, slot: int, send_fn) -> None:
    slot_data = get_shop_slot(slot)
    if not slot_data:
        return await send_fn(f"{ERR} | Slot `{slot}` không tồn tại. Xem `dtn shop weapon` hoặc `/shop weapon`.")

    price = slot_data["price"]
    bal   = get_balance(member.id)
    if bal < price:
        return await send_fn(
            f"{ERR} | Không đủ tiền. Cần **{price:,}** {COIN_EMOJI} "
            f"(bạn có **{bal:,}** {COIN_EMOJI})."
        )

    uid  = str(member.id)
    user, _ = get_user(uid)

    await update_balance_safe(member.id, -price)
    # ── ALWAYS stackable: shop must never produce a UID ──────────────────
    add_weapon(user, slot_data["weapon_id"], make_unique=False)
    mark_shop_slot_sold(slot)
    if not save_user(uid, user):
        return await send_fn(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

    w     = get_weapon_by_id(slot_data["weapon_id"])
    color = RARITY_COLOR.get(slot_data["rarity"], 0x5865F2)
    current_emoji = w.get("emoji", slot_data['emoji']) if w else slot_data['emoji']
    embed = discord.Embed(title="🛒 Mua weapon Thành Công!", color=color)
    embed.add_field(name="Vũ Khí", value=f"{current_emoji} **{slot_data['name']}**", inline=True)
    embed.add_field(name="ID",     value=f"`{slot_data['weapon_id']}`",               inline=True)
    embed.add_field(name="Đã trả", value=f"{price:,} {COIN_EMOJI}",                   inline=True)
    if w:
        eff_str = " | ".join(
            fmt_effect_val(k, v) for k, v in w.get("effects", {}).items()
        ) or "—"
        embed.add_field(
            name="<:Effect:1495466103047061679> | Hiệu ứng",
            value=eff_str, inline=False,
        )
    embed.set_footer(text=f"Số dư: {get_balance(member.id):,} {COIN_EMOJI}")
    await send_fn(embed=embed)


# ═══════════════════════════════════════════════════════════
# COG: EVENT BUY  (ebuy / eventbuy)  — MongoDB
# ═══════════════════════════════════════════════════════════

class RPGEventBuy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── PREFIX ───
    @commands.command(name="eventbuy", aliases=["ebuy"])
    async def event_buy(self, ctx, item_id: str = None, amount: int = 1):
        """Mua Soul Crate bằng Linh hoả. `dtn ebuy 004 [số lượng]`"""
        await _handle_event_buy(ctx.author, item_id, amount, ctx.send)

    # ─── SLASH ───
    @app_commands.command(name="ebuy", description="Mua Soul Crate bằng Linh hoả")
    @app_commands.guild_only()
    @app_commands.describe(
        item_id="ID vật phẩm (hiện tại chỉ hỗ trợ: 004)",
        amount="Số lượng muốn mua (mặc định: 1)",
    )
    async def slash_ebuy(
        self,
        interaction: discord.Interaction,
        item_id: str,
        amount: int = 1,
    ):
        await interaction.response.defer()
        await _handle_event_buy(
            interaction.user,
            item_id,
            amount,
            interaction.followup.send,
        )

    @slash_ebuy.autocomplete("item_id")
    async def ebuy_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        choices = [
            app_commands.Choice(name="Soul Crate (004)", value="004"),
        ]
        return [c for c in choices if current.lower() in c.name.lower()]


# ═══════════════════════════════════════════════════════════
# HELPER — xử lý logic ebuy (dùng chung prefix & slash)
# ═══════════════════════════════════════════════════════════

async def _handle_event_buy(
    member: discord.Member | discord.User,
    item_id: str | None,
    amount: int,
    send_fn,
) -> None:
    if not item_id or item_id != "004":
        return await send_fn(
            f"{ERR} | ID vật phẩm không đúng. Sử dụng: `dtn ebuy 004 [số lượng]` hoặc `/ebuy item_id:004`"
        )
    if amount <= 0:
        return await send_fn(f"{ERR} | Số lượng không hợp lệ.")

    uid           = str(member.id)
    currency_id   = "5200"   # Linh hoả
    crate_id      = "004"    # Soul Crate
    cost_per_unit = 25
    total_cost    = cost_per_unit * amount

    # ── Tải user từ MongoDB ──
    data = load_core_data(uid)
    user = data["user"]

    user_inv = user.get("inv", {})
    if user_inv.get(currency_id, 0) < total_cost:
        missing = total_cost - user_inv.get(currency_id, 0)
        return await send_fn(
            f"{ERR} | Bạn thiếu **{missing}** "
            f"<:Linh_hoa:1498614127386562601> Linh hoả (ID: 5200) "
            f"để thực hiện giao dịch này."
        )

    # ── Trừ Linh hoả, thêm Soul Crate ──
    user["inv"][currency_id] -= total_cost
    add_item(user, f"crate_{crate_id}", amount)

    # ── Lưu lên MongoDB ──
    if not save_core_data(uid, user):
        return await send_fn(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

    await send_fn(
        f"{OK} | Chúc mừng bạn đã đổi thành công **{total_cost}** Linh hoả "
        f"lấy **{amount}x** <:Soulcrate:1498617031501807646> **Soul Crate**!"
    )


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    # Remove trước để tránh slash bị stale sau reload extension
    for name in ("shop", "ebuy"):
        try:
            bot.tree.remove_command(name)
        except Exception:
            pass

    await bot.add_cog(RPGShop(bot))
    await bot.add_cog(RPGEventBuy(bot))
