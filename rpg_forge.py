"""
rpg_forge.py — Lệnh rr (reroll)

Cho phép người chơi tiêu shard (item 1999) để roll lại passive hoặc quality
của một vũ khí, xem so sánh cũ vs mới qua Discord embed, rồi xác nhận hoặc hủy.

Cú pháp:
    rr <uid | tên vũ khí> passive
    rr <uid | tên vũ khí> quality
"""

import asyncio
import random
from datetime import datetime, timezone

import discord

from rpg_passive  import roll_passive, resolve_passive, _is_valid_passive
from rpg_instance import (
    roll_quality, quality_label, quality_color,
    build_weapon_effects,
    QUALITY_MIN, QUALITY_MAX,
)
from rpg_weapon_data import get_weapon_by_id, RARITY_LABEL
from rpg_core        import remove_item, load_data, save_data, get_user, get_user_lock


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

ERR         = "<:X_:1495466670616219819>"
OK          = "<:Tick:1495466684520206528>"
RR_ICON     = "<:Rerrol:1506332609452441670>"
SHARD_ID    = "1999"
FORGE_IMG   = "IMG_forge.png"

RR_COST      = {"passive": (80, 120),  "quality": (130, 162)}
RR_COST_HALF = {"passive": (40, 60),   "quality": (65,  81)}

# { weapon_uid : discord_user_id (str) } — chống reroll đồng thời cùng vũ khí
_active_rr: dict[str, str] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _rr_parse_target(user: dict, weapon_arg: str):
    """
    Tìm weapon instance theo uid chính xác, hoặc theo tên (case-insensitive).

    Returns:
        (wi, w_data)  — nếu tìm thấy đúng 1 kết quả
        str           — thông báo lỗi
    """
    instances = user.get("weapon_instances", [])

    # Ưu tiên match uid trực tiếp
    for wi in instances:
        if isinstance(wi, dict) and wi.get("uid") == weapon_arg:
            w_data = get_weapon_by_id(wi["base_id"])
            if w_data is None:
                return f"{ERR} Không tìm thấy dữ liệu vũ khí `{wi['base_id']}`."
            return (wi, w_data)

    # Fallback: match theo tên
    arg_lower = weapon_arg.lower()
    matches = []
    for wi in instances:
        if not isinstance(wi, dict):
            continue
        w_data = get_weapon_by_id(wi.get("base_id", ""))
        if w_data and w_data["name"].lower() == arg_lower:
            matches.append((wi, w_data))

    if not matches:
        return f"{ERR} Không tìm thấy vũ khí `{weapon_arg}` trong túi đồ."
    if len(matches) > 1:
        uid_list = ", ".join(f"`{wi['uid']}`" for wi, _ in matches)
        return (
            f"{ERR} Có **{len(matches)}** vũ khí trùng tên. "
            f"Dùng UID cụ thể: {uid_list}"
        )
    return matches[0]


def _rr_validate(wi: dict):
    """
    Kiểm tra điều kiện để được reroll (level, broken).

    Returns:
        None  — hợp lệ
        str   — thông báo lỗi
    """
    if wi.get("broken"):
        return f"{ERR} Vũ khí đang **hỏng** — hãy sửa chữa trước khi reroll."
    level = wi.get("level", 0)
    if level < 3:
        return (
            f"{ERR} Vũ khí phải đạt cấp **3** trở lên "
            f"(hiện tại: cấp **{level}**)."
        )
    return None


def _rr_cost(mode: str, is_reroll: bool = False) -> int:
    """Trả về chi phí shard ngẫu nhiên theo bảng."""
    lo, hi = (RR_COST_HALF if is_reroll else RR_COST)[mode]
    return random.randint(lo, hi)


def _rr_check_shard(user: dict, cost: int) -> bool:
    return user["inv"].get(SHARD_ID, 0) >= cost


def _rr_roll_passive(wi: dict, w_data: dict) -> dict:
    """Roll passive mới, trả về stored dict (chưa resolve)."""
    return roll_passive(w_data["rarity"], wi.get("quality", 1.0))


def _rr_roll_quality(w_data: dict) -> float:
    """Roll quality mới trong [QUALITY_MIN, QUALITY_MAX]."""
    return roll_quality(w_data["rarity"])


