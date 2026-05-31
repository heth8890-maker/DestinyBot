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
  dtn ebuy 006|007|008 [amount]       — mua Guaranteed Crate bằng Linh hoả

SLASH COMMANDS
──────────────────────────────────────────────────────────
  /shop crate
  /shop item
  /shop weapon
  /shop buy slot:<int>
  /shop event
  /ebuy item_id:<str> amount:<int>     — hỗ trợ: 004, 006, 007, 008
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
from rpg_core import get_user, load_data, save_data
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
)
from cash import update_balance_safe, get_balance


# Components v2 flag (bit 15 = 32768)
_cv2_flags = discord.MessageFlags()
_cv2_flags.value = 1 << 15

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
# Mỗi entry:
#   weapon_id  — nếu có: áp dụng logic ẩn (emoji + tên theo rarity)
#   hide_emoji — override WEAPON_HIDE_ICONS khi chưa sở hữu (bỏ trống = tự lookup)
#   label      — tên hiển thị (mask tự động nếu mythical/legendary)
#   emoji      — emoji thật khi đã sở hữu
#   chance     — chuỗi tỉ lệ, None nếu là dòng ghi chú
#   rarity     — None nếu không phải weapon
_CUSTOM_DROPS: dict[str, list[dict]] = {
    "004": [
        {"weapon_id": "5001", "label": "Tam Hoả Thống Soái", "emoji": "<a:4574:1499013628672610334>", "chance": "0.3",  "rarity": "special"},
        {"weapon_id": "5002", "label": "Hồn Giáp Bất Diệt",  "emoji": "<a:4572:1499013638319505530>", "chance": "0.3",  "rarity": "special"},
        {"weapon_id": "5003", "label": "Linh Diệm Sát Thần", "emoji": "<a:4573:1499013635555463198>", "chance": "0.3",  "rarity": "special"},
        {"label": "Linh Hoả (x4–18)",   "emoji": "<:Linh_hoa:1498614127386562601>", "chance": "35",   "rarity": None},
        {"label": "Coin (2,000–6,000)", "emoji": "<:Coin:1495831576397742241>",     "chance": "64.4", "rarity": None},
    ],
    "006": [
        {"weapon_id": "5002", "label": "Hồn Giáp Bất Diệt", "emoji": "<a:4572:1499013638319505530>", "chance": "100", "rarity": "special"},
    ],
    "007": [
        {"weapon_id": "5003", "label": "Linh Diệm Sát Thần", "emoji": "<a:4573:1499013635555463198>", "chance": "100", "rarity": "special"},
    ],
    "008": [
        {"weapon_id": "5001", "label": "Tam Hoả Thống Soái", "emoji": "<a:4574:1499013628672610334>", "chance": "100", "rarity": "special"},
    ],
    # Crate 009: 5610/5611/5612 có entry trong WEAPON_HIDE_ICONS nhưng icon đó thuộc code path
    # crate 005 (PARADISE_CRATE_WEAPONS). Crate 009 dùng hide_emoji="❓" để không dùng nhầm.
    "009": [
        {"weapon_id": "5610", "label": "Lôi thần Indra",          "emoji": "<a:5610:1505051859537104906>", "chance": "33.33", "rarity": "mythical"},
        {"weapon_id": "5611", "label": "Thần thời gian Chronus",  "emoji": "<a:5611:1505052271182872576>", "chance": "33.33", "rarity": "mythical"},
        {"weapon_id": "5612", "label": "Surtr bản ngã hoàng kim", "emoji": "<a:5612:1505052278753595402>", "chance": "33.34", "rarity": "mythical"},
        {"label": "_Không thể mua trực tiếp — chỉ drop từ Crate of Paradise (005)_", "emoji": "⚠️", "chance": None, "rarity": None},
    ],
}

CRATE_PAGE_SIZE = 2   # số crate hiển thị mỗi trang (cho crate thường)

