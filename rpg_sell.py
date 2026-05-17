"""
===== FILE: rpg_sell.py =====
Tách từ rpg_game.py — toàn bộ luồng sell (item, weapon, all).

PREFIX COMMANDS
───────────────
  dtn sell                              → hiển thị help
  dtn sell help                         → help chi tiết
  dtn sell item <id> [amount|all]       → bán item cụ thể
  dtn sell all                          → bán tất cả item (không tính crate)
  dtn sell weapon <uid|base_id> [qty]   → bán weapon theo UID hoặc base_id
  dtn sell rarity <rarity> [qty|all]    → bán weapon theo rarity (bulk, confirm UI)

SLASH COMMANDS
──────────────
  /sell item   <id> [amount]            → bán item
  /sell weapon <uid|base_id> [amount]   → bán weapon
  /sell rarity <rarity> [amount]        → bán weapon theo rarity (confirm UI)
  /sell all                             → bán tất cả item

NOTES
─────
  - sell weapon / sell rarity dùng get_weapon_entity() — fix UID mismatch
  - sell rarity hiển thị preview embed + confirm button trước khi thực hiện
  - Equipped weapons không bao giờ bị bán (kiểm tra per-entry)
  - Sau khi tách: rpg_game.py chỉ còn RPGInventory + _resolve_sell_weapon_targets
    (nếu cần giữ ở đó). Nạp file này qua bot.load_extension("rpg_sell").
"""

import random

import discord
from discord import app_commands
from discord.ext import commands

from rpg_core import (
    get_item_by_id,
    add_item, remove_item,
    parse_effects,
    calc_sell_value,
    RARITY_COLOR, RARITY_LABEL,
    WeaponID, get_weapon_entity,
    get_user_lock,
    get_base_id,
)
from rpg_database import get_user, save_user
from rpg_weapon_data import (
    COIN_EMOJI,
    ERR,
    OK,
    RARITY_LABEL as W_RARITY_LABEL,
    RARITY_COLOR as W_RARITY_COLOR,
    parse_rarity_alias,
)
from rpg_addon import fmt_effect_val
from rpg_quest import add_quest_progress
from cash import update_balance_safe, get_balance

# ── Cosmetic ───────────────────────────────────────────────
_COIN = COIN_EMOJI
_BACKPACK = "<:Backpack:1495462021377032202>"
_SELL_ICON = "<:2245:1493575277605949480>"


# ═══════════════════════════════════════════════════════════
# INTERNAL: resolve sell-weapon targets
# ═══════════════════════════════════════════════════════════

def _resolve_sell_weapon_targets(
    user: dict,
    weapon_arg: str,
    amount: int,
) -> tuple[list[str], str | None]:
    """
    Xác định các bag-entry sẽ bán.

    Returns (targets, None)        — thành công
    Returns ([], error_message)    — lỗi validation

    Priority
    --------
    Pass 1 — UID thật sự  : WeaponID.is_unique() → amount phải = 1
    Pass 2 — base_id       : collect tối đa <amount> bản không equipped
    """
    if amount <= 0:
        return [], "Số lượng phải lớn hơn 0."

    weapons:  list[str] = user.get("weapons", [])
    equipped: list[str] = user.get("equipped", [])

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

    # Pass 2: base_id
    target_base = get_base_id(weapon_arg)
    candidates: list[str] = [
        entry for entry in list(weapons)
        if get_base_id(entry) == target_base and entry not in equipped
    ]

    if not candidates:
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

    return candidates[:amount], None


