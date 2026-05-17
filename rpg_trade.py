"""
rpg_trade.py — Hệ thống Trade RPG (Tối ưu)
════════════════════════════════════════════
Lệnh prefix (prefix: dtn):
  dtn trade @user              → mở giao dịch
  dtn trade help               → hướng dẫn (2 trang, nút ◀ ▶)
  dtn trade add <cat> <id> [q] → thêm weapon / item / crate
  dtn trade add gold <số>      → thêm tiền
  dtn trade remove <cat> <id>  → bỏ weapon / item / crate
  dtn trade remove gold <số>   → bỏ tiền
  dtn trade accept             → xác nhận
  dtn trade cancel             → huỷ

Reply shortcuts (reply vào bảng trade):
  add <số>                     → thêm tiền
  add weapon <id>              → thêm vũ khí
  add item <id> [qty]          → thêm item
  add crate <id> [qty]         → thêm crate
  accept                       → xác nhận
  cancel                       → huỷ

Slash (hybrid — đăng ký qua bot.tree.sync() ở main.py):
  /trade, /trade help, /trade add, /trade remove, /trade accept, /trade cancel
"""

import asyncio
import random
import string
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from rpg_core import (
    get_user_lock,
    get_item_by_id,
    add_item, remove_item,
    add_weapon, remove_weapon_from_bag,
)
from rpg_database import get_user, save_user
from rpg_instance import resolve_passive
from rpg_weapon_data import (
    get_weapon_by_id,
    get_crate_by_id,
    WEAPONS, RARE_CRATE_WEAPONS, DARK_CRATE_WEAPON, SPECIAL_WEAPONS, CRATES,
)
from rpg_item import ITEMS
from rpg_quest import add_quest_progress
from cash import update_balance_safe, get_balance

# ── Emoji constants ────────────────────────────────────────────────────
COIN_EMOJI   = "<:Coin:1495831576397742241>"
ERR          = "<:X_:1495466670616219819>"
OK           = "<:Tick:1495466684520206528>"
TRADE_ICON   = "<:Trade:1496101148711583865>"
PLAYER_ICON  = "<:3677:1496101987916189726>"

TRADE_COUNTDOWN = 5   # giây đếm ngược trước khi thực hiện

# ── Weapon pool tổng hợp ───────────────────────────────────────────────
_ALL_WEAPONS: list[dict] = WEAPONS + RARE_CRATE_WEAPONS + DARK_CRATE_WEAPON + SPECIAL_WEAPONS


# ══════════════════════════════════════════════════════════════════════
# LOOKUP HELPERS
# ══════════════════════════════════════════════════════════════════════

def _find_weapon(wid: str) -> dict | None:
    """Tìm weapon definition theo ID hoặc UID (xxx-YYYY)."""
    base_id = wid.split("-")[0]
    return get_weapon_by_id(base_id)


def _find_item(iid: str) -> dict | None:
    return next((i for i in ITEMS if i["id"] == iid), None)


def _find_crate(cid: str) -> dict | None:
    c = CRATES.get(str(cid))
    return {"id": str(cid), **c} if c else None


# ══════════════════════════════════════════════════════════════════════
# HELP EMBED — 2 trang với nút ◀ ▶
# ══════════════════════════════════════════════════════════════════════

