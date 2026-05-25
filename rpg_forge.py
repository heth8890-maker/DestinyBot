"""
rpg_forge.py — Lệnh rr (reroll)

Cú pháp:
    rr <uid | tên vũ khí> passive
    rr <uid | tên vũ khí> quality
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

import discord
from discord.ext import commands

from rpg_passive  import roll_passive, resolve_passive, _is_valid_passive
from rpg_instance import (
    roll_quality, quality_label, quality_color,
    build_weapon_effects,
)
from rpg_weapon_data import get_weapon_by_id, RARITY_LABEL
from rpg_core        import remove_item, load_data, save_data, get_user, get_user_lock

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

ERR           = "<:X_:1495466670616219819>"
OK            = "<:Tick:1495466684520206528>"
RR_ICON       = "<:Rerrol:1506332609452441670>"
SHARD_ICON    = "<:Enchant_shard:1506136888988405782>"
SHARD_ID      = "1099"
FORGE_IMG     = "IMG_forge.png"

RR_COST      = {"passive": (80, 120),  "quality": (130, 162)}
RR_COST_HALF = {"passive": (40, 60),   "quality": (65,  81)}

# { weapon_uid : discord_user_id } — chống reroll đồng thời cùng vũ khí
_active_rr: dict[str, str] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _rr_parse_target(user: dict, weapon_arg: str):
    instances = user.get("weapon_instances", [])

    for wi in instances:
        if isinstance(wi, dict) and wi.get("uid") == weapon_arg:
            w_data = get_weapon_by_id(wi["base_id"])
            if w_data is None:
                return f"{ERR} Không tìm thấy dữ liệu vũ khí `{wi['base_id']}`."
            return (wi, w_data)

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
        return f"{ERR} Có **{len(matches)}** vũ khí trùng tên. Dùng UID cụ thể: {uid_list}"
    return matches[0]


def _rr_validate(wi: dict):
    if wi.get("broken"):
        return f"{ERR} Vũ khí đang **hỏng** — hãy sửa chữa trước khi reroll."
    level = wi.get("level", 0)
    if level < 3:
        return f"{ERR} Vũ khí phải đạt cấp **3** trở lên (hiện tại: cấp **{level}**)."
    return None


def _rr_cost(mode: str, is_reroll: bool = False) -> int:
    lo, hi = (RR_COST_HALF if is_reroll else RR_COST)[mode]
    return random.randint(lo, hi)


def _rr_check_shard(user: dict, cost: int) -> bool:
    return user["inv"].get(SHARD_ID, 0) >= cost


def _rr_roll_passive(wi: dict, w_data: dict) -> dict:
    return roll_passive(w_data["rarity"], wi.get("quality", 1.0))


def _rr_roll_quality(w_data: dict) -> float:
    return roll_quality(w_data["rarity"])


# ══════════════════════════════════════════════════════════════════════════════
#  EMBED BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _passive_field_icon(passive_stored) -> str:
    """Lấy emoji của passive đã có, fallback về icon mặc định của hệ thống."""
    if passive_stored and _is_valid_passive(passive_stored):
        resolved = resolve_passive(passive_stored)
        if resolved and resolved.get("emoji"):
            return resolved["emoji"]
    return SHARD_ICON


def _fmt_passive_block(passive_stored) -> str:
    """Hiển thị passive đã resolve. Giữ nguyên giá trị âm."""
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
    """Hiển thị quality + stats đã scale."""
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


def _build_embed(
    wi: dict,
    w_data: dict,
    mode: str,
    new,
    cost_used: int,
    *,
    label_new: str = "Kết quả mới",
) -> discord.Embed:
    """
    Embed duy nhất gộp CŨ + MỚI thành 2 field inline.
    label_new tuỳ ngữ cảnh: "Kết quả mới" / "Đã áp dụng" / "Đã hủy".
    """
    rarity_str = RARITY_LABEL.get(w_data["rarity"], w_data["rarity"])
    title_mode = "Nội Tại" if mode == "passive" else "Phẩm Chất"

    # Màu embed theo quality mới (quality mode) hoặc quality hiện tại (passive mode)
    color = (
        quality_color(new)
        if mode == "quality" and isinstance(new, float)
        else quality_color(wi.get("quality", 1.0))
    )

    embed = discord.Embed(
        title=f"{RR_ICON} Reroll {title_mode} — {w_data.get('emoji', '')} {w_data['name']}",
        description=(
            f"UID: `{wi['uid']}` | {rarity_str} | Cấp **{wi.get('level', 1)}**"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=f"attachment://{FORGE_IMG}")

    # ── Field trái: Hiện tại ──────────────────────────────────────────────────
    if mode == "passive":
        icon_old = _passive_field_icon(wi.get("passive"))
        embed.add_field(
            name=f"{icon_old} Hiện tại",
            value=_fmt_passive_block(wi.get("passive")),
            inline=True,
        )
    else:
        embed.add_field(
            name=f"{SHARD_ICON} Hiện tại",
            value=_fmt_quality_block(wi, w_data),
            inline=True,
        )

    # ── Field phải: Kết quả mới ──────────────────────────────────────────────
    if mode == "passive":
        icon_new = _passive_field_icon(new) if (new and _is_valid_passive(new)) else SHARD_ICON
        embed.add_field(
            name=f"{icon_new} {label_new}",
            value=_fmt_passive_block(new),
            inline=True,
        )
    else:
        embed.add_field(
            name=f"{RR_ICON} {label_new}",
            value=_fmt_quality_block({**wi, "quality": new}, w_data),
            inline=True,
        )

    embed.set_footer(text=f"{w_data['id']} | Đã dùng: {cost_used} {SHARD_ICON}")
    return embed


# ══════════════════════════════════════════════════════════════════════════════
#  DISCORD UI — RerollView
# ══════════════════════════════════════════════════════════════════════════════

class RerollView(discord.ui.View):
    """
    View 1 embed duy nhất, không chuyển trang.
    Hiển thị song song CŨ | MỚI với các nút: Hủy | Shard | Reroll | Xác nhận.
    """

    def __init__(
        self,
        *,
        invoker_id: str,
        uid: str,
        wi: dict,
        w_data: dict,
        mode: str,
        old,
        new,
        cost_used: int,
        data: dict,
        img_attached: bool,
    ):
        super().__init__(timeout=60)
        self.invoker_id   = invoker_id
        self.uid          = uid
        self.wi           = wi
        self.w_data       = w_data
        self.mode         = mode
        self.old          = old
        self.new          = new
        self.cost_used    = cost_used
        self.data         = data
        self.img_attached = img_attached
        self.message: discord.Message | None = None
        self._click_lock  = asyncio.Lock()

        self._refresh_buttons()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_invoker(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self.invoker_id

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    def _shard_left(self) -> int:
        return get_user(self.uid, self.data)["inv"].get(SHARD_ID, 0)

    def _next_cost_preview(self) -> int:
        lo, hi = RR_COST_HALF[self.mode]
        return (lo + hi) // 2

    def _refresh_buttons(self):
        self.clear_items()
        self.add_item(self._btn_cancel())
        self.add_item(self._btn_shard())
        self.add_item(self._btn_reroll())
        self.add_item(self._btn_confirm())

    def _current_embed(self, *, label_new: str = "Kết quả mới") -> discord.Embed:
        return _build_embed(
            self.wi, self.w_data, self.mode, self.new,
            self.cost_used, label_new=label_new,
        )

    async def _edit(self, interaction: discord.Interaction, **kwargs):
        embed = self._current_embed(**kwargs)
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Button factories ──────────────────────────────────────────────────────

    def _btn_shard(self):
        btn = discord.ui.Button(
            label=f"Shard · {self.cost_used}",
            emoji=SHARD_ICON,
            style=discord.ButtonStyle.secondary,
            custom_id="rr_shard",
        )
        btn.callback = self._cb_shard
        return btn

    def _btn_reroll(self):
        btn = discord.ui.Button(
            label="Reroll",
            emoji=RR_ICON,
            style=discord.ButtonStyle.blurple,
            custom_id="rr_reroll",
        )
        btn.callback = self._cb_reroll
        return btn

    def _btn_confirm(self):
        btn = discord.ui.Button(
            label="Xác nhận",
            emoji=OK,
            style=discord.ButtonStyle.green,
            custom_id="rr_confirm",
        )
        btn.callback = self._cb_confirm
        return btn

    def _btn_cancel(self):
        btn = discord.ui.Button(
            label="Hủy",
            emoji=ERR,
            style=discord.ButtonStyle.red,
            custom_id="rr_cancel",
        )
        btn.callback = self._cb_cancel
        return btn

    # ── Callbacks ─────────────────────────────────────────────────────────────

    async def _cb_shard(self, interaction: discord.Interaction):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )
        shard_left = self._shard_left()
        await interaction.response.send_message(
            f"{SHARD_ICON} Bạn đang có: **{shard_left}** shard",
            ephemeral=True,
        )

    async def _cb_reroll(self, interaction: discord.Interaction):
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
                        f"{ERR} Không đủ shard! Cần **{cost50}** {SHARD_ICON}, còn **{left}** {SHARD_ICON}.",
                        ephemeral=True,
                    )

                # FIX BUG 1: Trừ shard trực tiếp vào dict thay vì qua remove_item
                # remove_item dùng .get("inv", {}) có thể tạo dict mới không gắn vào user,
                # khiến thay đổi bị mất khi save. Direct access đảm bảo đúng reference.
                user["inv"][SHARD_ID] = user["inv"].get(SHARD_ID, 0) - cost50
                if user["inv"].get(SHARD_ID, 1) <= 0:
                    user["inv"].pop(SHARD_ID, None)

                await save_data(fresh_data, self.uid)
                self.data = fresh_data

            if self.mode == "passive":
                self.new = _rr_roll_passive(self.wi, self.w_data)
            else:
                self.new = _rr_roll_quality(self.w_data)

            self.cost_used += cost50
            self._refresh_buttons()
            await self._edit(interaction)

    async def _cb_confirm(self, interaction: discord.Interaction):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )

        async with self._click_lock:
            async with get_user_lock(self.uid):
                fresh_data = load_data(self.uid)
                user = get_user(self.uid, fresh_data)

                target_wi = next(
                    (w for w in user.get("weapon_instances", [])
                     if isinstance(w, dict) and w.get("uid") == self.wi["uid"]),
                    None,
                )
                if target_wi is None:
                    self._disable_all()
                    self.stop()
                    _active_rr.pop(self.wi["uid"], None)
                    return await interaction.response.edit_message(
                        content=f"{ERR} Vũ khí không còn trong túi đồ.",
                        embed=None, view=self,
                    )

                target_wi[self.mode] = self.new
                await save_data(fresh_data, self.uid)

        # FIX BUG 2: Cập nhật self.wi sau khi save để embed "Đã xác nhận"
        # hiển thị giá trị mới. Nếu không update, _current_embed() đọc self.wi
        # cũ → field "Hiện tại" vẫn hiện giá trị trước reroll → trông như "vẫn giữ nguyên".
        self.wi = {**self.wi, self.mode: self.new}

        self._disable_all()
        self.stop()
        _active_rr.pop(self.wi["uid"], None)

        embed = self._current_embed(label_new="Đã áp dụng")
        embed.title = f"{OK} Đã xác nhận — {self.w_data.get('emoji', '')} {self.w_data['name']}"
        embed.color = discord.Color.green().value
        await interaction.response.edit_message(embed=embed, view=self)

    async def _cb_cancel(self, interaction: discord.Interaction):
        if not self._check_invoker(interaction):
            return await interaction.response.send_message(
                f"{ERR} Chỉ người dùng lệnh mới có thể thao tác.", ephemeral=True
            )

        self._disable_all()
        self.stop()
        _active_rr.pop(self.wi["uid"], None)

        embed = self._current_embed(label_new="Đã hủy")
        embed.title = f"{ERR} Đã hủy — {self.w_data.get('emoji', '')} {self.w_data['name']}"
        embed.color = discord.Color.red().value
        await interaction.response.edit_message(embed=embed, view=self)

    # ── Timeout ───────────────────────────────────────────────────────────────

    async def on_timeout(self):
        _active_rr.pop(self.wi.get("uid", ""), None)
        self._disable_all()
        if self.message:
            try:
                embed = self._current_embed(label_new="Hết giờ")
                embed.title = f"{RR_ICON} Hết giờ — {self.w_data.get('emoji', '')} {self.w_data['name']}"
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN COMMAND HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_reroll(ctx, args: list[str]):
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

        result = _rr_parse_target(user, weapon_arg)
        if isinstance(result, str):
            return await ctx.send(result)
        wi, w_data = result

        if wi["uid"] in _active_rr:
            return await ctx.send(
                f"{ERR} Vũ khí `{wi['uid']}` đang trong phiên reroll chưa kết thúc."
            )

        err = _rr_validate(wi)
        if err:
            return await ctx.send(err)

        cost = _rr_cost(mode, is_reroll=False)
        if not _rr_check_shard(user, cost):
            left = user["inv"].get(SHARD_ID, 0)
            return await ctx.send(
                f"{ERR} Không đủ shard! Cần **{cost}** {SHARD_ICON}, bạn còn **{left}** {SHARD_ICON}."
            )

        old = wi.get(mode)

        # FIX BUG 1 (nhất quán với _cb_reroll): trừ shard trực tiếp
        user["inv"][SHARD_ID] = user["inv"].get(SHARD_ID, 0) - cost
        if user["inv"].get(SHARD_ID, 1) <= 0:
            user["inv"].pop(SHARD_ID, None)

        await save_data(data, uid)

        if mode == "passive":
            new = _rr_roll_passive(wi, w_data)
        else:
            new = _rr_roll_quality(w_data)

        _active_rr[wi["uid"]] = uid

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
        img_attached=True,
    )

    embed = view._current_embed()

    try:
        forge_file = discord.File(FORGE_IMG, filename=FORGE_IMG)
        msg = await ctx.send(file=forge_file, embed=embed, view=view)
    except Exception:
        logger.warning("rpg_forge: không load được %s, gửi không thumbnail", FORGE_IMG)
        embed.set_thumbnail(url=None)
        msg = await ctx.send(embed=embed, view=view)

    view.message = msg


# ══════════════════════════════════════════════════════════════════════════════
#  COG & SETUP
# ══════════════════════════════════════════════════════════════════════════════

class RPGForge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="reroll", aliases=["rr"])
    async def reroll(self, ctx, *args):
        await cmd_reroll(ctx, list(args))


async def setup(bot: commands.Bot):
    await bot.add_cog(RPGForge(bot))