def _resolve_rarity_candidates(
    user: dict,
    rarity: str,
) -> list[dict]:
    """
    Trả về list[dict] mô tả weapon theo rarity có thể bán.
    Mỗi dict: {uid, name, level, price, rarity}
    Không bao gồm weapon đang equipped.
    """
    weapons:  list[str] = user.get("weapons", [])
    equipped: set[str]  = set(user.get("equipped", []))
    result = []

    for entry in weapons:
        if entry in equipped:
            continue
        entity = get_weapon_entity(user, entry)
        if entity is None:
            continue
        w_data = entity.data if hasattr(entity, "data") else {}
        w_rarity = w_data.get("rarity", "")
        if w_rarity != rarity:
            continue
        result.append({
            "uid":    entry,
            "name":   entity.fmt_name() if hasattr(entity, "fmt_name") else str(entry),
            "level":  getattr(entity, "level", 0),
            "price":  entity.get_price() if hasattr(entity, "get_price") else 0,
            "rarity": w_rarity,
        })

    return result


def _build_rarity_sell_embed(
    candidates: list[dict],
    rarity: str,
    color: int,
) -> discord.Embed:
    """Tạo embed preview bán weapon theo rarity."""
    total = sum(c["price"] for c in candidates)
    rlabel = W_RARITY_LABEL.get(rarity, rarity.capitalize())

    lines = []
    for c in candidates:
        lines.append(f"• {c['name']} — **{c['price']:,}** {_COIN}")

    desc = "\n".join(lines) if lines else "_Không có weapon_"
    embed = discord.Embed(
        title=f"{_SELL_ICON} | Bán tất cả — {rlabel}",
        description=desc,
        color=color,
    )
    embed.set_footer(text=f"Tổng: {total:,} {_COIN}  •  {len(candidates)} weapon")
    return embed


# ═══════════════════════════════════════════════════════════
# INTERNAL: confirm view (dùng cho rarity bulk-sell)
# ═══════════════════════════════════════════════════════════

class _ConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = None
        self.message: discord.Message | None = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="⏰ Hết thời gian — đã huỷ.", embed=None, view=self)
        except Exception:
            pass

    @discord.ui.button(
        emoji=discord.PartialEmoji.from_str("<:Tick:1495466684520206528>"),
        label="Xác nhận",
        style=discord.ButtonStyle.success,
    )
    async def btn_confirm(self, interaction: discord.Interaction, _btn):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(
        emoji=discord.PartialEmoji.from_str("<:X_:1495466670616219819>"),
        label="Huỷ",
        style=discord.ButtonStyle.danger,
    )
    async def btn_cancel(self, interaction: discord.Interaction, _btn):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


# ═══════════════════════════════════════════════════════════
# INTERNAL: core sell logic (shared giữa prefix & slash)
# ═══════════════════════════════════════════════════════════

