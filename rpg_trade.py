
import asyncio
import random
import string

import discord
from discord.ext import commands

from rpg_core import (
    get_user_lock,
    get_item_by_id,
    add_item, remove_item,
    add_weapon, remove_weapon_from_bag,
)
from rpg_database import get_user, save_user
from rpg_instance import resolve_passive
# FIX-2: Dùng get_weapon_by_id từ rpg_weapo — cover đủ 4 weapon pool
from rpg_weapon_data import (
    get_weapon_by_id,
    get_crate_by_id,
    WEAPONS, RARE_CRATE_WEAPONS, DARK_CRATE_WEAPON, SPECIAL_WEAPONS, CRATES,
)
from rpg_item import ITEMS
from rpg_quest import add_quest_progress
from cash import update_balance_safe, get_balance

COIN_EMOJI = "<:Coin:1495831576397742241>"
ERR        = "<:X_:1495466670616219819>"
OK         = "<:Tick:1495466684520206528>"

TRADE_COUNTDOWN = 5  # giây đếm ngược trước khi thực hiện

# ── Lookup helpers (cover tất cả weapon pool) ──────────────────────
_ALL_WEAPONS: list[dict] = WEAPONS + RARE_CRATE_WEAPONS + DARK_CRATE_WEAPON + SPECIAL_WEAPONS


def _find_weapon(wid: str) -> dict | None:
    """Tìm weapon definition theo ID hoặc UID (xxx-YYYY)."""
    base_id = wid.split("-")[0]
    return get_weapon_by_id(base_id)   


def _find_item(iid: str) -> dict | None:
    return next((i for i in ITEMS if i["id"] == iid), None)


def _find_crate(cid: str) -> dict | None:
    c = CRATES.get(str(cid))
    return {"id": str(cid), **c} if c else None


# ═══════════════════════════════════════════════════════════
# SESSION HELPERS
# ═══════════════════════════════════════════════════════════

def _make_sid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _blank_side() -> dict:
    # NEW-1: thêm "crates" vào side data
    return {"weapons": [], "items": [], "crates": [], "gold": 0, "accepted": False}


def _side_key(session: dict, uid: str) -> str | None:
    if session["uid_a"] == uid:
        return "side_a"
    if session["uid_b"] == uid:
        return "side_b"
    return None


def _other_side_key(sk: str) -> str:
    return "side_b" if sk == "side_a" else "side_a"


# ═══════════════════════════════════════════════════════════
# EMBED BUILDER
# ═══════════════════════════════════════════════════════════

def _side_text(side: dict, uid: str, bot, guild) -> tuple[str, str]:
    """Trả về (header, body) cho 1 bên trong embed."""
    member = guild.get_member(int(uid)) if guild else None
    name   = member.display_name if member else f"<@{uid}>"
    status = f"{OK} Accept" if side["accepted"] else "⏳ Waiting..."
    header = f"**{name}** — {status}"

    lines = []

    # ── Weapons (NEW-4: icon Discord thật từ weapon definition) ──
    for wid in side["weapons"]:
        w       = _find_weapon(wid)
        emoji   = w["emoji"] if w else "<:Uncommon:1495000967417040969>"
        label   = w["name"]  if w else wid
        wi_snap = side.get("weapon_snapshots", {}).get(wid, {})
        lv      = wi_snap.get("level", 1)
        p       = resolve_passive(wi_snap.get("passive")) if wi_snap else None
        p_icon  = p.get("emoji", "") if p and p.get("id") else ""
        lines.append(f"{emoji}{p_icon} **{label}** Lv{lv} `{wid}`")

    # ── Items (NEW-4: icon Discord thật từ item definition) ──
    for entry in side["items"]:
        item  = _find_item(entry["id"])
        emoji = item["emoji"] if item else "📦"
        name_ = item["name"]  if item else entry["id"]
        lines.append(f"{emoji} `{entry['id']}` {name_} ×{entry['qty']}")

    # ── Crates (NEW-1: hiển thị crate với icon thật) ──
    for entry in side.get("crates", []):
        crate = _find_crate(entry["id"])
        emoji = crate["emoji"] if crate else "📦"
        name_ = crate["name"]  if crate else entry["id"]
        lines.append(f"{emoji} `{entry['id']}` {name_} ×{entry['qty']}")

    # ── Gold ──
    if side["gold"] > 0:
        lines.append(f"{COIN_EMOJI} **{side['gold']:,}**")

    body = "\n".join(lines) if lines else "*(trống)*"
    return header, body