# ★ Bảng map weapon_id → icon ẩn (chưa từng sở hữu)
WEAPON_HIDE_ICONS: dict[str, str] = {
    "463":  "<:463_hide:1507357616475607182>",
    "465":  "<:465_hide:1507357618744594534>",
    "467":  "<:467_hide:1507357623656382484>",
    "464":  "<:464_hide:1507357626978275398>",
    "3708": "<:3708_hide:1507357638776586530>",
    "3695": "<:3695_hide:1507357643168284714>",
    "4510": "<:4510_hide:1507357647333101568>",
    "5594": "<:5594_hide:1507357650604789811>",
    "5591": "<:5591_hide:1507357664571822100>",
    "5593": "<:5593_hide:1507357666756788364>",
    "4511": "<:4511_hide:1507357676517064774>",
    "466":  "<:466_hide:1507357680233353298>",
    "5001": "<:5001_hide:1507357683106451596>",
    "5003": "<:5003_hide:1507357685748596786>",
    "4541": "<:4541_hide:1507357689213096138>",
    "3696": "<:3696_hide:1507357691469889587>",
    "3706": "<:3706_hide:1507358640430907422>",
    "3697": "<:3697_hide:1507358642981179493>",
    "5610": "<:5610_hide:1507358649830473799>",
    "5612": "<:5612_hide:1507358652812754985>",
    "4509": "<:4509_hide:1507358656340033756>",
    "5611": "<:5611_hide:1507358661549232198>",
    "5595": "<:5595_hide:1507358665428963408>",
    "5002": "<:5002_hide:1507385317278355476>",
    "4518": "<:4518_hide:1507385320084344973>",
    "4529": "<:4529_hide:1507385328229416980>",
}


def _render_custom_entry(entry: dict, user_weapons: set[str] | None) -> str:
    """
    Render 1 dòng drop trong _CUSTOM_DROPS.
    - Weapon entry (có weapon_id):
        + Đã sở hữu → emoji thật + tên thật
        + Chưa sở hữu → hide_emoji (nếu có trong entry) hoặc WEAPON_HIDE_ICONS lookup;
          tên bị mask nếu mythical/legendary, giữ nguyên nếu special.
    - Non-weapon entry (rarity=None) → render thẳng, không mask.
    """
    weapon_id = entry.get("weapon_id")
    rarity    = entry.get("rarity")
    chance    = entry.get("chance")

    if weapon_id:
        owned = user_weapons is not None and weapon_id in user_weapons
        if owned:
            emoji = entry["emoji"]
            label = entry["label"]
        else:
            # hide_emoji trong entry override WEAPON_HIDE_ICONS (dùng cho crate 009)
            # Không có hide_emoji → lookup WEAPON_HIDE_ICONS → fallback ❓
            if "hide_emoji" in entry:
                emoji = entry["hide_emoji"]
            else:
                emoji = WEAPON_HIDE_ICONS.get(weapon_id, "❓")
            if rarity == "mythical":
                label = "?????"
            elif rarity == "legendary":
                label = "???"
            else:
                label = entry["label"]   # special: giữ tên
    else:
        emoji = entry["emoji"]
        label = entry["label"]

    rarity_tag = f"  _{_rarity_tier(rarity)}_" if rarity else ""
    chance_str = f" — {chance}%" if chance else ""
    return f"  {emoji} **{label}**{chance_str}{rarity_tag}"


def _masked_name(w: dict) -> str:
    """Trả về tên weapon hoặc ẩn nếu là legendary/mythical."""
    rarity = w.get("rarity", "")
    if rarity == "mythical":
        return "?????"
    if rarity == "legendary":
        return "???"
    return _SHORT.get(w["name"], w["name"])

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