async def _do_sell_item(
    author_id: int,
    uid: str,
    item_id: str,
    amount_str: str,
    send_fn,
):
    """Sell item logic. send_fn(content=, embed=) → gửi phản hồi."""
    user, upgraded_weapons = get_user(uid)

    if item_id not in user["inv"]:
        return await send_fn(content=f"{ERR} | Bạn không có vật phẩm này.")

    item = get_item_by_id(item_id)
    if not item:
        return await send_fn(content=f"{ERR} | Item không tồn tại.")

    owned = user["inv"][item_id]
    if amount_str.lower() == "all":
        qty = owned
    else:
        try:
            qty = int(amount_str)
        except ValueError:
            return await send_fn(content=f"{ERR} | Số lượng không hợp lệ.")

    if qty <= 0 or qty > owned:
        return await send_fn(content=f"{ERR} | Bạn chỉ có {owned} cái, không đủ.")

    effects = parse_effects(user.get("equipped", []), user)
    total   = calc_sell_value(item, qty, effects)

    if not remove_item(user, item_id, qty):
        return await send_fn(content=f"{ERR} | Không thể bán.")

    if not save_user(uid, user, upgraded_weapons):
        return await send_fn(content=f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

    await update_balance_safe(author_id, total)
    add_quest_progress(author_id, "items_sold", qty)

    await send_fn(
        content=(
            f"{_COIN} | Đã bán **{qty}x** {item['emoji']} {item['name']} "
            f"→ nhận **{total:,}** {_COIN}"
        )
    )


async def _do_sell_all(
    author_id: int,
    uid: str,
    send_fn,
):
    """Bán tất cả item (không tính crate)."""
    user, upgraded_weapons = get_user(uid)

    sell_ids = [k for k in user["inv"] if not k.startswith("crate_")]
    if not sell_ids:
        return await send_fn(
            content=f"{_BACKPACK} Không có vật phẩm nào để bán (crate không tính)."
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
        lines.append(f"{item['emoji']} {item['name']} x{qty} → **{total:,}** {_COIN}")
        remove_item(user, item_id, qty)

    if not save_user(uid, user, upgraded_weapons):
        return await send_fn(content=f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

    await update_balance_safe(author_id, grand_total)
    add_quest_progress(author_id, "items_sold", total_qty)

    embed = discord.Embed(
        title=f"{_SELL_ICON} | Bán tất cả vật phẩm",
        description="\n".join(lines) or "_Không có gì_",
        color=0xFFD700,
    )
    embed.set_footer(text=f"Tổng nhận: {grand_total:,} {_COIN}")
    await send_fn(embed=embed)


async def _do_sell_weapon(
    author_id: int,
    uid: str,
    weapon_arg: str,
    amount: int,
    send_fn,
):
    """Bán weapon theo UID / base_id."""
    async with get_user_lock(uid):
        user, upgraded_weapons = get_user(uid)

        targets, err = _resolve_sell_weapon_targets(user, weapon_arg, amount)
        if err:
            return await send_fn(content=f"{ERR} | {err}")

        entity = get_weapon_entity(user, targets[0])
        if entity is None:
            return await send_fn(
                content=(
                    f"{ERR} | Không tìm thấy dữ liệu vũ khí `{targets[0]}` "
                    f"trong database. Liên hệ admin nếu lỗi tiếp tục."
                )
            )

        total_value = 0
        for t in targets:
            t_entity = get_weapon_entity(user, t)
            total_value += t_entity.get_price() if t_entity else entity.get_price()

        from rpg_core import remove_weapon_from_bag
        for t in targets:
            removed = remove_weapon_from_bag(user, t)
            if not removed:
                return await send_fn(
                    content=f"{ERR} | Lỗi nội bộ khi xoá vũ khí `{t}`. Thử lại."
                )
            if WeaponID.is_unique(t):
                user["upgraded_weapons"] = [
                    uw for uw in user.get("upgraded_weapons", [])
                    if uw.get("uid") != t
                ]

        if not save_user(uid, user, upgraded_weapons):
            return await send_fn(content=f"{ERR} | Lỗi lưu dữ liệu, thử lại sau!")

        await update_balance_safe(author_id, total_value)
        add_quest_progress(author_id, "weapons_sold", amount)

    qty_label = f"**{amount}x** " if amount > 1 else ""
    w_name    = entity.fmt_name() if hasattr(entity, "fmt_name") else weapon_arg
    await send_fn(
        content=(
            f"{_COIN} | Đã bán {qty_label}{w_name} "
            f"→ nhận **{total_value:,}** {_COIN}"
        )
    )


async def _do_sell_rarity(
    author_id: int,
    uid: str,
    rarity: str,
    amount: int | None,       # None = tất cả
    send_fn,
    edit_fn,
):
    """
    Bán weapon theo rarity — preview embed + confirm UI.

    send_fn(content=, embed=, view=) → gửi tin nhắn mới → trả về Message
    edit_fn(msg, content=, embed=, view=) → edit tin nhắn đó
    """
    user, _ = get_user(uid)

    candidates = _resolve_rarity_candidates(user, rarity)
    if not candidates:
        rlabel = W_RARITY_LABEL.get(rarity, rarity.capitalize())
        return await send_fn(
            content=f"{ERR} | Không có weapon **{rlabel}** nào có thể bán."
        )

    # Sort: level thấp trước, tie-break ngẫu nhiên
    candidates.sort(key=lambda c: (c["level"], random.random()))

    if amount is not None:
        if amount > len(candidates):
            return await send_fn(
                content=(
                    f"{ERR} | Chỉ có **{len(candidates)}** weapon **{rarity}** hợp lệ, "
                    f"không đủ **{amount}** để bán."
                )
            )
        candidates = candidates[:amount]

    color = W_RARITY_COLOR.get(rarity, 0xFFA500)
    embed = _build_rarity_sell_embed(candidates, rarity, color)

    view         = _ConfirmView(author_id)
    view.message = await send_fn(embed=embed, view=view)
    await view.wait()

    if not view.confirmed:
        for child in view.children:
            child.disabled = True
        await edit_fn(view.message, content=f"{ERR} | Đã huỷ bán.", embed=None, view=view)
        return

    # Re-fetch tránh race condition
    user, upgraded_weapons = get_user(uid)

    weapons_set = set(user.get("weapons", []))
    sold_uids   = [c["uid"] for c in candidates if c["uid"] in weapons_set]

    if not sold_uids:
        await edit_fn(
            view.message,
            content=f"{ERR} | Weapon đã không còn trong kho.",
            embed=None, view=None,
        )
        return

    actual_total = 0
    sold_set     = set(sold_uids)

    from rpg_core import remove_weapon_from_bag
    for uid_w in sold_uids:
        entity = get_weapon_entity(user, uid_w)
        actual_total += entity.get_price() if entity else 0

        remove_weapon_from_bag(user, uid_w)
        if WeaponID.is_unique(uid_w):
            user["upgraded_weapons"] = [
                uw for uw in user.get("upgraded_weapons", [])
                if uw.get("uid") != uid_w
            ]

    if not save_user(uid, user, upgraded_weapons):
        await edit_fn(
            view.message,
            content=f"{ERR} | Lỗi lưu dữ liệu — không có gì bị bán.",
            embed=None, view=None,
        )
        return

    await update_balance_safe(author_id, actual_total)
    add_quest_progress(author_id, "weapons_sold", len(sold_uids))

    for child in view.children:
        child.disabled = True

    await edit_fn(
        view.message,
        content=(
            f"{OK} | Đã bán **{len(sold_uids)}** weapon "
            f"— nhận **{actual_total:,}** {_COIN}"
        ),
        embed=None, view=view,
    )


# ═══════════════════════════════════════════════════════════
# HELP EMBED
# ═══════════════════════════════════════════════════════════

def _help_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"{_SELL_ICON} | Hướng dẫn lệnh Sell",
        color=0xFFD700,
    )
    embed.add_field(
        name="🗂️ Item",
        value=(
            "`dtn sell item <id> <số lượng>` — bán item theo số lượng\n"
            "`dtn sell item <id> all` — bán toàn bộ item đó\n"
            f"-# Ví dụ: `dtn sell item herb 10`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🗂️ Bán tất cả item",
        value=(
            "`dtn sell all` — bán tất cả vật phẩm trong kho\n"
            "-# _(Không bán crate)_"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Weapon — theo ID / UID",
        value=(
            "`dtn sell weapon <base_id>` — bán 1 bản sao\n"
            "`dtn sell weapon <base_id> <số lượng>` — bán nhiều bản sao\n"
            "`dtn sell weapon <uid>` — bán đúng instance (amount = 1)\n"
            f"-# Ví dụ: `dtn sell weapon 463 3`"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Weapon — theo Rarity",
        value=(
            "`dtn sell rarity <rarity>` — bán toàn bộ weapon rarity đó\n"
            "`dtn sell rarity <rarity> <số lượng>` — bán N weapon (level thấp trước)\n"
            "-# Rarity: `common` `c` | `uncommon` `u` | `rare` `r` | `epic` `e` | `legend` `l` | `special` `s`\n"
            f"-# Ví dụ: `dtn sell rarity rare` hoặc `dtn sell rarity r 5`"
        ),
        inline=False,
    )
    embed.add_field(
        name="✨ Slash Commands",
        value=(
            "`/sell item` — bán item\n"
            "`/sell weapon` — bán weapon\n"
            "`/sell rarity` — bán theo rarity\n"
            "`/sell all` — bán tất cả item"
        ),
        inline=False,
    )
    embed.set_footer(text="Weapon đang trang bị (equipped) sẽ không bao giờ bị bán tự động.")
    return embed


# ═══════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════

class RPGSell(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────────────
    # PREFIX GROUP: dtn sell
    # ──────────────────────────────────────────────────────

    @commands.group(name="sell", invoke_without_command=True)
    async def sell(self, ctx):
        """Hiển thị help khi dùng dtn sell không có subcommand."""
        await ctx.send(embed=_help_embed())

    # ─── help ───────────────────────────────────────────────

    @sell.command(name="help", aliases=["h", "?"])
    async def sell_help(self, ctx):
        """dtn sell help — hiển thị hướng dẫn đầy đủ."""
        await ctx.send(embed=_help_embed())

    # ─── item ───────────────────────────────────────────────

    @sell.command(name="item", aliases=["i"])
    async def sell_item(self, ctx, item_id: str = None, amount: str = "1"):
        """dtn sell item <id> [amount|all]"""
        if item_id is None:
            return await ctx.send(
                f"{ERR} | Cú pháp: `dtn sell item <id> [số lượng|all]`\n"
                f"-# Xem thêm: `dtn sell help`"
            )
        uid = str(ctx.author.id)
        await _do_sell_item(
            ctx.author.id, uid, item_id, amount,
            send_fn=lambda content=None, embed=None, **_: ctx.send(content=content, embed=embed),
        )

    # ─── all ────────────────────────────────────────────────

    @sell.command(name="all", aliases=["a"])
    async def sell_all(self, ctx):
        """dtn sell all — bán toàn bộ item (không tính crate)."""
        uid = str(ctx.author.id)
        await _do_sell_all(
            ctx.author.id, uid,
            send_fn=lambda content=None, embed=None, **_: ctx.send(content=content, embed=embed),
        )

    # ─── weapon ─────────────────────────────────────────────

    @sell.command(name="weapon", aliases=["w"])
    async def sell_weapon(self, ctx, weapon_arg: str = None, amount: str = "1"):
        """
        dtn sell weapon <uid|base_id> [amount]
        Sell 1 hoặc nhiều bản sao weapon. Weapon equipped không bị bán.
        """
        if weapon_arg is None:
            return await ctx.send(
                f"{ERR} | Cú pháp: `dtn sell weapon <base_id|uid> [số lượng]`\n"
                f"-# Xem thêm: `dtn sell help`"
            )
        try:
            qty = int(amount)
        except ValueError:
            return await ctx.send(f"{ERR} | Số lượng không hợp lệ — phải là số nguyên.")

        uid = str(ctx.author.id)
        await _do_sell_weapon(
            ctx.author.id, uid, weapon_arg, qty,
            send_fn=lambda content=None, embed=None, **_: ctx.send(content=content, embed=embed),
        )

    # ─── rarity ─────────────────────────────────────────────

    @sell.command(name="rarity", aliases=["r", "rar"])
    async def sell_rarity(self, ctx, rarity_arg: str = None, amount: str = None):
        """
        dtn sell rarity <rarity> [amount|all]
        Bán weapon theo rarity — hiển thị preview + confirm button.
        """
        if rarity_arg is None:
            return await ctx.send(
                f"{ERR} | Cú pháp: `dtn sell rarity <rarity> [số lượng|all]`\n"
                f"-# Rarity: common/uncommon/rare/epic/legend/special\n"
                f"-# Xem thêm: `dtn sell help`"
            )

        parsed = parse_rarity_alias(rarity_arg)
        if not parsed:
            return await ctx.send(
                f"{ERR} | Rarity không hợp lệ: `{rarity_arg}`\n"
                f"-# Dùng: `common` `c` | `uncommon` `u` | `rare` `r` | `epic` `e` | `legend` `l` | `special` `s`"
            )

        qty: int | None
        if amount is None or (isinstance(amount, str) and amount.lower() == "all"):
            qty = None
        else:
            try:
                qty = int(amount)
                if qty <= 0:
                    raise ValueError
            except ValueError:
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")

        uid = str(ctx.author.id)

        async def _send(**kwargs):
            return await ctx.send(**kwargs)

        async def _edit(msg: discord.Message, **kwargs):
            await msg.edit(**kwargs)

        await _do_sell_rarity(ctx.author.id, uid, parsed, qty, _send, _edit)

    # ──────────────────────────────────────────────────────
    # SLASH COMMANDS
    # ──────────────────────────────────────────────────────

    sell_group = app_commands.Group(
        name="sell",
        description="Bán vật phẩm hoặc vũ khí",
    )

    # ─── /sell item ─────────────────────────────────────────

    @sell_group.command(name="item", description="Bán item cụ thể theo ID")
    @app_commands.describe(
        item_id="ID của item (vd: herb, stone)",
        amount="Số lượng muốn bán, hoặc 'all' để bán hết",
    )
    async def slash_sell_item(
        self,
        interaction: discord.Interaction,
        item_id: str,
        amount: str = "1",
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)

        async def _send(content=None, embed=None, **_):
            return await interaction.followup.send(content=content, embed=embed)

        await _do_sell_item(interaction.user.id, uid, item_id, amount, _send)

    # ─── /sell all ──────────────────────────────────────────

    @sell_group.command(name="all", description="Bán toàn bộ vật phẩm trong kho (không tính crate)")
    async def slash_sell_all(self, interaction: discord.Interaction):
        await interaction.response.defer()
        uid = str(interaction.user.id)

        async def _send(content=None, embed=None, **_):
            return await interaction.followup.send(content=content, embed=embed)

        await _do_sell_all(interaction.user.id, uid, _send)

    # ─── /sell weapon ───────────────────────────────────────

    @sell_group.command(name="weapon", description="Bán weapon theo base_id hoặc UID")
    @app_commands.describe(
        weapon_arg="base_id (vd: 463) hoặc UID của weapon",
        amount="Số lượng muốn bán (mặc định: 1)",
    )
    async def slash_sell_weapon(
        self,
        interaction: discord.Interaction,
        weapon_arg: str,
        amount: int = 1,
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)

        async def _send(content=None, embed=None, **_):
            return await interaction.followup.send(content=content, embed=embed)

        await _do_sell_weapon(interaction.user.id, uid, weapon_arg, amount, _send)

    # ─── /sell rarity ───────────────────────────────────────

    @sell_group.command(name="rarity", description="Bán weapon theo rarity (preview + xác nhận)")
    @app_commands.describe(
        rarity="Rarity: common/uncommon/rare/epic/legend/special",
        amount="Số lượng (bỏ trống = bán tất cả rarity đó)",
    )
    async def slash_sell_rarity(
        self,
        interaction: discord.Interaction,
        rarity: str,
        amount: int | None = None,
    ):
        await interaction.response.defer()
        uid = str(interaction.user.id)

        parsed = parse_rarity_alias(rarity)
        if not parsed:
            return await interaction.followup.send(
                content=(
                    f"{ERR} | Rarity không hợp lệ: `{rarity}`\n"
                    f"-# Dùng: `common` `uncommon` `rare` `epic` `legend` `special`"
                )
            )

        async def _send(content=None, embed=None, view=None):
            return await interaction.followup.send(content=content, embed=embed, view=view)

        async def _edit(msg: discord.Message, content=None, embed=None, view=None):
            await msg.edit(content=content, embed=embed, view=view)

        await _do_sell_rarity(interaction.user.id, uid, parsed, amount, _send, _edit)


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