def _build_embed(session: dict, bot, guild) -> discord.Embed:
    embed = discord.Embed(
        title=f"<:Trade:1496101148711583865> | Bảng Giao Dịch  `[{session['sid']}]`",
        color=0x3498DB,
    )
    ha, ba = _side_text(session["side_a"], session["uid_a"], bot, guild)
    hb, bb = _side_text(session["side_b"], session["uid_b"], bot, guild)
    embed.add_field(name=f"<:3677:1496101987916189726>️ | {ha}", value=ba, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)   # separator
    embed.add_field(name=f"<:3677:1496101987916189726>️ | {hb}", value=bb, inline=True)
    embed.set_footer(
        text=(
            "trade accept/cancel  │  "
            "aw <id>  ·  ae <id> <qty>  ·  ac <id> <qty>  │  "
            "trade give <tiền>  │  "
            "remove weapon/item/crate <id> [qty]"
        )
    )
    return embed


# ═══════════════════════════════════════════════════════════
# EXECUTE TRADE
# ═══════════════════════════════════════════════════════════

async def _execute_trade(ctx, session: dict) -> str:
    """Thực hiện chuyển giao. Trả về chuỗi kết quả."""
    uid_a = session["uid_a"]
    uid_b = session["uid_b"]
    sa    = session["side_a"]
    sb    = session["side_b"]
    notes = []

    # ── Kiểm tra & chuyển tiền (cash.py độc lập, không cần lock rpg_core) ──
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

    # ── Phase 1: Load A → remove tất cả thứ A cho đi → save A ──────────────
    # pending_*_to_b: danh sách đã remove thành công khỏi A, chờ add vào B
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
                notes.append(
                    f"⚠️ <@{uid_a}> không đủ crate `{cid}` ({owned}/{qty}) → bỏ qua."
                )
            else:
                inv_a[cid] = owned - qty
                if inv_a[cid] == 0:
                    del inv_a[cid]
                pending_crates_to_b.append({"id": cid, "qty": qty})

        save_user(uid_a, user_a)

    # ── Phase 2: Load B → add received từ A + remove thứ B cho đi → save B ──
    # received_*_from_b: danh sách đã remove thành công khỏi B, chờ add vào A
    received_weapons_from_b: list[tuple[str, dict | None]] = []
    received_items_from_b:   list[dict] = []
    received_crates_from_b:  list[dict] = []

    async with get_user_lock(uid_b):
        user_b, _ = get_user(uid_b)

        # Add nhận từ A
        for wid, wi_entry in pending_weapons_to_b:
            add_weapon(user_b, wid)
            if wi_entry:
                user_b.setdefault("weapon_instances", []).append(wi_entry)
        for entry in pending_items_to_b:
            add_item(user_b, entry["id"], entry["qty"])
        for entry in pending_crates_to_b:
            inv_b = user_b.setdefault("crates", {})
            inv_b[entry["id"]] = inv_b.get(entry["id"], 0) + entry["qty"]

        # Remove thứ B cho đi
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
                notes.append(
                    f"⚠️ <@{uid_b}> không đủ crate `{cid}` ({owned}/{qty}) → bỏ qua."
                )
            else:
                inv_b[cid] = owned - qty
                if inv_b[cid] == 0:
                    del inv_b[cid]
                received_crates_from_b.append({"id": cid, "qty": qty})

        save_user(uid_b, user_b)

    # ── Phase 3: Load lại A → add received từ B → save A lần 2 ─────────────
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

    # ── Quest progress ────────────────────────────────────────────────────────
    add_quest_progress(uid_a, "trades_done")
    add_quest_progress(uid_b, "trades_done")

    m_a = ctx.guild.get_member(int(uid_a)) if ctx.guild else None
    m_b = ctx.guild.get_member(int(uid_b)) if ctx.guild else None
    ta  = m_a.mention if m_a else f"<@{uid_a}>"
    tb  = m_b.mention if m_b else f"<@{uid_b}>"

    result = f"{OK} | **Giao dịch hoàn tất!** {ta} ↔<:Trade:1496101148711583865> {tb}"
    if notes:
        result += "\n" + "\n".join(notes)
    return result


# ═══════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════

