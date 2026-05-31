"""
===== FILE: rpg_game.py =====
Discord Cog chính. Các cog đã tách ra file riêng:
  rpg_crate.py    → dtn crate
  rpg_question.py → dtn quest
  rpg_trade.py    → dtn trade / dtn add / dtn remove
  rpg_weapon.py   → dtn weapon (tất cả subcommand)
  rpg_item.py     → dtn item / dtn item use
  rpg_shop.py     → dtn shop / dtn shopbuy   ← TÁCH RIÊNG
  rpg_sell.py     → dtn sell / /sell          ← TÁCH RIÊNG

COMMAND MAP
──────────────────────────────────────────────────────────
  dtn inv      (prefix)
  /inv         (slash)
──────────────────────────────────────────────────────────

⚡ THAY ĐỔI v3 (Weapon Identity Layer):
  - inv         : dùng WeaponID.is_unique() thay vì "-" not in (chuẩn hoá logic)
  - inv equipped: dùng entity.fmt_name() — thống nhất hiển thị với status/upgrade
  - Xoá duplicate _equipped_display (đã có trong rpg_weapon.py, import từ đó)

⚡ THAY ĐỔI v4:
  - RPGSell + _resolve_sell_weapon_targets tách sang rpg_sell.py
  - Thêm slash command /inv cho RPGInventory

⚡ THAY ĐỔI v6 (Components v2):
  - Chuyển toàn bộ từ Embed → Container (Components v2)
  - Dùng Separator để phân trang giữa các section
  - Thêm header text hướng dẫn lệnh ở trên cùng container
  - Crate hiển thị id và rarity giống item thường
  - _paginate_fields / PageView / _send_paged giữ nguyên cho compat

⚡ THAY ĐỔI v5 (Cleanup):
  - Xoá toàn bộ import thừa còn lại từ các cog đã tách (refactoring debt)
  - Gộp _send_paged + _send_paged_inter thành _send_paged chung
  - Fix PageView.on_timeout: edit message để disable nút thật sự trên Discord
  - Fix slash_inv: thêm try/except, loại bỏ gọi interaction.user.id dư
  - _build_inv_embeds nhận balance sẵn thay vì tự gọi get_balance() bên trong
"""

import discord
from discord import app_commands
from discord.ext import commands

from rpg_core import (
    get_item_by_id,
    CRATES,
    get_user,
    load_data,
)
from rpg_weapon_data import _rarity_tier
from cash import get_balance

# ── Cosmetic ───────────────────────────────────────────────
COIN_EMOJI = "<:Coin:1495831576397742241>"
SKULL_EMOJI = "<:2859:1495250145942704189>"
SWORD_EMOJI = "<:2918:1495252941492457502>"
HUNT_CD_SEC = 16

ERR = "<:X_:1495466670616219819>"
OK  = "<:Tick:1495466684520206528>"

# ── Components v2 flag ─────────────────────────────────────
_cv2_flags = discord.MessageFlags()
_cv2_flags.value = 1 << 15   # IS_COMPONENTS_V2



# ═══════════════════════════════════════════════════════════
# PAGINATION HELPERS
# ═══════════════════════════════════════════════════════════

_FIELD_VALUE_CAP = 1000   # max chars per embed field value (Discord hard limit = 1024)
_PAGE_TOTAL_CAP  = 4000   # max total field chars per embed page (Discord total = 6000)


def _split_field(
    name: str,
    lines: list[str],
    inline: bool = False,
    empty_fallback: str = "_Trống_",
) -> list[tuple]:
    """
    Chia list[str] thành 1+ field tuple (name, value, inline).
    Đảm bảo mỗi field value không vượt _FIELD_VALUE_CAP.
    """
    if not lines:
        return [(name, empty_fallback, inline)]

    chunks: list[str] = []
    cur:    list[str] = []
    cur_len = 0
    for line in lines:
        ll = len(line) + 1          # +1 cho ký tự newline
        if cur and cur_len + ll > _FIELD_VALUE_CAP:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += ll
    if cur:
        chunks.append("\n".join(cur))

    if len(chunks) == 1:
        return [(name, chunks[0], inline)]
    return [
        (f"{name} ({i + 1}/{len(chunks)})", chunk, inline)
        for i, chunk in enumerate(chunks)
    ]