def _build_crate_page_container(
    page: int,
    user_weapons: set[str] | None = None,
) -> discord.ui.Container:
    """
    Tạo Container (Components v2) cho trang <page> của shop crate (0-indexed).
    - user_weapons: set các base_id đã từng sở hữu. None = ẩn tất cả hide icon.
    - Tên weapon legendary/mythical luôn bị ẩn thành ???/?????.
    - Buttons được đặt ở ActionRow top-level trong LayoutView (không nhúng trong container).
    """
    pages       = _build_crate_pages()
    total_pages = len(pages)
    page        = max(0, min(page, total_pages - 1))
    page_items  = pages[page]

    children: list = []

    # ── Header ──────────────────────────────────────────────
    children.append(discord.ui.TextDisplay(
        content=(
            "# <:Shop:1495464183037165763> Shop Crate\n"
            "Mua crate để nhận weapon ngẫu nhiên!\n"
            "Trang bị weapon giúp: tăng ô hunt, giảm cooldown, "
            "tăng % giá bán, tăng tỉ lệ rare.\n\n"
            f"PAGE **{page + 1}** / **{total_pages}**"
        )
    ))
    children.append(discord.ui.Separator(visible=True))

    # ── Từng crate trên trang ────────────────────────────────
    for idx, (crate_id, crate) in enumerate(page_items):
        # Tên crate + giá
        price_text = (
            "**Không bán** _(drop từ Crate of Paradise 005)_"
            if crate_id == "009"
            else f"**{crate['price']:,}** {COIN_EMOJI}"
        )
        children.append(discord.ui.TextDisplay(
            content=(
                f"### {crate['emoji']} {crate['name']}  |  ID: `{crate_id}`\n"
                f"<:2245:1493575277605949480> Giá: {price_text}\n\n"
                f"**Drop rate:**"
            )
        ))
        children.append(discord.ui.Separator(
            visible=False,
            spacing=discord.SeparatorSpacing.small,
        ))

        if crate_id in _CUSTOM_DROPS:
            entries = _CUSTOM_DROPS[crate_id]
            for e_idx, entry in enumerate(entries):
                children.append(discord.ui.TextDisplay(
                    content=_render_custom_entry(entry, user_weapons)
                ))
                if e_idx < len(entries) - 1:
                    children.append(discord.ui.Separator(
                        visible=False,
                        spacing=discord.SeparatorSpacing.small,
                    ))
        else:
            pool = _CRATE_POOL.get(crate_id, WEAPONS)
            if len(pool) == 1:
                w         = pool[0]
                weapon_id = w["id"]
                if user_weapons is not None and weapon_id in user_weapons:
                    emoji = w["emoji"]
                    name  = _SHORT.get(w["name"], w["name"])
                else:
                    emoji = WEAPON_HIDE_ICONS.get(weapon_id, w["emoji"])
                    name  = _masked_name(w)
                children.append(discord.ui.TextDisplay(
                    content=f"{emoji} **{name}** — {w['chance']}%  _{_rarity_tier(w['rarity'])}_"
                ))
            else:
                for w_idx, w in enumerate(pool):
                    weapon_id = w["id"]
                    if user_weapons is not None and weapon_id in user_weapons:
                        emoji = w["emoji"]
                        name  = _SHORT.get(w["name"], w["name"])
                    else:
                        emoji = WEAPON_HIDE_ICONS.get(weapon_id, w["emoji"])
                        name  = _masked_name(w)
                    children.append(discord.ui.TextDisplay(
                        content=(
                            f"  {emoji} **{name}** "
                            f"— {w['chance']}%  _{_rarity_tier(w['rarity'])}_"
                        )
                    ))
                    # Separator nhỏ giữa các weapon (trừ weapon cuối)
                    if w_idx < len(pool) - 1:
                        children.append(discord.ui.Separator(
                            visible=False,
                            spacing=discord.SeparatorSpacing.small,
                        ))

        # Separator ngang giữa các crate (trừ crate cuối trên trang)
        if idx < len(page_items) - 1:
            children.append(discord.ui.Separator(visible=True))

    # ── Footer ──────────────────────────────────────────────
    children.append(discord.ui.Separator(visible=True))
    children.append(discord.ui.TextDisplay(
        content="-# dtn crate buy <id> [amount]  |  dtn crate open <id>"
    ))

    return discord.ui.Container(*children, accent_color=discord.Color(0x9B59B6))


class CrateShopView(discord.ui.LayoutView):
    """
    LayoutView phân trang cho shop crate (Components v2).
    Layout nút: [◀ Trước]  [page/total (disabled)]  [Tiếp ▶]
    Timeout 60s. Chỉ author gốc mới được bấm nút.

    LayoutView: container + buttons là ActionRow top-level (không nhúng trong Container).
    Gửi bằng send(view=self) — không cần flags=.
    """

    def __init__(
        self,
        page: int = 0,
        author_id: int | None = None,
        user_weapons: set[str] | None = None,
    ):
        super().__init__(timeout=60)
        self.page         = max(0, min(page, _total_crate_pages() - 1))
        self.author_id    = author_id
        self.user_weapons = user_weapons
        self._disabled    = False
        self.message: discord.Message | None = None
        self._build()

    def _build(self) -> None:
        """Rebuild toàn bộ view: container + ActionRow buttons."""
        self.clear_items()
        total = _total_crate_pages()

        # ── Container (không có button bên trong) ───────────
        container = _build_crate_page_container(self.page, user_weapons=self.user_weapons)
        self.add_item(container)

        # ── ActionRow top-level ──────────────────────────────
        btn_prev = discord.ui.Button(
            label="◀ Trước",
            style=discord.ButtonStyle.secondary,
            disabled=self._disabled or (self.page == 0),
        )
        btn_page = discord.ui.Button(
            label=f"{self.page + 1} / {total}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        btn_next = discord.ui.Button(
            label="Tiếp ▶",
            style=discord.ButtonStyle.primary,
            disabled=self._disabled or (self.page >= total - 1),
        )

        async def _prev(interaction: discord.Interaction) -> None:
            if self.author_id and interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "❌ Chỉ người dùng lệnh mới được bấm nút này.", ephemeral=True
                )
                return
            self.page -= 1
            self._build()
            await interaction.response.edit_message(view=self)

        async def _next(interaction: discord.Interaction) -> None:
            if self.author_id and interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "❌ Chỉ người dùng lệnh mới được bấm nút này.", ephemeral=True
                )
                return
            self.page += 1
            self._build()
            await interaction.response.edit_message(view=self)

        btn_prev.callback = _prev
        btn_next.callback = _next

        ar = discord.ui.ActionRow()
        ar.add_item(btn_prev)
        ar.add_item(btn_page)
        ar.add_item(btn_next)
        self.add_item(ar)

    async def on_timeout(self) -> None:
        self._disabled = True
        self._build()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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