class RPGTrade(commands.Cog):
    def __init__(self, bot):
        self.bot      = bot
        self.sessions: dict[str, dict] = {}   # sid → session

    # ── helpers ──────────────────────────────────────────────

    def _by_uid(self, uid: str) -> dict | None:
        for s in self.sessions.values():
            if uid in (s["uid_a"], s["uid_b"]):
                return s
        return None

    def _invalidate_accepted(self, session: dict):
        """Khi bảng thay đổi, reset accepted của cả 2."""
        session["side_a"]["accepted"] = False
        session["side_b"]["accepted"] = False

    async def _update_embed(self, session: dict, channel: discord.TextChannel):
        """
        NEW-2: Xoá bảng cũ + gửi bảng mới để tránh trôi.
        Cập nhật session["msg_id"] sau mỗi lần gửi.
        """
        # Xoá tin nhắn bảng cũ
        msg_id = session.get("msg_id")
        if msg_id:
            try:
                old_msg = await channel.fetch_message(msg_id)
                await old_msg.delete()
            except Exception:
                pass

        # Gửi bảng mới
        try:
            new_msg = await channel.send(
                embed=_build_embed(session, self.bot, channel.guild)
            )
            session["msg_id"] = new_msg.id
        except Exception:
            pass

    # ── dtn trade @user ───────────────────────────────────────
    @commands.group(name="trade", invoke_without_command=True)
    async def trade(self, ctx):
        uid = str(ctx.author.id)

        if ctx.message.mentions:
            target = ctx.message.mentions[0]
            if target.id == ctx.author.id:
                return await ctx.send(f"{ERR} | Không thể trade với chính mình.")
            t_uid = str(target.id)

            if self._by_uid(uid):
                return await ctx.send(
                    f"{ERR} | Bạn đang có giao dịch mở. Dùng `dtn trade cancel` trước."
                )
            if self._by_uid(t_uid):
                return await ctx.send(f"{ERR} | {target.mention} đang có giao dịch mở.")

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
            msg   = await ctx.send(
                f"<:Trade:1496101148711583865> | {ctx.author.mention} mời {target.mention} giao dịch!\n"
                f"Dùng **`aw <id>`** · **`ae <id> <qty>`** · **`ac <id> <qty>`** để thêm weapon/item/crate "
                f"rồi **`dtn trade accept`** để xác nhận.",
                embed=embed,
            )
            session["msg_id"]     = msg.id
            session["channel_id"] = ctx.channel.id
        else:
            await ctx.send(
                "<:Trade:1496101148711583865> | **Hướng dẫn Trade:**\n"
                "• `dtn trade @user` — mở bảng\n"
                "• `aw <id>` / `ae <id> <qty>` / `ac <id> <qty>` — thêm weapon/item/crate (aliases)\n"
                "• `dtn add weapon/item/crate <id> [qty]` — lệnh đầy đủ\n"
                "• `dtn remove weapon/item/crate <id> [qty]` / `dtn remove <tiền>` — bỏ ra\n"
                "• `dtn trade give <tiền>` — thêm tiền\n"
                "• `dtn trade accept` — xác nhận; 2 bên accept → trade sau 5 giây\n"
                "• `dtn trade cancel` — huỷ"
            )

    # ── dtn trade give <amount> ───────────────────────────────
    @trade.command(name="give")
    async def trade_give(self, ctx, amount: int):
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(f"{ERR} | Bạn không có giao dịch đang mở.")
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

    # ── dtn trade accept ──────────────────────────────────────
    @trade.command(name="accept")
    async def trade_accept(self, ctx):
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(f"{ERR} | Không có giao dịch nào.")

        sk = _side_key(session, uid)
        session[sk]["accepted"] = True

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)

        sa, sb = session["side_a"], session["side_b"]
        if sa["accepted"] and sb["accepted"]:
            await ctx.send(
                f"{OK} Cả 2 đã đồng ý! Trade sẽ thực hiện sau **{TRADE_COUNTDOWN} giây**...\n"
                f"_(gõ `dtn trade cancel` ngay bây giờ để hủy)_"
            )
            await asyncio.sleep(TRADE_COUNTDOWN)

            if session["sid"] not in self.sessions:
                return  # đã bị cancel trong thời gian đợi

            result = await _execute_trade(ctx, session)
            del self.sessions[session["sid"]]

            # Edit bảng cuối thành màn hình kết quả
            ch     = self.bot.get_channel(session.get("channel_id", ctx.channel.id)) or ctx.channel
            msg_id = session.get("msg_id")
            if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    done_embed = discord.Embed(
                        title="<:Tick:1495466684520206528> Giao Dịch Hoàn Tất",
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

    # ── dtn trade cancel ──────────────────────────────────────
    @trade.command(name="cancel")
    async def trade_cancel(self, ctx):
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(f"{ERR} | Không có giao dịch nào.")

        sid = session["sid"]
        del self.sessions[sid]

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        if channel:
            msg_id = session.get("msg_id")
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(
                        embed=discord.Embed(
                            title=f"{ERR} Giao Dịch Bị Huỷ", color=0xE74C3C
                        )
                    )
                except Exception:
                    pass

        await ctx.send(f"{ERR} Giao dịch đã bị huỷ.")

    # ═══════════════════════════════════════════════════════
    # CORE ADD LOGIC (dùng chung cho add_cmd + aliases)
    # ═══════════════════════════════════════════════════════

    async def _do_add(self, ctx, category: str, item_id: str, qty: str = "1"):
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(
                f"{ERR} | Bạn không có giao dịch đang mở. Dùng `dtn trade @user` trước."
            )

        sk   = _side_key(session, uid)
        side = session[sk]
        cat  = category.lower()

        # ── Weapon ──────────────────────────────────────────
        if cat == "weapon":
            wid  = item_id
            user, _ = get_user(uid)
            bag  = user.get("weapons", [])

            if wid not in bag:
                return await ctx.send(
                    f"{ERR} | Bạn không có vũ khí `{wid}` trong bag."
                )
            if wid in side["weapons"]:
                return await ctx.send(f"{ERR} | `{wid}` đã có trong bảng rồi.")

            owned_count   = bag.count(wid)
            already_added = side["weapons"].count(wid)
            if already_added >= owned_count:
                return await ctx.send(f"{ERR} | Bạn chỉ có {owned_count}x `{wid}`.")

            side["weapons"].append(wid)
            # Snapshot level + passive for embed display (read from already-loaded user)
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

        # ── Item ─────────────────────────────────────────────
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
                    f"{ERR} | Bạn có {owned_qty}x `{item_id}` "
                    f"(đã thêm {already}x vào bảng)."
                )
            for e in side["items"]:
                if e["id"] == item_id:
                    e["qty"] += quantity
                    break
            else:
                side["items"].append({"id": item_id, "qty": quantity})

            self._invalidate_accepted(session)

        # ── Crate (NEW-1) ─────────────────────────────────────
        elif cat == "crate":
            crate = _find_crate(item_id)
            if not crate:
                return await ctx.send(
                    f"{ERR} | Crate `{item_id}` không tồn tại. "
                    f"Crate hợp lệ: `001` Common · `002` Rare · `003` Dark · `004` Soul"
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
                    f"{ERR} | Bạn có {owned_qty}x crate `{item_id}` "
                    f"(đã thêm {already}x vào bảng)."
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
                f"{ERR} | Loại không hợp lệ. Dùng `weapon`, `item`, hoặc `crate`."
            )

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)
        await ctx.send(f"{OK} | Đã thêm `{item_id}` vào bảng.")

    # ── dtn add weapon/item/crate ─────────────────────────────
    @commands.command(name="add")
    async def add_cmd(self, ctx, category: str, item_id: str, qty: str = "1"):
        await self._do_add(ctx, category, item_id, qty)

    # ── NEW-3: Aliases ────────────────────────────────────────
    @commands.command(name="aw")
    async def alias_aw(self, ctx, item_id: str):
        """aw <id>  →  add weapon <id>"""
        await self._do_add(ctx, "weapon", item_id)

    @commands.command(name="ae")
    async def alias_ae(self, ctx, item_id: str, qty: str = "1"):
        """ae <id> [qty]  →  add item <id> [qty]"""
        await self._do_add(ctx, "item", item_id, qty)

    @commands.command(name="ac")
    async def alias_ac(self, ctx, item_id: str, qty: str = "1"):
        """ac <id> [qty]  →  add crate <id> [qty]"""
        await self._do_add(ctx, "crate", item_id, qty)

    # ── dtn remove ────────────────────────────────────────────
    @commands.command(name="remove")
    async def remove_cmd(self, ctx, first: str, second: str = None, third: str = "1"):
        uid     = str(ctx.author.id)
        session = self._by_uid(uid)
        if not session:
            return await ctx.send(f"{ERR} | Bạn không có giao dịch đang mở.")

        sk   = _side_key(session, uid)
        side = session[sk]

        # ── remove <amount>  (tiền) ──
        try:
            amount = int(first)
            if amount <= 0:
                return await ctx.send(f"{ERR} | Số tiền phải > 0.")
            side["gold"] = max(0, side["gold"] - amount)
            self._invalidate_accepted(session)
            channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
            await self._update_embed(session, channel or ctx.channel)
            return await ctx.send(f"{OK} | Đã bỏ **{amount:,}** {COIN_EMOJI} khỏi bảng.")
        except ValueError:
            pass

        # ── remove weapon / item / crate ──
        cat = first.lower()
        if second is None:
            return await ctx.send(
                f"{ERR} | Cú pháp: `dtn remove <tiền>` "
                f"hoặc `dtn remove weapon/item/crate <id> [qty]`"
            )

        if cat == "weapon":
            wid = second
            if wid not in side["weapons"]:
                return await ctx.send(f"{ERR} | Vũ khí `{wid}` không có trong bảng.")
            side["weapons"].remove(wid)
            self._invalidate_accepted(session)

        elif cat == "item":
            iid = second
            try:
                quantity = int(third)
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

        # ── NEW-1: remove crate ──
        elif cat == "crate":
            cid = second
            try:
                quantity = int(third)
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
                f"{ERR} | Loại không hợp lệ. Dùng `weapon`, `item`, `crate`, hoặc số tiền."
            )

        channel = self.bot.get_channel(session.get("channel_id", ctx.channel.id))
        await self._update_embed(session, channel or ctx.channel)
        await ctx.send(f"{OK} | Đã bỏ khỏi bảng.")


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGTrade(bot))