# ══════════════════════════════════════════════════════════════════════════════
#  EMBED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_passive_block(passive_stored) -> str:
    """
    Trả về multiline string hiển thị passive (resolved) cho embed field.
    Giữ nguyên giá trị âm — không dùng abs().
    """
    if not passive_stored or not _is_valid_passive(passive_stored):
        return "_Chưa có nội tại_"

    resolved = resolve_passive(passive_stored)
    if not resolved:
        return "_Nội tại không hợp lệ_"

    p_label = RARITY_LABEL.get(resolved["rarity"], resolved["rarity"])
    lines = [f"{resolved['emoji']} **{resolved['name']}** _{p_label}_"]

    if resolved.get("desc"):
        lines.append(f"`{resolved['desc']}`")

    for k, v in resolved["effects"].items():
        if k == "extra_slot":
            lines.append(f"└ `{k}`: **+{int(v)} ô**")
        elif isinstance(v, float):
            lines.append(f"└ `{k}`: **{v:+.1%}**")
        else:
            lines.append(f"└ `{k}`: **{v:+}**")

    return "\n".join(lines)


def _fmt_quality_block(wi_snapshot: dict, w_data: dict) -> str:
    """
    Trả về multiline string hiển thị quality + stats đã scale cho embed field.
    wi_snapshot là bản copy của wi với quality đã được thay bằng giá trị cần show.
    """
    q = wi_snapshot.get("quality", 1.0)
    lines = [f"Phẩm chất: {quality_label(q)}"]

    effects = build_weapon_effects(w_data.get("effects", {}), wi_snapshot)
    for k, v in effects.items():
        if k == "extra_slot":
            lines.append(f"└ `{k}`: **+{int(v)} ô**")
        elif isinstance(v, float):
            lines.append(f"└ `{k}`: **{v:+.1%}**")
        else:
            lines.append(f"└ `{k}`: **{v:+}**")

    return "\n".join(lines)


def _build_rr_embed(
    wi: dict,
    w_data: dict,
    old,           # passive stored dict  | float quality  (giá trị CŨ)
    new,           # passive stored dict  | float quality  (giá trị MỚI, chưa ghi)
    cost_used: int,
    shard_left: int,
    mode: str,
) -> discord.Embed:
    """Tạo Discord Embed đầy đủ theo spec."""

    title_mode = "Nội Tại" if mode == "passive" else "Phẩm Chất"
    current_quality = wi.get("quality", 1.0)

    embed = discord.Embed(
        title=f"🔨 Reroll {title_mode} — {w_data.get('emoji', '')} {w_data['name']}",
        color=quality_color(current_quality),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=f"attachment://{FORGE_IMG}")

    # ── Field 1: Thông tin vũ khí ─────────────────────────────────────────────
    rarity_str = RARITY_LABEL.get(w_data["rarity"], w_data["rarity"])
    embed.add_field(
        name="📋 Thông tin vũ khí",
        value=(
            f"UID: `{wi['uid']}`\n"
            f"Độ hiếm: {rarity_str}\n"
            f"Cấp độ: **{wi.get('level', 1)}**"
        ),
        inline=False,
    )

    # ── Field 2: Bản Cũ ───────────────────────────────────────────────────────
    if mode == "passive":
        old_text = _fmt_passive_block(old)
    else:
        wi_old = {**wi, "quality": old}
        old_text = _fmt_quality_block(wi_old, w_data)

    embed.add_field(name="── Bản Cũ ──", value=old_text or "\u200b", inline=True)

    # ── Field 3: Bản Mới ──────────────────────────────────────────────────────
    if mode == "passive":
        new_text = _fmt_passive_block(new)
    else:
        wi_new = {**wi, "quality": new}
        new_text = _fmt_quality_block(wi_new, w_data)

    embed.add_field(name="── Bản Mới ──", value=new_text or "\u200b", inline=True)

    # ── Field 4: Chi phí ──────────────────────────────────────────────────────
    embed.add_field(
        name="💎 Chi phí",
        value=(
            f"Đã dùng: **{cost_used}** 🔷 shard\n"
            f"Còn lại: **{shard_left}** 🔷 shard"
        ),
        inline=False,
    )

    embed.set_footer(text=f"{w_data['id']} | {w_data['name']}")
    return embed


# ══════════════════════════════════════════════════════════════════════════════
#  DISCORD UI — RerollView
# ══════════════════════════════════════════════════════════════════════════════

