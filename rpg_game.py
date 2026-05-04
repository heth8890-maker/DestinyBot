"""
===== FILE: rpg_game.py =====
Discord Cog chính. Các cog đã tách ra file riêng:
  rpg_crate.py    → dtn crate
  rpg_question.py → dtn quest
  rpg_trade.py    → dtn trade / dtn add / dtn remove
  rpg_weapon.py   → dtn weapon (tất cả subcommand)
  rpg_item.py     → dtn item / dtn item use

COMMAND MAP
──────────────────────────────────────────────────────────
  dtn inv
  dtn sell item <id> [amount|all]
  dtn sell weapon <id>
  dtn sell all

  dtn shop crate
  dtn shop item
  dtn shop weapon                      — ★ weapon shop (reset 6h, 10 slot)
  dtn shopbuy <slot>                   — ★ mua weapon từ shop

  dtn hunt
  dtn hunt log [amount]
──────────────────────────────────────────────────────────

⚡ THAY ĐỔI v3 (Weapon Identity Layer):
  - sell weapon : dùng get_weapon_entity() — fix UID mismatch khi bán unique weapon
  - inv         : dùng WeaponID.is_unique() thay vì "-" not in (chuẩn hoá logic)
  - inv equipped: dùng entity.fmt_name() — thống nhất hiển thị với status/upgrade
  - Xoá duplicate _equipped_display (đã có trong rpg_weapon.py, import từ đó)
"""

import time
import random

import discord
from discord.ext import commands

from rpg_core import (
    get_item_by_id, get_weapon_by_id, get_crate_by_id,
    add_item, remove_item,
    add_weapon, remove_weapon_from_bag,
    roll_hunt_items, handle_egg,
    calc_sell_value, calc_hunt_cooldown, parse_effects,
    ITEMS, WEAPONS, CRATES, RARITY_COLOR, RARITY_LABEL,
    # ── v3: Weapon Identity Layer ─────────────────────────
    WeaponID, get_weapon_entity,
    get_user_lock,
    # ── Agent3: base_id resolver (Ghost Inventory fix) ────
    get_base_id,
)
from rpg_database import get_user, save_user
from rpg_weapon import (
    RARE_CRATE_WEAPONS,
    DARK_CRATE_WEAPON,
    _rarity_tier,
    COIN_EMOJI as _W_COIN,
    ERR as _W_ERR,
    OK  as _W_OK,
)
from rpg_addon import (
    load_weapon_shop,
    seconds_to_shop_reset,
    get_shop_slot,
    fmt_effect_val,
    mark_shop_slot_sold,
)
from rpg_quest import add_quest_progress
from cash import update_balance_safe, get_balance

# ── Cosmetic ───────────────────────────────────────────────
COIN_EMOJI  = "<:Coin:1495831576397742241>"
SKULL_EMOJI = "<:2859:1495250145942704189>"
SWORD_EMOJI = "<:2918:1495252941492457502>"
HUNT_CD_SEC = 16

