"""
===== FILE: rpg_game.py =====
Discord Cog chính. Các cog đã tách ra file riêng:
  rpg_crate.py    → dtn crate
  rpg_question.py → dtn quest
  rpg_trade.py    → dtn trade / dtn add / dtn remove
  .py   → dtn weapon (tất cả subcommand)
  rpg_item.py     → dtn item / dtn item use
  rpg_shop.py     → dtn shop / dtn shopbuy   ← TÁCH RIÊNG

COMMAND MAP
──────────────────────────────────────────────────────────
  dtn inv
  dtn sell item <id> [amount|all]
  dtn sell weapon <id>
  dtn sell all
──────────────────────────────────────────────────────────

⚡ THAY ĐỔI v3 (Weapon Identity Layer):
  - sell weapon : dùng get_weapon_entity() — fix UID mismatch khi bán unique weapon
  - inv         : dùng WeaponID.is_unique() thay vì "-" not in (chuẩn hoá logic)
  - inv equipped: dùng entity.fmt_name() — thống nhất hiển thị với status/upgrade
  - Xoá duplicate _equipped_display (đã có trong rpg_weapo.py, import từ đó)
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
from rpg_weapon_data import (
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
        # fmt_name() có thể không hiển thị upgrade decorators cho đến khi rpg_weapn.py
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

    @sell.command(name="weapon", aliases=["w"])
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
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGInventory(bot))
    await bot.add_cog(RPGSell(bot))