class RerollView(discord.ui.View):
    """
    View 3 nút: Xác nhận / Reroll khác / Hủy.

    State quan trọng:
        wi        — weapon instance dict gốc (không bị mutate trong view)
        new       — kết quả roll hiện tại (chưa ghi vào wi) — cập nhật khi reroll lại
        cost_used — tổng shard đã tiêu kể từ lần đầu
        data      — snapshot load_data mới nhất (cập nhật sau mỗi lần trừ shard)
    """

    def __init__(
        self,
        *,
        invoker_id: str,     # Discord user ID của người gõ lệnh
        uid: str,            # Discord user ID (str) — dùng cho DB operations
        wi: dict,
        w_data: dict,
        mode: str,           # "passive" | "quality"
        old,                 # giá trị cũ của wi[mode]
        new,                 # kết quả roll lần đầu
        cost_used: int,
        data: dict,          # snapshot load_data — giữ để commit khi Xác nhận
    ):
        super().__init__(timeout=60)
        self.invoker_id = invoker_id
        self.uid        = uid
        self.wi         = wi
        self.w_data     = w_data
        self.mode       = mode
        self.old        = old
        self.new        = new
        self.cost_used  = cost_used
        self.data       = data
        self.message: discord.Message | None = None
        self._click_lock = asyncio.Lock()   # chống double-click

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_invoker(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self.invoker_id

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    # ── Button: Xác nhận ─────────────────────────────────────────────────────

    @discord.ui.button(
        label="Xác nhận",
        emoji="<:Tick:1495466684520206528>",
        style=discord.ButtonStyle.green,
        custom_id="rr_confirm",
    )
    async def btn_confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )

        async with self._click_lock:
            async with get_user_lock(self.uid):
                # Reload fresh để tránh ghi đè dữ liệu cũ
                fresh_data = load_data(self.uid)
                user = get_user(self.uid, fresh_data)

                # Kiểm tra wi còn tồn tại không (có thể bị xóa trong lúc pending)
                target_wi = next(
                    (
                        w for w in user.get("weapon_instances", [])
                        if isinstance(w, dict) and w.get("uid") == self.wi["uid"]
                    ),
                    None,
                )
                if target_wi is None:
                    self._disable_all()
                    self.stop()
                    _active_rr.pop(self.wi["uid"], None)
                    return await interaction.response.edit_message(
                        content=f"{ERR} Vũ khí không còn trong túi đồ — không thể xác nhận.",
                        view=self,
                    )

                # Ghi kết quả mới vào wi
                target_wi[self.mode] = self.new
                await save_data(fresh_data, self.uid)

            shard_left = user["inv"].get(SHARD_ID, 0)
            self._disable_all()
            self.stop()
            _active_rr.pop(self.wi["uid"], None)

            embed = _build_rr_embed(
                self.wi, self.w_data,
                self.old, self.new,
                self.cost_used, shard_left,
                self.mode,
            )
            embed.title = f"✅ Đã xác nhận — {self.w_data.get('emoji', '')} {self.w_data['name']}"
            await interaction.response.edit_message(embed=embed, view=self)

    # ── Button: Reroll khác ───────────────────────────────────────────────────

    @discord.ui.button(
        label="Reroll khác",
        emoji="<:Rerrol:1506332609452441670>",
        style=discord.ButtonStyle.blurple,
        custom_id="rr_reroll",
    )
    async def btn_reroll(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )

        async with self._click_lock:
            cost50 = _rr_cost(self.mode, is_reroll=True)

            async with get_user_lock(self.uid):
                fresh_data = load_data(self.uid)
                user = get_user(self.uid, fresh_data)

                if not _rr_check_shard(user, cost50):
                    left = user["inv"].get(SHARD_ID, 0)
                    return await interaction.response.send_message(
                        f"{ERR} Không đủ shard! Cần **{cost50}**, còn **{left}**.",
                        ephemeral=True,
                    )

                remove_item(user, SHARD_ID, cost50)
                await save_data(fresh_data, self.uid)
                self.data = fresh_data  # cập nhật snapshot

            # Roll mới — KHÔNG ghi vào wi, chỉ cập nhật biến tạm
            if self.mode == "passive":
                self.new = _rr_roll_passive(self.wi, self.w_data)
            else:
                self.new = _rr_roll_quality(self.w_data)

            self.cost_used += cost50

            shard_left = get_user(self.uid, self.data)["inv"].get(SHARD_ID, 0)
            embed = _build_rr_embed(
                self.wi, self.w_data,
                self.old, self.new,
                self.cost_used, shard_left,
                self.mode,
            )
            await interaction.response.edit_message(embed=embed, view=self)

    # ── Button: Hủy ──────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Hủy",
        emoji="<:X_:1495466670616219819>",
        style=discord.ButtonStyle.red,
        custom_id="rr_cancel",
    )
    async def btn_cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )

        self._disable_all()
        self.stop()
        _active_rr.pop(self.wi["uid"], None)

        shard_left = get_user(self.uid, self.data)["inv"].get(SHARD_ID, 0)
        embed = _build_rr_embed(
            self.wi, self.w_data,
            self.old, self.new,
            self.cost_used, shard_left,
            self.mode,
        )
        embed.title = f"🚫 Đã hủy — {self.w_data.get('emoji', '')} {self.w_data['name']}"
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Timeout ───────────────────────────────────────────────────────────────

    async def on_timeout(self):
        _active_rr.pop(self.wi.get("uid", ""), None)
        self._disable_all()

        if self.message is not None:
            try:
                shard_left = get_user(self.uid, self.data)["inv"].get(SHARD_ID, 0)
                embed = _build_rr_embed(
                    self.wi, self.w_data,
                    self.old, self.new,
                    self.cost_used, shard_left,
                    self.mode,
                )
                embed.title = f"<:Clock:1506452991765647471> |  Hết giờ — {self.w_data.get('emoji', '')} {self.w_data['name']}"
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass   # message có thể đã bị xóa — không crash bot


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN COMMAND HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_reroll(ctx, args: list[str]):
    """
    Entry point cho lệnh `rr`.

    args = [weapon_arg, mode]  — sau khi bot dispatcher tách prefix.

    Ví dụ:
        rr 467-ABC12 passive
        rr "Đuôi tắc kè hoa" quality
    """
    # ── Parse args ────────────────────────────────────────────────────────────
    if len(args) < 2:
        return await ctx.send(
            f"{ERR} Cú pháp: `rr <uid | tên vũ khí> <passive | quality>`"
        )

    weapon_arg = args[0]
    mode       = args[1].lower()

    if mode not in ("passive", "quality"):
        return await ctx.send(
            f"{ERR} Mode không hợp lệ. Chỉ chấp nhận `passive` hoặc `quality`."
        )

    uid = str(ctx.author.id)

    async with get_user_lock(uid):
        data = load_data(uid)
        user = get_user(uid, data)

        # ── Tìm vũ khí ───────────────────────────────────────────────────────
        result = _rr_parse_target(user, weapon_arg)
        if isinstance(result, str):
            return await ctx.send(result)
        wi, w_data = result

        # ── Kiểm tra session đang chạy ───────────────────────────────────────
        if wi["uid"] in _active_rr:
            return await ctx.send(
                f"{ERR} Vũ khí `{wi['uid']}` đang trong phiên reroll chưa kết thúc."
            )

        # ── Validate điều kiện vũ khí ─────────────────────────────────────────
        err = _rr_validate(wi)
        if err:
            return await ctx.send(err)

        # ── Tính chi phí & kiểm tra shard ────────────────────────────────────
        cost = _rr_cost(mode, is_reroll=False)
        if not _rr_check_shard(user, cost):
            left = user["inv"].get(SHARD_ID, 0)
            return await ctx.send(
                f"{ERR} Không đủ shard! Cần **{cost}** 🔷, bạn còn **{left}** 🔷."
            )

        # ── Lưu giá trị cũ, trừ shard ngay, save ─────────────────────────────
        old = wi.get(mode)
        remove_item(user, SHARD_ID, cost)
        await save_data(data, uid)

        # ── Roll kết quả — CHƯA ghi vào wi ───────────────────────────────────
        if mode == "passive":
            new = _rr_roll_passive(wi, w_data)
        else:
            new = _rr_roll_quality(w_data)

        # ── Đăng ký session (trong lock để thread-safe) ───────────────────────
        _active_rr[wi["uid"]] = uid

    # ── Build embed & view (ngoài lock — không cần giữ lock khi send) ────────
    shard_left = user["inv"].get(SHARD_ID, 0)
    embed = _build_rr_embed(wi, w_data, old, new, cost, shard_left, mode)

    view = RerollView(
        invoker_id=uid,
        uid=uid,
        wi=wi,
        w_data=w_data,
        mode=mode,
        old=old,
        new=new,
        cost_used=cost,
        data=data,
    )

    forge_file = discord.File(FORGE_IMG, filename=FORGE_IMG)
    msg = await ctx.send(file=forge_file, embed=embed, view=view)
    view.message = msg   # lưu ref để on_timeout có thể edit