async def _do_shop_crate(
    send_fn,
    author_id: int | None = None,
    interaction: discord.Interaction | None = None,
):
    """Gửi shop crate dùng Components v2 (LayoutView).
    - Cả prefix lẫn slash đều dùng send(view=view) — không cần flags=.
    - Prefix: send_fn = ctx.send         → trả về Message → lưu vào view.message
    - Slash:  send_fn = interaction.response.send_message → lấy qua original_response()
    """
    uid = (
        str(interaction.user.id) if interaction is not None
        else (str(author_id) if author_id else None)
    )
    user_weapons: set[str] | None = None
    if uid:
        data = load_data(uid)
        user_data = get_user(uid, data)
        # seen_weapons: base_ids user đã từng nhận (populate bởi add_weapon)
        # Fallback: derive từ weapons list cho user cũ chưa có seen_weapons
        # weapons có thể chứa base_id thuần ("5610") hoặc UID ("5610-ABC12")
        seen = set(user_data.get("seen_weapons", []))
        for w in user_data.get("weapons", []):
            if isinstance(w, str):
                seen.add(w.split("-")[0])
        user_weapons = seen

    view = CrateShopView(page=0, author_id=author_id, user_weapons=user_weapons)
    msg  = await send_fn(view=view)

    # Lưu message để on_timeout có thể edit disable nút
    if isinstance(msg, discord.Message):
        view.message = msg
    elif interaction is not None:
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass


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