def _paginate_fields(
    fields:      list[tuple],
    title:       str,
    description: str = "",
    color:       int = 0x5865F2,
    footer:      str = "",
) -> list[discord.Embed]:
    """
    Gộp list field (name, value, inline) thành nhiều Embed page.
    Mỗi page không vượt _PAGE_TOTAL_CAP tổng ký tự field.
    """
    pages: list[list[tuple]] = []
    cur_page:  list[tuple]   = []
    cur_total = len(description)

    for f in fields:
        f_chars = len(f[0]) + len(f[1])
        if cur_page and cur_total + f_chars > _PAGE_TOTAL_CAP:
            pages.append(cur_page)
            cur_page  = []
            cur_total = len(description)
        cur_page.append(f)
        cur_total += f_chars

    if cur_page:
        pages.append(cur_page)
    if not pages:
        pages = [[]]

    total  = len(pages)
    embeds = []
    for i, page_fields in enumerate(pages):
        page_tag = f" • Trang {i + 1}/{total}" if total > 1 else ""
        embed = discord.Embed(
            title=title + page_tag,
            description=description,
            color=color,
        )
        for fname, fvalue, finline in page_fields:
            embed.add_field(name=fname, value=fvalue, inline=finline)
        if footer:
            embed.set_footer(text=footer)
        embeds.append(embed)

    return embeds


class PageView(discord.ui.View):
    """
    View phân trang chung dùng cho inv / shop item / shop weapon.
    Nút ◀ Trước / Tiếp ▶ — tự disable ở trang đầu/cuối. Timeout 60s.
    """

    def __init__(self, embeds: list[discord.Embed], page: int = 0):
        super().__init__(timeout=60)
        self.embeds  = embeds
        self.page    = max(0, min(page, len(embeds) - 1))
        self.message: discord.Message | None = None   # gán sau khi send
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= len(self.embeds) - 1)

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.page], view=self
        )

    @discord.ui.button(label="Tiếp ▶", style=discord.ButtonStyle.primary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.page], view=self
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        # Cập nhật message thật sự trên Discord để nút bị disable
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass


async def _send_paged_components(
    target: commands.Context | discord.Interaction,
    pages: list[discord.ui.Container],
) -> None:
    """
    Gửi Components v2 phân trang.
    - prefix  : LayoutView + ctx.send(view=lv)  — không cần flags=
    - slash   : followup.send(components=[...], flags=_cv2_flags)
    Buttons được nhúng vào ActionRow bên trong Container (View thường pattern).
    """
    is_interaction = isinstance(target, discord.Interaction)

    if is_interaction:
        # Slash / interaction: dùng components= + flags=
        if len(pages) == 1:
            await target.followup.send(components=[pages[0]], flags=_cv2_flags)
        else:
            view = PageViewV2(pages, author_id=target.user.id)
            await target.followup.send(
                components=[view.build_container()],
                flags=_cv2_flags,
            )
    else:
        # Prefix: LayoutView + ctx.send(view=lv)
        if len(pages) == 1:
            lv = discord.ui.LayoutView()
            lv.add_item(pages[0])
            await target.send(view=lv)
        else:
            view = PageViewV2(pages, author_id=target.author.id)
            lv = discord.ui.LayoutView()
            lv.add_item(view.build_container())
            view.message = await target.send(view=lv)


class PageViewV2(discord.ui.View):
    """
    View phân trang cho Components v2.
    Buttons nhúng vào ActionRow bên trong Container (theo pattern View thường).
    """

    def __init__(self, pages: list[discord.ui.Container], author_id: int, page: int = 0):
        super().__init__(timeout=60)
        self.pages     = pages
        self.page      = max(0, min(page, len(pages) - 1))
        self.author_id = author_id
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= len(self.pages) - 1)

    def build_container(self) -> discord.ui.Container:
        """Trả về Container của trang hiện tại, nhúng buttons vào cuối."""
        base = self.pages[self.page]
        # Lấy children của container gốc rồi thêm ActionRow buttons
        children = list(base.children) + [discord.ui.ActionRow(*self.children)]
        return discord.ui.Container(*children, accent_color=discord.Color(0x5865F2))

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Không phải inventory của bạn.", ephemeral=True
            )
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            components=[self.build_container()],
            flags=_cv2_flags,
        )

    @discord.ui.button(label="Tiếp ▶", style=discord.ButtonStyle.primary)
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Không phải inventory của bạn.", ephemeral=True
            )
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            components=[self.build_container()],
            flags=_cv2_flags,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass


# ═══════════════════════════════════════════════════════════
# COMPONENTS v2 — INVENTORY BUILDER
# ═══════════════════════════════════════════════════════════

_HEADER_TEXT = (
    "[?] `dtn sell all` - để bán tất cả item\n"
    "`dtn shop` - để xem các shop\n"
    "`dtn weapon` để xem vũ khí bản thân"
)

_ITEMS_PER_PAGE = 15   # số dòng item/crate tối đa mỗi trang


def _chunk_lines(lines: list[str], size: int) -> list[list[str]]:
    """Chia list thành chunks kích thước cố định."""
    return [lines[i : i + size] for i in range(0, max(len(lines), 1), size)]


def _build_inv_components(
    display_name: str,
    balance: int,
    user: dict,
) -> list[discord.ui.Container]:
    """
    Trả về list[discord.ui.Container] phân trang cho inventory.
    Nhận balance sẵn từ ngoài — tránh gọi DB bên trong builder.
    Buttons chưa nhúng ở đây — PageViewV2.build_container() sẽ thêm vào.
    """
    item_lines: list[str] = []
    crate_lines: list[str] = []

    for item_id, qty in user["inv"].items():
        if item_id.startswith("crate_"):
            cid    = item_id.split("_", 1)[1]
            crate  = CRATES.get(cid)
            emoji  = crate["emoji"]  if crate else "📦"
            name   = crate["name"]   if crate else item_id
            rarity = crate.get("rarity", "common") if crate else "common"
            tier   = _rarity_tier(rarity)
            crate_lines.append(
                f"{emoji} `crate_{cid}` **{name}** x{qty}  _{tier}_"
            )
            continue
        item = get_item_by_id(item_id)
        if item:
            tier = _rarity_tier(item["rarity"])
            item_lines.append(
                f"{item['emoji']} `{item_id}` **{item['name']}** x{qty}  _{tier}_"
            )

    # Flatten sections → list dòng có heading
    all_blocks: list[str] = []
    if item_lines:
        all_blocks.append("<:2851:1495250164116492469> **Vật phẩm**")
        all_blocks.extend(item_lines)
    if crate_lines:
        all_blocks.append("📦 **Crate**")
        all_blocks.extend(crate_lines)

    if not all_blocks:
        all_blocks = ["_Kho đồ trống._"]

    page_chunks = _chunk_lines(all_blocks, _ITEMS_PER_PAGE)
    total = len(page_chunks)

    containers: list[discord.ui.Container] = []
    for page_idx, chunk in enumerate(page_chunks):
        page_tag = f" • Trang {page_idx + 1}/{total}" if total > 1 else ""

        container = discord.ui.Container(
            discord.ui.TextDisplay(_HEADER_TEXT),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"## <:Backpack:1495462021377032202> Kho đồ của **{display_name}**{page_tag}\n"
                f"-# {COIN_EMOJI} Số dư: **{balance:,}**"
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("\n".join(chunk)),
            accent_color=discord.Color(0x5865F2),
        )
        containers.append(container)

    return containers


# ═══════════════════════════════════════════════════════════
# COG: INVENTORY
# ═══════════════════════════════════════════════════════════

class RPGInventory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── prefix: dtn inv ────────────────────────────────────

    @commands.command(name="inv")
    async def inv(self, ctx):
        uid        = str(ctx.author.id)
        data       = load_data(uid)
        user       = get_user(uid, data)
        balance    = get_balance(ctx.author.id)
        pages      = _build_inv_components(ctx.author.display_name, balance, user)
        await _send_paged_components(ctx, pages)

    # ─── slash: /inv ────────────────────────────────────────

    @app_commands.command(name="inv", description="Xem kho đồ (vật phẩm & crate)")
    async def slash_inv(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            uid     = str(interaction.user.id)
            data    = load_data(uid)
            user    = get_user(uid, data)
            balance = get_balance(interaction.user.id)
            pages   = _build_inv_components(interaction.user.display_name, balance, user)
            await _send_paged_components(interaction, pages)
        except Exception as e:
            await interaction.followup.send(
                f"<:X_:1495466670616219819> Có lỗi xảy ra khi tải inventory: `{e}`",
                ephemeral=True,
            )


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGInventory(bot))