def _build_help_page(page: int) -> discord.Embed:
    """Tạo embed help trade theo trang (0 = reply shortcuts, 1 = prefix/slash)."""
    if page == 0:
        embed = discord.Embed(
            title=f"{TRADE_ICON} | Hướng Dẫn Trade — Trang 1 / 2",
            description=(
                "**📨 Lệnh Reply Nhanh**\n"
                "Chỉ cần **reply vào bảng trade** rồi gõ lệnh bên dưới — "
                "không cần prefix, không cần nhớ lệnh dài!"
            ),
            color=0x3498DB,
        )
        embed.add_field(
            name="💬 Cú pháp reply:",
            value=(
                "```\n"
                "add <số tiền>           → thêm tiền vào bảng\n"
                "add weapon <uid>        → thêm vũ khí\n"
                "add item   <id> [qty]   → thêm item\n"
                "add crate  <id> [qty]   → thêm crate\n"
                "accept                  → xác nhận giao dịch\n"
                "cancel                  → huỷ giao dịch\n"
                "```"
            ),
            inline=False,
        )
        embed.add_field(
            name="💡 Lưu ý:",
            value=(
                "• Chỉ **2 người trong phiên trade** mới reply được\n"
                "• Sau mỗi lệnh bảng tự cập nhật, tin cũ bị xoá\n"
                "• Thêm vào bảng sẽ **reset accepted** của cả 2 bên"
            ),
            inline=False,
        )
        embed.set_footer(text="Trang 1/2 · Nhấn ▶ để xem lệnh prefix & slash đầy đủ")
    else:
        embed = discord.Embed(
            title=f"{TRADE_ICON} | Hướng Dẫn Trade — Trang 2 / 2",
            description=(
                "**⌨️ Lệnh Prefix & Slash**\n"
                "Prefix mặc định: `dtn` · Slash: `/trade ...`"
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="🔓 Mở / Đóng giao dịch:",
            value=(
                "```\n"
                "dtn trade @user     /trade user:@user   → mở bảng\n"
                "dtn trade accept    /trade accept       → xác nhận\n"
                "dtn trade cancel    /trade cancel       → huỷ\n"
                "```"
            ),
            inline=False,
        )
        embed.add_field(
            name="➕ Thêm vào bảng:",
            value=(
                "```\n"
                "dtn trade add weapon <uid>        → thêm vũ khí\n"
                "dtn trade add item   <id> [qty]   → thêm item\n"
                "dtn trade add crate  <id> [qty]   → thêm crate\n"
                "dtn trade add gold   <số>         → thêm tiền\n"
                "```\n"
                "Slash: `/trade add category:weapon value:<uid>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="➖ Bỏ khỏi bảng:",
            value=(
                "```\n"
                "dtn trade remove weapon <uid>       → bỏ vũ khí\n"
                "dtn trade remove item   <id> [qty]  → bỏ item\n"
                "dtn trade remove crate  <id> [qty]  → bỏ crate\n"
                "dtn trade remove gold   <số>        → bỏ tiền\n"
                "```\n"
                "Slash: `/trade remove category:gold value:<số>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="📦 ID Crate hợp lệ:",
            value="`001` Common · `002` Rare · `003` Dark · `004` Soul",
            inline=False,
        )
        embed.set_footer(text="Trang 2/2 · Nhấn ◀ để xem lệnh reply nhanh")
    return embed


class TradeHelpView(discord.ui.View):
    """View 2 trang cho lệnh help trade."""

    def __init__(self):
        super().__init__(timeout=120)
        self.page = 0
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page == 1)

    @discord.ui.button(label="◀ Trước", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=_build_help_page(self.page), view=self)

    @discord.ui.button(label="Tiếp ▶", style=discord.ButtonStyle.primary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=_build_help_page(self.page), view=self)


# ══════════════════════════════════════════════════════════════════════
# SESSION HELPERS
# ══════════════════════════════════════════════════════════════════════

def _make_sid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _blank_side() -> dict:
    return {"weapons": [], "items": [], "crates": [], "gold": 0, "accepted": False}


def _side_key(session: dict, uid: str) -> str | None:
    if session["uid_a"] == uid:
        return "side_a"
    if session["uid_b"] == uid:
        return "side_b"
    return None


def _other_side_key(sk: str) -> str:
    return "side_b" if sk == "side_a" else "side_a"


# ══════════════════════════════════════════════════════════════════════
# EMBED BUILDER
# ══════════════════════════════════════════════════════════════════════

def _side_text(side: dict, uid: str, bot, guild) -> tuple[str, str]:
    """Trả về (header, body) cho 1 bên trong embed bảng trade."""
    member = guild.get_member(int(uid)) if guild else None
    name   = member.display_name if member else f"<@{uid}>"
    status = f"{OK} Accepted" if side["accepted"] else "⏳ Waiting..."
    header = f"**{name}** — {status}"

    lines = []

    # Weapons
    for wid in side["weapons"]:
        w       = _find_weapon(wid)
        emoji   = w["emoji"] if w else "<:Uncommon:1495000967417040969>"
        label   = w["name"]  if w else wid
        wi_snap = side.get("weapon_snapshots", {}).get(wid, {})
        lv      = wi_snap.get("level", 1)
        p       = resolve_passive(wi_snap.get("passive")) if wi_snap else None
        p_icon  = p.get("emoji", "") if p and p.get("id") else ""
        lines.append(f"{emoji}{p_icon} **{label}** Lv{lv} `{wid}`")

    # Items
    for entry in side["items"]:
        item  = _find_item(entry["id"])
        emoji = item["emoji"] if item else "📦"
        name_ = item["name"]  if item else entry["id"]
        lines.append(f"{emoji} `{entry['id']}` {name_} ×{entry['qty']}")

    # Crates
    for entry in side.get("crates", []):
        crate = _find_crate(entry["id"])
        emoji = crate["emoji"] if crate else "📦"
        name_ = crate["name"]  if crate else entry["id"]
        lines.append(f"{emoji} `{entry['id']}` {name_} ×{entry['qty']}")

    # Gold
    if side["gold"] > 0:
        lines.append(f"{COIN_EMOJI} **{side['gold']:,}**")

    body = "\n".join(lines) if lines else "*(trống)*"
    return header, body


def _build_embed(session: dict, bot, guild) -> discord.Embed:
    """Tạo embed bảng trade. Footer hướng dẫn reply shortcut."""
    embed = discord.Embed(
        title=f"{TRADE_ICON} | Bảng Giao Dịch  `[{session['sid']}]`",
        color=0x3498DB,
    )
    ha, ba = _side_text(session["side_a"], session["uid_a"], bot, guild)
    hb, bb = _side_text(session["side_b"], session["uid_b"], bot, guild)
    embed.add_field(name=f"{PLAYER_ICON}️ | {ha}", value=ba, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)   # spacer
    embed.add_field(name=f"{PLAYER_ICON}️ | {hb}", value=bb, inline=True)
    embed.set_footer(
        text=(
            "💬 Reply bảng này: add <tiền>  ·  add weapon <id>  ·  add item <id> [qty]  ·  accept  ·  cancel\n"
            "⌨️  Prefix: dtn trade add / remove / accept / cancel  ·  📖 dtn trade help"
        )
    )
    return embed


# ══════════════════════════════════════════════════════════════════════
# EXECUTE TRADE — giữ nguyên logic gốc
# ══════════════════════════════════════════════════════════════════════

async def _execute_trade(ctx, session: dict) -> str:
    """Thực hiện chuyển giao. Trả về chuỗi kết quả."""
    uid_a = session["uid_a"]
    uid_b = session["uid_b"]
    sa    = session["side_a"]
    sb    = session["side_b"]
    notes = []

    # ── Kiểm tra & chuyển tiền ────────────────────────────────────────
    if sa["gold"] > 0 and get_balance(int(uid_a)) < sa["gold"]:
        notes.append(f"⚠️ <@{uid_a}> không đủ tiền → bỏ qua phần tiền.")
        sa["gold"] = 0
    if sb["gold"] > 0 and get_balance(int(uid_b)) < sb["gold"]:
        notes.append(f"⚠️ <@{uid_b}> không đủ tiền → bỏ qua phần tiền.")
        sb["gold"] = 0
    if sa["gold"] > 0:
        await update_balance_safe(int(uid_a), -sa["gold"])
        await update_balance_safe(int(uid_b), +sa["gold"])
    if sb["gold"] > 0:
        await update_balance_safe(int(uid_b), -sb["gold"])
        await update_balance_safe(int(uid_a), +sb["gold"])

    # ── Phase 1: Remove từ A ─────────────────────────────────────────
    pending_weapons_to_b: list[tuple[str, dict | None]] = []
    pending_items_to_b:   list[dict] = []
    pending_crates_to_b:  list[dict] = []

    async with get_user_lock(uid_a):
        user_a, _ = get_user(uid_a)

        for wid in sa["weapons"]:
            if wid not in user_a.get("weapons", []):
                notes.append(f"⚠️ <@{uid_a}> không có vũ khí `{wid}` → bỏ qua.")
                continue
            remove_weapon_from_bag(user_a, wid)
            wi_entry = None
            instances_a = user_a.setdefault("weapon_instances", [])
            for i, wi in enumerate(instances_a):
                if isinstance(wi, dict) and wi.get("uid") == wid:
                    wi_entry = instances_a.pop(i)
                    break
            pending_weapons_to_b.append((wid, wi_entry))

        for entry in sa["items"]:
            if not remove_item(user_a, entry["id"], entry["qty"]):
                notes.append(f"⚠️ <@{uid_a}> không đủ `{entry['id']}` → bỏ qua.")
            else:
                pending_items_to_b.append(entry)

        for entry in sa.get("crates", []):
            cid   = str(entry["id"])
            qty   = entry["qty"]
            inv_a = user_a.setdefault("crates", {})
            owned = inv_a.get(cid, 0)
            if owned < qty:
                notes.append(f"⚠️ <@{uid_a}> không đủ crate `{cid}` ({owned}/{qty}) → bỏ qua.")
            else:
                inv_a[cid] = owned - qty
                if inv_a[cid] == 0:
                    del inv_a[cid]
                pending_crates_to_b.append({"id": cid, "qty": qty})

        save_user(uid_a, user_a)

    # ── Phase 2: Add vào B + Remove từ B ─────────────────────────────
    received_weapons_from_b: list[tuple[str, dict | None]] = []
    received_items_from_b:   list[dict] = []
    received_crates_from_b:  list[dict] = []

    async with get_user_lock(uid_b):
        user_b, _ = get_user(uid_b)

        for wid, wi_entry in pending_weapons_to_b:
            add_weapon(user_b, wid)
            if wi_entry:
                user_b.setdefault("weapon_instances", []).append(wi_entry)
        for entry in pending_items_to_b:
            add_item(user_b, entry["id"], entry["qty"])
        for entry in pending_crates_to_b:
            inv_b = user_b.setdefault("crates", {})
            inv_b[entry["id"]] = inv_b.get(entry["id"], 0) + entry["qty"]

        for wid in sb["weapons"]:
            if wid not in user_b.get("weapons", []):
                notes.append(f"⚠️ <@{uid_b}> không có vũ khí `{wid}` → bỏ qua.")
                continue
            remove_weapon_from_bag(user_b, wid)
            wi_entry = None
            instances_b = user_b.setdefault("weapon_instances", [])
            for i, wi in enumerate(instances_b):
                if isinstance(wi, dict) and wi.get("uid") == wid:
                    wi_entry = instances_b.pop(i)
                    break
            received_weapons_from_b.append((wid, wi_entry))

        for entry in sb["items"]:
            if not remove_item(user_b, entry["id"], entry["qty"]):
                notes.append(f"⚠️ <@{uid_b}> không đủ `{entry['id']}` → bỏ qua.")
            else:
                received_items_from_b.append(entry)

        for entry in sb.get("crates", []):
            cid   = str(entry["id"])
            qty   = entry["qty"]
            inv_b = user_b.setdefault("crates", {})
            owned = inv_b.get(cid, 0)
            if owned < qty:
                notes.append(f"⚠️ <@{uid_b}> không đủ crate `{cid}` ({owned}/{qty}) → bỏ qua.")
            else:
                inv_b[cid] = owned - qty
                if inv_b[cid] == 0:
                    del inv_b[cid]
                received_crates_from_b.append({"id": cid, "qty": qty})

        save_user(uid_b, user_b)

    # ── Phase 3: Add received từ B vào A ─────────────────────────────
    async with get_user_lock(uid_a):
        user_a, _ = get_user(uid_a)

        for wid, wi_entry in received_weapons_from_b:
            add_weapon(user_a, wid)
            if wi_entry:
                user_a.setdefault("weapon_instances", []).append(wi_entry)
        for entry in received_items_from_b:
            add_item(user_a, entry["id"], entry["qty"])
        for entry in received_crates_from_b:
            inv_a = user_a.setdefault("crates", {})
            inv_a[entry["id"]] = inv_a.get(entry["id"], 0) + entry["qty"]

        save_user(uid_a, user_a)

    # ── Quest progress ────────────────────────────────────────────────
    add_quest_progress(uid_a, "trades_done")
    add_quest_progress(uid_b, "trades_done")

    m_a = ctx.guild.get_member(int(uid_a)) if ctx.guild else None
    m_b = ctx.guild.get_member(int(uid_b)) if ctx.guild else None
    ta  = m_a.mention if m_a else f"<@{uid_a}>"
    tb  = m_b.mention if m_b else f"<@{uid_b}>"

    result = f"{OK} | **Giao dịch hoàn tất!** {ta} ↔{TRADE_ICON} {tb}"
    if notes:
        result += "\n" + "\n".join(notes)
    return result


# ══════════════════════════════════════════════════════════════════════
# COG
# ══════════════════════════════════════════════════════════════════════

class RPGTrade(commands.Cog):
    def __init__(self, bot):
        self.bot      = bot
        self.sessions: dict[str, dict] = {}   # sid → session

    # ── Internal helpers ──────────────────────────────────────────────

    def _by_uid(self, uid: str) -> dict | None:
        """Tìm session đang mở theo user ID."""
        for s in self.sessions.values():
            if uid in (s["uid_a"], s["uid_b"]):
                return s
        return None

    def _invalidate_accepted(self, session: dict):
        """Reset accepted của cả 2 khi bảng thay đổi."""
        session["side_a"]["accepted"] = False
        session["side_b"]["accepted"] = False

    async def _update_embed(self, session: dict, channel: discord.TextChannel):
        """Xoá bảng cũ → gửi bảng mới (tránh bị cuốn trôi)."""
        msg_id = session.get("msg_id")
        if msg_id:
            try:
                old_msg = await channel.fetch_message(msg_id)
                await old_msg.delete()
            except Exception:
                pass
        try:
            new_msg = await channel.send(
                embed=_build_embed(session, self.bot, channel.guild)
            )
            session["msg_id"] = new_msg.id
        except Exception:
            pass

    # ── Accept / Cancel helpers (dùng chung cho prefix + slash + reply) ──

    async def _do_accept(self, ctx: commands.Context, uid: str, session: dict):
        sk = _side_key(session, uid)
        session[sk]["accepted"] = True

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)

        sa, sb = session["side_a"], session["side_b"]
        if sa["accepted"] and sb["accepted"]:
            await ctx.send(
                f"{OK} Cả 2 đã đồng ý! Trade thực hiện sau **{TRADE_COUNTDOWN} giây**...\n"
                f"_(gõ `dtn trade cancel` ngay bây giờ nếu muốn huỷ)_"
            )
            await asyncio.sleep(TRADE_COUNTDOWN)

            if session["sid"] not in self.sessions:
                return   # bị cancel trong lúc đợi

            result = await _execute_trade(ctx, session)
            del self.sessions[session["sid"]]

            ch     = self.bot.get_channel(session.get("channel_id", ctx.channel.id)) or ctx.channel
            msg_id = session.get("msg_id")
            if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    done_embed = discord.Embed(
                        title=f"{OK} Giao Dịch Hoàn Tất",
                        description=result,
                        color=0x2ECC71,
                    )
                    await msg.edit(embed=done_embed)
                    return
                except Exception:
                    pass
            await ctx.send(result)
        else:
            other_uid = session["uid_b"] if sk == "side_a" else session["uid_a"]
            m         = ctx.guild.get_member(int(other_uid)) if ctx.guild else None
            other_tag = m.mention if m else f"<@{other_uid}>"
            await ctx.send(f"{OK} Bạn đã accept! Đang chờ {other_tag} xác nhận...")

    async def _do_cancel(self, ctx: commands.Context, uid: str, session: dict):
        sid = session["sid"]
        del self.sessions[sid]

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        if channel:
            msg_id = session.get("msg_id")
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=discord.Embed(
                        title=f"{ERR} Giao Dịch Bị Huỷ", color=0xE74C3C
                    ))
                except Exception:
                    pass
        await ctx.send(f"{ERR} Giao dịch đã bị huỷ.")

    async def _do_add(
        self,
        ctx: commands.Context,
        category: str,
        item_id: str,
        qty: str = "1",
    ):
        """
        Logic thêm weapon / item / crate vào bảng trade.
        Dùng chung cho prefix, slash và reply shortcut.
        """
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Bạn không có giao dịch đang mở.\n"
                f"💡 Dùng `dtn trade @user` để mở · Xem hướng dẫn: **`dtn trade help`**"
            )

        sk   = _side_key(session, uid)
        side = session[sk]
        cat  = category.lower()

        # ── Weapon ────────────────────────────────────────────────
        if cat == "weapon":
            wid      = item_id
            user, _  = get_user(uid)
            bag      = user.get("weapons", [])

            if wid not in bag:
                return await ctx.send(f"{ERR} | Bạn không có vũ khí `{wid}` trong bag.")
            if wid in side["weapons"]:
                return await ctx.send(f"{ERR} | `{wid}` đã có trong bảng rồi.")

            owned_count   = bag.count(wid)
            already_added = side["weapons"].count(wid)
            if already_added >= owned_count:
                return await ctx.send(f"{ERR} | Bạn chỉ có {owned_count}x `{wid}`.")

            side["weapons"].append(wid)
            user_snap, _ = get_user(uid)
            wi_snap = next(
                (wi for wi in user_snap.get("weapon_instances", [])
                 if isinstance(wi, dict) and wi.get("uid") == wid),
                {},
            )
            side.setdefault("weapon_snapshots", {})[wid] = {
                "level":   wi_snap.get("level", 1),
                "passive": wi_snap.get("passive"),
            }
            self._invalidate_accepted(session)

        # ── Item ──────────────────────────────────────────────────
        elif cat == "item":
            try:
                quantity = int(qty)
                assert quantity > 0
            except (ValueError, AssertionError):
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")

            if not _find_item(item_id):
                return await ctx.send(f"{ERR} | Item `{item_id}` không tồn tại.")

            user, _   = get_user(uid)
            owned_qty = user["inv"].get(item_id, 0)
            already   = sum(e["qty"] for e in side["items"] if e["id"] == item_id)

            if owned_qty < already + quantity:
                return await ctx.send(
                    f"{ERR} | Bạn có {owned_qty}x `{item_id}` (đã thêm {already}x vào bảng)."
                )
            for e in side["items"]:
                if e["id"] == item_id:
                    e["qty"] += quantity
                    break
            else:
                side["items"].append({"id": item_id, "qty": quantity})

            self._invalidate_accepted(session)

        # ── Crate ─────────────────────────────────────────────────
        elif cat == "crate":
            crate = _find_crate(item_id)
            if not crate:
                return await ctx.send(
                    f"{ERR} | Crate `{item_id}` không tồn tại. "
                    f"Hợp lệ: `001` Common · `002` Rare · `003` Dark · `004` Soul"
                )
            try:
                quantity = int(qty)
                assert quantity > 0
            except (ValueError, AssertionError):
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")

            user, _   = get_user(uid)
            owned_qty = user.get("crates", {}).get(str(item_id), 0)
            already   = sum(e["qty"] for e in side.get("crates", []) if e["id"] == item_id)

            if owned_qty < already + quantity:
                return await ctx.send(
                    f"{ERR} | Bạn có {owned_qty}x crate `{item_id}` (đã thêm {already}x vào bảng)."
                )
            for e in side["crates"]:
                if e["id"] == item_id:
                    e["qty"] += quantity
                    break
            else:
                side["crates"].append({"id": item_id, "qty": quantity})

            self._invalidate_accepted(session)

        else:
            return await ctx.send(
                f"{ERR} | Loại không hợp lệ. Dùng `weapon`, `item`, hoặc `crate`.\n"
                f"💡 Xem hướng dẫn: **`dtn trade help`**"
            )

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)
        await ctx.send(f"{OK} | Đã thêm `{item_id}` vào bảng.")

    async def _do_give_inner(
        self,
        ctx: commands.Context,
        uid: str,
        session: dict,
        amount: int,
    ):
        """Thêm gold vào bảng trade (dùng chung cho prefix / slash / reply)."""
        if amount <= 0:
            return await ctx.send(f"{ERR} | Số tiền phải > 0.")
        sk   = _side_key(session, uid)
        side = session[sk]
        if get_balance(ctx.author.id) < side["gold"] + amount:
            return await ctx.send(f"{ERR} | Số dư không đủ.")
        side["gold"] += amount
        self._invalidate_accepted(session)
        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)
        await ctx.send(f"{OK} | Đã thêm **{amount:,}** {COIN_EMOJI} vào bảng.")

    async def _do_remove_inner(
        self,
        ctx: commands.Context,
        uid: str,
        session: dict,
        category: str,
        value: str,
        qty: str = "1",
    ):
        """Bỏ gold / weapon / item / crate khỏi bảng (dùng chung)."""
        sk   = _side_key(session, uid)
        side = session[sk]
        cat  = category.lower()

        if cat == "gold":
            try:
                amount = int(value)
                assert amount > 0
            except (ValueError, AssertionError):
                return await ctx.send(f"{ERR} | Số tiền không hợp lệ.")
            side["gold"] = max(0, side["gold"] - amount)
            self._invalidate_accepted(session)
            channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
            await self._update_embed(session, channel or ctx.channel)
            return await ctx.send(f"{OK} | Đã bỏ **{amount:,}** {COIN_EMOJI} khỏi bảng.")

        elif cat == "weapon":
            wid = value
            if wid not in side["weapons"]:
                return await ctx.send(f"{ERR} | Vũ khí `{wid}` không có trong bảng.")
            side["weapons"].remove(wid)
            self._invalidate_accepted(session)

        elif cat == "item":
            iid = value
            try:
                quantity = int(qty)
                assert quantity > 0
            except (ValueError, AssertionError):
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")
            found = False
            for e in side["items"]:
                if e["id"] == iid:
                    e["qty"] = max(0, e["qty"] - quantity)
                    found = True
                    break
            if not found:
                return await ctx.send(f"{ERR} | Item `{iid}` không có trong bảng.")
            side["items"] = [e for e in side["items"] if e["qty"] > 0]
            self._invalidate_accepted(session)

        elif cat == "crate":
            cid = value
            try:
                quantity = int(qty)
                assert quantity > 0
            except (ValueError, AssertionError):
                return await ctx.send(f"{ERR} | Số lượng không hợp lệ.")
            found = False
            for e in side["crates"]:
                if e["id"] == cid:
                    e["qty"] = max(0, e["qty"] - quantity)
                    found = True
                    break
            if not found:
                return await ctx.send(f"{ERR} | Crate `{cid}` không có trong bảng.")
            side["crates"] = [e for e in side["crates"] if e["qty"] > 0]
            self._invalidate_accepted(session)

        else:
            return await ctx.send(
                f"{ERR} | Loại không hợp lệ. Dùng `weapon`, `item`, `crate`, hoặc `gold`.\n"
                f"💡 Xem hướng dẫn: **`dtn trade help`**"
            )

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)
        await ctx.send(f"{OK} | Đã bỏ khỏi bảng.")

    # ══════════════════════════════════════════════════════════════
    # REPLY SHORTCUTS — on_message listener
    # ══════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Phát hiện reply vào bảng trade → xử lý lệnh rút gọn:
          add <số>                    → thêm tiền
          add weapon/item/crate <id>  → thêm
          accept                      → xác nhận
          cancel                      → huỷ
        """
        if message.author.bot or not message.reference:
            return

        uid     = str(message.author.id)
        session = self._by_uid(uid)
        if not session:
            return

        # Chỉ xử lý nếu reply đúng vào bảng trade
        if message.reference.message_id != session.get("msg_id"):
            return

        content = message.content.strip()
        parts   = content.split()
        if not parts:
            return

        # Tạo context từ message để tái sử dụng helper methods
        ctx = await self.bot.get_context(message)
        cmd = parts[0].lower()

        if cmd == "accept":
            await self._do_accept(ctx, uid, session)

        elif cmd == "cancel":
            await self._do_cancel(ctx, uid, session)

        elif cmd == "add":
            if len(parts) < 2:
                return await ctx.send(
                    f"{ERR} | Cú pháp reply:\n"
                    f"• `add <tiền>` · `add weapon <id>` · `add item <id> [qty]` · `add crate <id> [qty]`"
                )
            second = parts[1].lower()
            if second in ("weapon", "item", "crate"):
                if len(parts) < 3:
                    return await ctx.send(f"{ERR} | Thiếu ID. Cú pháp: `add {second} <id>`")
                item_id = parts[2]
                qty     = parts[3] if len(parts) > 3 else "1"
                await self._do_add(ctx, second, item_id, qty)
            else:
                # Coi là thêm tiền
                try:
                    amount = int(parts[1])
                    await self._do_give_inner(ctx, uid, session, amount)
                except ValueError:
                    await ctx.send(
                        f"{ERR} | Cú pháp: `add <tiền>` hoặc `add weapon/item/crate <id> [qty]`\n"
                        f"📖 Xem chi tiết: **`dtn trade help`**"
                    )
        else:
            # Lệnh không hợp lệ — gợi ý nhẹ
            await ctx.send(
                f"{ERR} | Lệnh reply không nhận ra. Có thể dùng:\n"
                f"`add <tiền>` · `add weapon <id>` · `accept` · `cancel`\n"
                f"📖 Đầy đủ hơn: **`dtn trade help`**"
            )

    # ══════════════════════════════════════════════════════════════
    # COMMANDS — Hybrid (prefix + slash)
    # ══════════════════════════════════════════════════════════════

    # ── trade (group) ─────────────────────────────────────────────
    @commands.hybrid_group(
        name="trade",
        invoke_without_command=True,
        description="Mở giao dịch với người chơi khác.",
    )
    @app_commands.describe(user="Người chơi muốn trade cùng")
    async def trade(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """
        dtn trade @user   → mở bảng giao dịch
        dtn trade         → gợi ý lệnh (dùng khi không có subcommand)
        """
        uid = str(ctx.author.id)

        if user:
            if user.id == ctx.author.id:
                return await ctx.send(
                    f"{ERR} | Không thể trade với chính mình.",
                    ephemeral=bool(ctx.interaction),
                )
            t_uid = str(user.id)

            if self._by_uid(uid):
                return await ctx.send(
                    f"{ERR} | Bạn đang có giao dịch mở. Dùng `dtn trade cancel` trước.",
                    ephemeral=bool(ctx.interaction),
                )
            if self._by_uid(t_uid):
                return await ctx.send(
                    f"{ERR} | {user.mention} đang có giao dịch mở.",
                    ephemeral=bool(ctx.interaction),
                )

            sid     = _make_sid()
            session = {
                "sid":    sid,
                "uid_a":  uid,
                "uid_b":  t_uid,
                "side_a": _blank_side(),
                "side_b": _blank_side(),
            }
            self.sessions[sid] = session

            embed = _build_embed(session, self.bot, ctx.guild)
            tip   = (
                f"{TRADE_ICON} | {ctx.author.mention} mời {user.mention} giao dịch!\n"
                f"💬 **Reply vào bảng** để dùng lệnh nhanh: "
                f"`add <tiền>` · `add weapon <id>` · `accept` · `cancel`\n"
                f"📖 Xem đầy đủ: **`dtn trade help`**  ·  Slash: `/trade help`"
            )
            msg = await ctx.send(tip, embed=embed)
            session["msg_id"]     = msg.id
            session["channel_id"] = ctx.channel.id

        else:
            # Không có mention → gợi ý ngắn
            await ctx.send(
                f"{TRADE_ICON} | Dùng **`dtn trade @user`** để mở giao dịch.\n"
                f"📖 Xem hướng dẫn chi tiết: **`dtn trade help`**",
                ephemeral=bool(ctx.interaction),
            )

    # ── trade help ────────────────────────────────────────────────
    @trade.command(name="help", description="Xem hướng dẫn trade chi tiết (2 trang).")
    async def trade_help(self, ctx: commands.Context):
        """
        Hiển thị help trade có nút ◀ ▶ để chuyển trang.
        Trang 1: lệnh reply nhanh | Trang 2: lệnh prefix & slash đầy đủ
        """
        view = TradeHelpView()
        await ctx.send(
            embed=_build_help_page(0),
            view=view,
            ephemeral=bool(ctx.interaction),
        )

    # ── trade add ─────────────────────────────────────────────────
    @trade.command(name="add", description="Thêm weapon / item / crate / tiền vào bảng trade.")
    @app_commands.describe(
        category="Loại muốn thêm",
        value="UID vũ khí / ID item-crate / số tiền (tuỳ category)",
        qty="Số lượng (chỉ dùng với item & crate, mặc định 1)",
    )
    async def trade_add(
        self,
        ctx: commands.Context,
        category: Literal["weapon", "item", "crate", "gold"],
        value: str,
        qty: str = "1",
    ):
        """Thêm weapon / item / crate / tiền vào bảng trade."""
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Bạn không có giao dịch đang mở.\n"
                f"💡 Dùng `dtn trade @user` để mở · Xem hướng dẫn: **`dtn trade help`**",
                ephemeral=bool(ctx.interaction),
            )

        if category == "gold":
            try:
                amount = int(value)
            except ValueError:
                return await ctx.send(
                    f"{ERR} | Số tiền không hợp lệ.",
                    ephemeral=bool(ctx.interaction),
                )
            await self._do_give_inner(ctx, uid, session, amount)
        else:
            await self._do_add(ctx, category, value, qty)

    # ── trade remove ──────────────────────────────────────────────
    @trade.command(name="remove", description="Bỏ weapon / item / crate / tiền khỏi bảng trade.")
    @app_commands.describe(
        category="Loại muốn bỏ",
        value="UID vũ khí / ID item-crate / số tiền (tuỳ category)",
        qty="Số lượng (chỉ dùng với item & crate, mặc định 1)",
    )
    async def trade_remove(
        self,
        ctx: commands.Context,
        category: Literal["weapon", "item", "crate", "gold"],
        value: str,
        qty: str = "1",
    ):
        """Bỏ weapon / item / crate / tiền khỏi bảng trade."""
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Bạn không có giao dịch đang mở.",
                ephemeral=bool(ctx.interaction),
            )
        await self._do_remove_inner(ctx, uid, session, category, value, qty)

    # ── trade accept ──────────────────────────────────────────────
    @trade.command(name="accept", description="Xác nhận giao dịch. Cả 2 accept → trade sau 5 giây.")
    async def trade_accept(self, ctx: commands.Context):
        """Xác nhận giao dịch."""
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Không có giao dịch nào đang mở.",
                ephemeral=bool(ctx.interaction),
            )
        await self._do_accept(ctx, uid, session)

    # ── trade cancel ──────────────────────────────────────────────
    @trade.command(name="cancel", description="Huỷ giao dịch đang mở.")
    async def trade_cancel(self, ctx: commands.Context):
        """Huỷ giao dịch."""
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Không có giao dịch nào đang mở.",
                ephemeral=bool(ctx.interaction),
            )
        await self._do_cancel(ctx, uid, session)


# ══════════════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGTrade(bot))