def _build_weapon_shop_container() -> discord.ui.Container:
    """
    Tạo Container (Components v2) cho weapon shop — 1 trang duy nhất, không có effect.
    Separator ngang chia đầu/cuối, Separator nhỏ giữa các slot.
    """
    shop      = load_weapon_shop()
    remaining = seconds_to_shop_reset()
    h, m      = divmod(remaining // 60, 60)

    children: list = []

    # ── Header ────────────────────────────────────────────────
    children.append(discord.ui.TextDisplay(
        content=(
            "# <:Hamer:1495462570469888069> Weapon Shop\n"
            f"⏳ Reset sau **{h}h {m}m**  •  "
            "Dùng `dtn shop buy <slot>` hoặc `/shop buy` để mua."
        )
    ))
    children.append(discord.ui.Separator(visible=True))

    # ── Danh sách slot ────────────────────────────────────────
    slots = shop.get("slots", [])
    for i, s in enumerate(slots):
        w             = get_weapon_by_id(s["weapon_id"])
        current_emoji = w.get("emoji", s["emoji"]) if w else s["emoji"]
        rarity_e      = _rarity_tier(s["rarity"])

        children.append(discord.ui.TextDisplay(
            content=(
                f"`[{s['slot']:02d}]` {current_emoji} **{s['name']}** — {rarity_e}\n"
                f"<:2245:1493575277605949480> **{s['price']:,}** {COIN_EMOJI}"
                f"  | Drop rate: **{s['drop_rate']}%**"
                f"  |  ID: `{s['weapon_id']}`"
            )
        ))
        if i < len(slots) - 1:
            children.append(discord.ui.Separator(
                visible=False,
                spacing=discord.SeparatorSpacing.small,
            ))

    # ── Footer ────────────────────────────────────────────────
    children.append(discord.ui.Separator(visible=True))
    children.append(discord.ui.TextDisplay(
        content="-# dtn shop buy <slot>  |  /shop buy slot:<số>"
    ))

    return discord.ui.Container(*children, accent_color=discord.Color(0x3498DB))


# ★ Crate event bán bằng Linh hoả — thêm/bớt crate: sửa dict này
_EVENT_SHOP: dict[str, int] = {
    "004": 25,     # Soul Crate
    "006": 1200,   # Hồn Giáp Bất Diệt Crate
    "007": 1200,   # Linh Diệm Sát Thần Crate
    "008": 1200,   # Tam Hoả Thống Soái Crate
}


def _build_event_embed() -> discord.Embed:
    embed = discord.Embed(
        title="<:Shop:1495464183037165763> Shop Event",
        description=(
            "Dùng <:Linh_hoa:1498614127386562601> Linh hoả để đổi Soul Crate hiếm!\n"
            "Crate 006/007/008 đảm bảo **100%** vũ khí special tương ứng."
        ),
        color=0xCCFFCC,
    )
    for crate_id, cost in _EVENT_SHOP.items():
        crate = CRATES.get(crate_id)
        if not crate:
            continue
        embed.add_field(
            name=f"{crate['emoji']} | {crate['name']} (ID: {crate_id})",
            value=(
                f"**Giá:** {cost}x <:Linh_hoa:1498614127386562601> Linh hoả\n"
                f"**Lệnh mua:** `dtn ebuy {crate_id} [số lượng]`  hoặc  `/ebuy item_id:{crate_id}`"
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
        embeds = _build_shop_item_embeds()
        if not embeds:
            await ctx.send("Không có dữ liệu item.")
            return
        for e in embeds:
            await ctx.send(embed=e)

    # ─── WEAPON SHOP ───
    @shop.command(name="weapon")
    async def shop_weapon(self, ctx):
        """Xem 10 weapon đang bán (reset mỗi 6 tiếng)."""
        lv = discord.ui.LayoutView()
        lv.add_item(_build_weapon_shop_container())
        await ctx.send(view=lv)

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
        await _do_shop_crate(
            interaction.response.send_message,
            author_id=interaction.user.id,
            interaction=interaction,
        )

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
        await interaction.response.send_message(
            components=[_build_weapon_shop_container()],
            flags=_cv2_flags,
        )

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
    data = load_data(uid)
    user = get_user(uid, data)

    # ── ALWAYS stackable: shop must never produce a UID ──────────────────
    add_weapon(user, slot_data["weapon_id"], make_unique=False)
    data[uid] = user
    if not await save_data(data, uid):
        return await send_fn(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")
    await update_balance_safe(member.id, -price)
    mark_shop_slot_sold(slot)

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
        """Mua crate event bằng Linh hoả. `dtn ebuy <004|006|007|008> [số lượng]`"""
        await _handle_event_buy(ctx.author, item_id, amount, ctx.send)

    # ─── SLASH ───
    @app_commands.command(name="ebuy", description="Mua Soul Crate bằng Linh hoả")
    @app_commands.guild_only()
    @app_commands.describe(
        item_id="ID vật phẩm (004 · 006 · 007 · 008)",
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
            app_commands.Choice(name="Soul Crate (004)",                  value="004"),
            app_commands.Choice(name="Hồn Giáp Bất Diệt Crate (006)",    value="006"),
            app_commands.Choice(name="Linh Diệm Sát Thần Crate (007)",   value="007"),
            app_commands.Choice(name="Tam Hoả Thống Soái Crate (008)",   value="008"),
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
    if not item_id or item_id not in _EVENT_SHOP:
        valid_ids = ", ".join(f"`{k}`" for k in _EVENT_SHOP)
        return await send_fn(
            f"{ERR} | ID vật phẩm không đúng. ID hợp lệ: {valid_ids}"
        )
    if amount <= 0:
        return await send_fn(f"{ERR} | Số lượng không hợp lệ.")

    uid           = str(member.id)
    currency_id   = "5200"          # Linh hoả
    crate_id      = item_id
    cost_per_unit = _EVENT_SHOP[crate_id]
    total_cost    = cost_per_unit * amount

    # ── Tải user từ rpg_core (unified data layer) ──
    data = load_data(uid)
    user = get_user(uid, data)

    user_inv = user.get("inv", {})
    if user_inv.get(currency_id, 0) < total_cost:
        missing = total_cost - user_inv.get(currency_id, 0)
        return await send_fn(
            f"{ERR} | Bạn thiếu **{missing}** "
            f"<:Linh_hoa:1498614127386562601> Linh hoả (ID: 5200) "
            f"để thực hiện giao dịch này."
        )

    # ── Trừ Linh hoả, thêm crate ──
    user["inv"][currency_id] -= total_cost
    add_item(user, f"crate_{crate_id}", amount)

    # ── Lưu lên MongoDB qua rpg_core ──
    data[uid] = user
    if not await save_data(data, uid):
        return await send_fn(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

    crate       = CRATES.get(crate_id, {})
    crate_emoji = crate.get("emoji", "📦")
    crate_name  = crate.get("name", f"Crate {crate_id}")
    await send_fn(
        f"{OK} | Chúc mừng bạn đã đổi thành công **{total_cost}** "
        f"<:Linh_hoa:1498614127386562601> Linh hoả "
        f"lấy **{amount}x** {crate_emoji} **{crate_name}**!"
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