ERR = "<:X_:1495466670616219819>"
OK  = "<:Tick:1495466684520206528>"


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
    Nút ⬅️ ➡️ — tự disable ở trang đầu/cuối. Timeout 60s.
    """

    def __init__(self, embeds: list[discord.Embed], page: int = 0):
        super().__init__(timeout=60)
        self.embeds = embeds
        self.page   = max(0, min(page, len(embeds) - 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page >= len(self.embeds) - 1)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.page], view=self
        )

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
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


async def _send_paged(
    ctx, embeds: list[discord.Embed]
) -> None:
    """Gửi 1 embed nếu chỉ có 1 trang, ngược lại gửi kèm PageView."""
    if len(embeds) == 1:
        await ctx.send(embed=embeds[0])
    else:
        await ctx.send(embed=embeds[0], view=PageView(embeds))


# ═══════════════════════════════════════════════════════════
# COG: INVENTORY
# ═══════════════════════════════════════════════════════════

class RPGInventory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="inv")
    async def inv(self, ctx):
        uid  = str(ctx.author.id)
        user, upgraded_weapons = get_user(uid)

        item_lines, crate_lines, weapon_lines, upgraded_lines = [], [], [], []

        # ── Items & Crates ────────────────────────────────────────────────────
        for item_id, qty in user["inv"].items():
            if item_id.startswith("crate_"):
                cid   = item_id.split("_", 1)[1]
                crate = CRATES.get(cid)
                emoji = crate["emoji"] if crate else "📦"
                name  = crate["name"]  if crate else item_id
                crate_lines.append(f"{emoji} {name} x{qty}")
                continue
            item = get_item_by_id(item_id)
            if item:
                tier = _rarity_tier(item["rarity"])
                item_lines.append(
                    f"{item['emoji']} `{item_id}` **{item['name']}** x{qty}  _{tier}_"
                )

        # ── Regular weapons — dùng WeaponID.is_unique() thay vì "-" not in ────
        # FIX: also collect UID weapons that have no upgraded_weapons entry.
        # Under v1.7 a UID without an upgrade record is valid, but the old code
        # skipped them in both the regular-weapon block (is_unique check) AND the
        # upgraded-weapon block (no entry in upgraded_weapons) → they were invisible.
        # upgraded_weapons is now a separate dict {uid: {...}} from get_user()
        uw_uids: set[str] = {uw["uid"] for uw in upgraded_weapons}

        weapon_counts:    dict[str, int] = {}
        uid_no_upgrade:   list[str]      = []   # UIDs with no upgrade entry yet

        for wid in user.get("weapons", []):
            if not WeaponID.is_unique(str(wid)):
                weapon_counts[wid] = weapon_counts.get(wid, 0) + 1
            elif wid not in uw_uids:
                uid_no_upgrade.append(wid)   # UID but not yet upgraded

        for wid, cnt in weapon_counts.items():
            # Dùng get_weapon_entity() — single entry point
            entity = get_weapon_entity(user, wid)
            if entity:
                weapon_lines.append(
                    f"{entity.base_data['emoji']} `{wid}` **{entity.base_data['name']}** x{cnt}"
                )

        # Render UID weapons that have no upgrade entry yet (FIX: were invisible before)
        for wid in uid_no_upgrade:
            entity = get_weapon_entity(user, wid)
            if entity:
                weapon_lines.append(
                    f"{entity.base_data['emoji']} `{wid}` **{entity.base_data['name']}** _(chưa nâng cấp)_"
                )

        # ── Upgraded weapons — dùng entity.fmt_name() → thống nhất với status ─
        # TODO: get_weapon_entity() vẫn đọc user["upgraded_weapons"] (list cũ từ rpg_core).
        # fmt_name() có thể không hiển thị upgrade decorators cho đến khi rpg_weapon.py
        # được refactor sang MongoDB pattern. max_lv ở đây đọc trực tiếp từ upgraded_weapons
        # dict nên vẫn đúng.
        for uw_data in upgraded_weapons:
            uid_key = uw_data["uid"]
            entity = get_weapon_entity(user, uid_key)
            if entity:
                effect_levels = uw_data.get("effect_levels", {})
                max_lv = max(effect_levels.values()) if effect_levels else 1
                upgraded_lines.append(
                    f"<:Effect:1495466103047061679> `{uid_key}` "
                    f"{entity.fmt_name()} _(max lv{max_lv}/30)_"
                )

        # ── Equipped — ALWAYS resolve base_id first (Ghost Inventory fix) ─────
        # NEVER look up `wid` directly in the weapon database.
        # UIDs like "467-A1B2C" are not database keys → returns None
        # → missing emoji/name → "Ghost" slot in the embed.

        # ── Build paginated fields (logic cũ giữ nguyên, chỉ wrap vào phân trang) ──
        fields: list[tuple] = []
        fields += _split_field(
            "<:2851:1495250164116492469> Vật phẩm", item_lines
        )
        if crate_lines:
            fields += _split_field("📦 Crate", crate_lines)
        fields += _split_field(
            "<:Hamer:1495462570469888069> Kho vũ khí", weapon_lines
        )
        if upgraded_lines:
            fields += _split_field(
                "<:Info:1496098636247863491> Weapon Nâng Cấp", upgraded_lines
            )

        title  = f"<:Backpack:1495462021377032202> Kho đồ của {ctx.author.display_name}"
        footer = f"Số dư: {get_balance(ctx.author.id):,} {COIN_EMOJI}"
        embeds = _paginate_fields(fields, title=title, color=0x5865F2, footer=footer)
        await _send_paged(ctx, embeds)


# ═══════════════════════════════════════════════════════════
# SELL WEAPON — MODULE-LEVEL HELPER
# ═══════════════════════════════════════════════════════════

def _resolve_sell_weapon_targets(
    user: dict,
    weapon_arg: str,
    amount: int,
) -> tuple[list[str], str | None]:
    """
    Identify which weapon bag entries to sell.

    Returns (targets, None) on success.
    Returns ([], error_message) on any validation failure.

    Priority
    ────────
    Pass 1 — exact UID match  : weapon_arg literally in weapons list
                                → amount must be 1
    Pass 2 — base-ID match    : get_base_id(entry) == get_base_id(weapon_arg)
                                → collect up to <amount> non-equipped copies

    Equipped check
    ──────────────
    Done per bag entry via `entry not in equipped`.
    equipped stores the EXACT string that was slotted (UID or base_id).
    Two entries sharing a base_id are evaluated independently:
    only the one whose exact string is in equipped is blocked.
    """
    if amount <= 0:
        return [], "Số lượng phải lớn hơn 0."

    # Both are list[str] per existing data contract
    weapons:  list[str] = user.get("weapons", [])
    equipped: list[str] = user.get("equipped", [])

    # ── Pass 1: weapon_arg là UID thật sự → dùng WeaponID.is_unique() ────────
    # KHÔNG dùng `weapon_arg in weapons` để phân nhánh — base ID cũng có thể
    # nằm trong weapons list và sẽ bị nhầm sang UID path, gây lỗi khi bán nhiều.
    if WeaponID.is_unique(weapon_arg):
        if weapon_arg not in weapons:
            return [], f"Không có vũ khí `{weapon_arg}` trong kho."
        if weapon_arg in equipped:
            return [], (
                f"Vũ khí `{weapon_arg}` đang được trang bị — "
                f"hãy bỏ trang bị trước khi bán."
            )
        if amount != 1:
            return [], (
                "Khi bán bằng UID chỉ có thể bán 1 cái. "
                "Dùng base ID nếu muốn bán nhiều."
            )
        return [weapon_arg], None

    # ── Pass 2: weapon_arg là base ID → stackable path ───────────────────────
    target_base = get_base_id(weapon_arg)

    # Iterate a snapshot so removal elsewhere never affects this scan
    candidates: list[str] = []
    for entry in list(weapons):
        if get_base_id(entry) == target_base and entry not in equipped:
            candidates.append(entry)

    if not candidates:
        # Distinguish "never existed" vs "all copies are equipped"
        all_copies = [w for w in weapons if get_base_id(w) == target_base]
        if all_copies:
            return [], (
                f"Tất cả bản sao của vũ khí `{weapon_arg}` đang được trang bị — "
                f"hãy bỏ trang bị trước khi bán."
            )
        return [], f"Không có vũ khí `{weapon_arg}` trong kho."

    if amount > len(candidates):
        return [], (
            f"Bạn chỉ có **{len(candidates)}** bản sao có thể bán "
            f"(không tính bản đang trang bị), "
            f"nhưng yêu cầu bán **{amount}**."
        )

    # Slice exactly <amount> entries from the front of the candidate list
    return candidates[:amount], None


# ═══════════════════════════════════════════════════════════
# COG: SELL
# ═══════════════════════════════════════════════════════════

class RPGSell(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="sell", invoke_without_command=True)
    async def sell(self, ctx):
        await ctx.send(
            f"{COIN_EMOJI} **Cách dùng lệnh sell:**\n"
            f"• `dtn sell item <id> [amount|all]` — bán vật phẩm\n"
            f"• `dtn sell weapon <id>` — bán vũ khí trong kho\n"
            f"• `dtn sell all` — bán toàn bộ vật phẩm"
        )

    @sell.command(name="item")
    async def sell_item(self, ctx, item_id: str, amount: str = "1"):
        uid  = str(ctx.author.id)
        user, upgraded_weapons = get_user(uid)

        if item_id not in user["inv"]:
            return await ctx.send(f"{ERR} | Bạn không có vật phẩm này.")

        item = get_item_by_id(item_id)
        if not item:
            return await ctx.send(f"{ERR} | Item không tồn tại.")

        owned = user["inv"][item_id]
        if amount.lower() == "all":
            qty = owned
        else:
            try:
                qty = int(amount)
            except ValueError:
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")

        if qty <= 0 or qty > owned:
            return await ctx.send(f"{ERR} | Bạn chỉ có {owned} cái, không đủ.")

        effects = parse_effects(user.get("equipped", []), user)
        total   = calc_sell_value(item, qty, effects)

        if not remove_item(user, item_id, qty):
            return await ctx.send(f"{ERR} | Không thể bán.")

        if not save_user(uid, user, upgraded_weapons):
            return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")
        await update_balance_safe(ctx.author.id, total)
        add_quest_progress(ctx.author.id, "items_sold", qty)

        await ctx.send(
            f"{COIN_EMOJI} | Đã bán **{qty}x** {item['emoji']} {item['name']} "
            f"→ nhận **{total:,}** {COIN_EMOJI}"
        )

    @sell.command(name="weapon")
    async def sell_weapon(self, ctx, weapon_arg: str, amount: str = "1"):
        """
        Bán vũ khí trong kho.

        Syntax
        ──────
          dtn sell weapon <uid>              — bán đúng instance (amount = 1)
          dtn sell weapon <base_id>          — bán 1 bản sao
          dtn sell weapon <base_id> <amount> — bán nhiều bản sao cùng lúc

        Equipped safety
        ───────────────
          Chỉ block bán nếu ĐÚNG INSTANCE đó đang được trang bị.
          Bản sao khác có cùng base_id vẫn bán được bình thường.
        """
        # ── 0. Parse & basic validate amount ──────────────────────────
        try:
            qty = int(amount)
        except ValueError:
            return await ctx.send(f"{ERR} | Số lượng không hợp lệ — phải là số nguyên.")

        uid = str(ctx.author.id)

        async with get_user_lock(uid):
            user, upgraded_weapons = get_user(uid)  # fresh read inside lock — prevents stale data

            # ── 1. Resolve targets (all validation happens here) ───────
            targets, err = _resolve_sell_weapon_targets(user, weapon_arg, qty)
            if err:
                return await ctx.send(f"{ERR} | {err}")

            # ── 2. Resolve display entity (representative = targets[0]) ─
            entity = get_weapon_entity(user, targets[0])
            if entity is None:
                # Item passes bag check but has no DB entry — corrupted state
                return await ctx.send(
                    f"{ERR} | Không tìm thấy dữ liệu vũ khí `{targets[0]}` "
                    f"trong database. Liên hệ admin nếu lỗi tiếp tục."
                )

            # ── 3. Calculate total price (per-item, not flat multiply) ──
            # get_price() may differ between upgraded UIDs and base copies,
            # so we resolve each target individually and sum.
            total_value = 0
            for t in targets:
                t_entity = get_weapon_entity(user, t)
                # Fallback to representative price if a target has no entity
                # (should not happen — targets were validated — but be safe)
                total_value += t_entity.get_price() if t_entity else entity.get_price()

            # ── 4. Remove each target from bag ─────────────────────────
            # Iterates `targets` (separate list) — never mutates weapons
            # mid-iteration unsafely. remove_weapon_from_bag handles the
            # actual list mutation internally.
            for t in targets:
                removed = remove_weapon_from_bag(user, t)
                if not removed:
                    # True unexpected state — bag was valid at step 1
                    return await ctx.send(
                        f"{ERR} | Lỗi nội bộ khi xoá vũ khí `{t}`. Thử lại."
                    )

                # Clean up upgrade record for unique weapons
                if WeaponID.is_unique(t):
                    user["upgraded_weapons"] = [
                        uw for uw in user["upgraded_weapons"] if uw.get("uid") != t
                    ]

            # ── 5. Persist → reward (order matters) ───────────────────
            if not save_user(uid, user, upgraded_weapons):   # sync, no await
                return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")
            await update_balance_safe(ctx.author.id, total_value)
            add_quest_progress(ctx.author.id, "weapons_sold", qty)

        # ── 6. Confirm to user (outside lock — no shared state access) ─
        qty_label = f"**{qty}x** " if qty > 1 else ""
        await ctx.send(
            f"{COIN_EMOJI} | Đã bán {qty_label}{entity.fmt_name()} "
            f"→ nhận **{total_value:,}** {COIN_EMOJI}"
        )

    @sell.command(name="all")
    async def sell_all(self, ctx):
        uid  = str(ctx.author.id)
        user, upgraded_weapons = get_user(uid)

        sell_ids = [k for k in user["inv"] if not k.startswith("crate_")]
        if not sell_ids:
            return await ctx.send(
                "<:Backpack:1495462021377032202> Không có vật phẩm nào để bán (crate không tính)."
            )

        effects     = parse_effects(user.get("equipped", []), user)
        grand_total = 0
        lines       = []
        total_qty   = 0

        for item_id in sell_ids:
            item = get_item_by_id(item_id)
            if not item:
                continue
            qty   = user["inv"][item_id]
            total = calc_sell_value(item, qty, effects)
            grand_total += total
            total_qty   += qty
            lines.append(f"{item['emoji']} {item['name']} x{qty} → **{total:,}** {COIN_EMOJI}")
            remove_item(user, item_id, qty)

        if not save_user(uid, user, upgraded_weapons):
            return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")
        await update_balance_safe(ctx.author.id, grand_total)
        add_quest_progress(ctx.author.id, "items_sold", total_qty)

        embed = discord.Embed(
            title="<:2245:1493575277605949480> | Bán tất cả vật phẩm",
            description="\n".join(lines),
            color=0xFFD700,
        )
        embed.set_footer(text=f"Tổng nhận: {grand_total:,} {COIN_EMOJI}")
        await ctx.send(embed=embed)


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
    await bot.add_cog(RPGInventory(bot))
    await bot.add_cog(RPGSell(bot))
    await bot.add_cog(RPGShop(bot))
    await bot.add_cog(RPGShopBuy(bot))
