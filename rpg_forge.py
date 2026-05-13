import discord
from discord.ext import commands

# ── Constants ────────────────────────────────────────────────────────────────

REPAIR_COST_PER_POINT = {
    "common":    2,
    "uncommon":  5,
    "rare":      8,
    "epic":      10,
    "legendary": 18,
    "special":   15,
    "soul":      15,
}

_QUALITY_LABEL_FALLBACK = {
    "very_low":    "Rất Thấp",
    "low":         "Thấp",
    "medium_low":  "Khá Thấp",
    "medium":      "Trung Bình",
    "medium_high": "Khá Cao",
    "high":        "Cao",
    "very_high":   "Rất Cao",
    "extreme":     "Cực Cao",
}

FORGE_IMAGE_PATH = "assets/IMG_forge.png"
COIN_EMOJI       = "<:Coin:1495831576397742241>"
ERR              = "<:X_:1495466670616219819>"
OK               = "<:Tick:1495466684520206528>"
HAMMER_EMOJI     = "<:Hamer:1495462570469888069>"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_quality_label(quality: str) -> str:
    try:
        from rpg_instance import QUALITY_TIERS
        return QUALITY_TIERS.get(quality, {}).get("label", quality)
    except Exception:
        return _QUALITY_LABEL_FALLBACK.get(quality, quality)


# INTEGRITY: Sentinel returned when an instance fails validation.
# Callers must check `cost is None` to detect refused repair.
_CORRUPT = None


def _validate_instance(wi: dict) -> str | None:
    """
    Verify that a weapon instance has the required durability fields and that
    their values form a coherent state.

    Returns None if the instance is valid.
    Returns a non-empty error string describing the problem if it is not.

    Rules:
    - wi must be a non-empty dict (empty dict == missing instance)
    - "durability" key must be explicitly present (no fabricated default)
    - "durability_max" key must be explicitly present (no fabricated default)
    - durability_max must be an int >= 1
    - durability must be an int in [0, durability_max]
    """
    if not wi:
        return "instance missing or empty"

    if "durability_max" not in wi:
        return "durability_max field absent"

    if "durability" not in wi:
        return "durability field absent"

    dur_max = wi["durability_max"]
    dur     = wi["durability"]

    if not isinstance(dur_max, int) or dur_max < 1:
        return f"durability_max invalid ({dur_max!r})"

    if not isinstance(dur, int) or dur < 0:
        return f"durability invalid ({dur!r})"

    if dur > dur_max:
        return f"durability ({dur}) exceeds durability_max ({dur_max}) — inverted state"

    return None  # valid


def _calc_repair_cost(wi: dict, w_data: dict) -> int | None:
    """
    Tính giá repair 1 weapon instance.
    Trả về 0 nếu không cần repair.
    Trả về None nếu instance không hợp lệ — caller phải từ chối repair.

    INTEGRITY: We never fabricate durability values.
    If either durability field is absent or inconsistent, we refuse with None
    rather than computing a cost from guessed state.
    """
    err = _validate_instance(wi)
    if err:
        # Do NOT fall through with defaults — return sentinel so caller aborts.
        print(f"[FORGE INTEGRITY] _calc_repair_cost refused: {err} | wi={wi!r}")
        return _CORRUPT

    broken  = wi.get("broken", False)
    dur     = wi["durability"]      # guaranteed present by _validate_instance
    dur_max = wi["durability_max"]  # guaranteed present and >= 1
    missing = dur_max - dur

    if missing <= 0 and not broken:
        return 0

    rarity  = w_data.get("rarity", "common") if w_data else "common"
    cost_pp = REPAIR_COST_PER_POINT.get(rarity, 50)
    return max(missing, 1) * cost_pp


def _build_forge_embed(
    slots_info: list,
    total_cost: int,
    author_name: str,
) -> discord.Embed:
    """
    slots_info: list gồm tối đa 3 phần tử, mỗi phần tử là dict:
        {
            "slot": int (1/2/3),
            "uid": str | None,
            "name": str,
            "emoji": str,
            "level": int,
            "quality_label": str,
            "durability": int,
            "durability_max": int,
            "cost": int,
            "broken": bool,
            "needs_repair": bool,
            "corrupt": bool,   # NEW — True if instance failed integrity check
        }
    total_cost: tổng giá repair tất cả (only from valid instances)
    author_name: display name của user
    """
    embed = discord.Embed(
        title=f"{HAMMER_EMOJI} Repair Weapon",
        description=f"**{author_name}** — Xưởng rèn",
        color=0xFF6B35,
    )
    embed.set_thumbnail(url="attachment://IMG_forge.png")

    for s in slots_info:
        slot_header = f"Ô [{s['slot']}]"

        if s["uid"] is None:
            embed.add_field(
                name=slot_header,
                value="🔲 | _Trống_",
                inline=False,
            )
            continue

        # INTEGRITY: surface corrupt slots visibly so the player knows.
        if s.get("corrupt"):
            embed.add_field(
                name=slot_header,
                value=(
                    f"⚠️ **[DỮ LIỆU LỖI]** — `{s['uid']}`\n"
                    f"_Không thể xác minh trạng thái vũ khí này. "
                    f"Liên hệ admin._"
                ),
                inline=False,
            )
            continue

        dur     = s["durability"]
        dur_max = s["durability_max"]
        filled  = int(dur / max(dur_max, 1) * 10)
        bar     = "█" * filled + "░" * (10 - filled)
        broken_tag = " ⚠️ | **HỎng**" if s["broken"] else ""
        cost_text  = f"**{s['cost']:,}** {COIN_EMOJI}" if s["needs_repair"] else "_Không cần repair_"

        embed.add_field(
            name=slot_header,
            value=(
                f"{s['emoji']} **{s['name']}**{broken_tag}\n"
                f"Lv **{s['level']}** │ Bậc: **{s['quality_label']}**\n"
                f"Độ bền: `{bar}` {dur}/{dur_max}\n"
                f"-# `{s['uid']}`\n"
                f"Chi phí: {cost_text}"
            ),
            inline=False,
        )

    if total_cost > 0:
        embed.add_field(
            name="Tổng chi phí",
            value=f"**{total_cost:,}** {COIN_EMOJI}",
            inline=False,
        )
        embed.set_footer(text="Hết hạn sau 30 giây")
    else:
        embed.set_footer(text="Tất cả vũ khí đang trong trạng thái tốt!")

    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class RPGForge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="repair", aliases=["re"])
    async def repair(self, ctx):
        from rpg_core     import get_base_id
        from rpg_weapon_data   import get_weapon_by_id
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        equipped = user.get("equipped", [None, None, None])
        if not isinstance(equipped, list) or len(equipped) != 3:
            equipped = [None, None, None]

        # Tất cả slot đều trống → báo sớm
        if not any(equipped):
            return await ctx.send(
                f"{ERR} | Bạn chưa trang bị vũ khí nào."
            )

        # INTEGRITY: Build wi_map from explicit uid keys only.
        # A missing uid means the instance cannot be addressed — exclude it.
        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # ── Build slots_info ──────────────────────────────────────────
        slots_info = []
        total_cost = 0

        for slot_idx, uid in enumerate(equipped, 1):
            if not uid:
                slots_info.append({
                    "slot":           slot_idx,
                    "uid":            None,
                    "needs_repair":   False,
                    "cost":           0,
                    "name":           "",
                    "emoji":          "",
                    "level":          1,
                    "quality_label":  "",
                    "durability":     0,
                    "durability_max": 0,
                    "broken":         False,
                    "corrupt":        False,
                })
                continue

            # INTEGRITY: Look up the instance. A missing entry is not a valid
            # "no damage" state — it is a corrupt/missing instance. We do NOT
            # fall back to {} and silently continue.
            wi = wi_map.get(uid)  # None if absent, not {}
            if wi is None:
                print(
                    f"[FORGE INTEGRITY] uid={uid!r} equipped in slot {slot_idx} "
                    f"but has no weapon_instance record. Marking corrupt."
                )
                slots_info.append({
                    "slot":           slot_idx,
                    "uid":            uid,
                    "needs_repair":   False,
                    "cost":           0,
                    "name":           uid,
                    "emoji":          "⚔️",
                    "level":          1,
                    "quality_label":  "???",
                    "durability":     0,
                    "durability_max": 0,
                    "broken":         False,
                    "corrupt":        True,  # surfaced to player in embed
                })
                continue

            b_id = get_base_id(str(uid)) or str(uid)
            w    = get_weapon_by_id(b_id)

            nm     = w["name"]  if w else b_id
            em     = w["emoji"] if w else "⚔️"
            level  = wi.get("level", 1)
            q_key  = wi.get("quality", "medium")
            q_lbl  = _get_quality_label(q_key)

            # INTEGRITY: _calc_repair_cost returns None on invalid state.
            # We must treat None as "refuse this slot", not as cost=0.
            cost = _calc_repair_cost(wi, w)
            if cost is _CORRUPT:
                print(
                    f"[FORGE INTEGRITY] uid={uid!r} failed durability validation. "
                    f"Slot {slot_idx} marked corrupt."
                )
                slots_info.append({
                    "slot":           slot_idx,
                    "uid":            uid,
                    "needs_repair":   False,
                    "cost":           0,
                    "name":           nm,
                    "emoji":          em,
                    "level":          level,
                    "quality_label":  q_lbl,
                    "durability":     0,
                    "durability_max": 0,
                    "broken":         wi.get("broken", False),
                    "corrupt":        True,
                })
                continue

            # Instance is valid — use its real field values only.
            dur    = wi["durability"]      # guaranteed by _validate_instance
            dur_mx = wi["durability_max"]  # guaranteed by _validate_instance
            broken = wi.get("broken", False)

            needs_repair = cost > 0
            total_cost  += cost

            slots_info.append({
                "slot":           slot_idx,
                "uid":            uid,
                "name":           nm,
                "emoji":          em,
                "level":          level,
                "quality_label":  q_lbl,
                "durability":     dur,
                "durability_max": dur_mx,
                "cost":           cost,
                "broken":         broken,
                "needs_repair":   needs_repair,
                "corrupt":        False,
            })

        # ── Không có gì cần repair ────────────────────────────────────
        if total_cost == 0:
            embed = _build_forge_embed(slots_info, 0, ctx.author.display_name)
            try:
                file = discord.File(FORGE_IMAGE_PATH, filename="IMG_forge.png")
                return await ctx.send(embed=embed, file=file)
            except Exception:
                return await ctx.send(embed=embed)

        # ── Không đủ tiền ─────────────────────────────────────────────
        # INTEGRITY: Read from "cash" only — canonical currency field.
        # Do not alias "gold", "coins", or any other key.
        balance = user.get("cash", 0)
        if balance < total_cost:
            embed = _build_forge_embed(
                slots_info, total_cost, ctx.author.display_name
            )
            embed.color = 0xFF0000
            embed.description = (
                f"**{ctx.author.display_name}** — Không đủ tiền!\n"
                f"Cần: **{total_cost:,}** {COIN_EMOJI} │ "
                f"Có: **{balance:,}** {COIN_EMOJI}"
            )
            try:
                file = discord.File(FORGE_IMAGE_PATH, filename="IMG_forge.png")
                return await ctx.send(embed=embed, file=file)
            except Exception:
                return await ctx.send(embed=embed)

        # ── Embed xác nhận ────────────────────────────────────────────
        embed = _build_forge_embed(
            slots_info, total_cost, ctx.author.display_name
        )

        # ── Confirm View ──────────────────────────────────────────────
        class ForgeConfirmView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)
                self_v.confirmed = None
                self_v.message   = None

            async def on_timeout(self_v):
                for child in self_v.children:
                    child.disabled = True
                try:
                    await self_v.message.edit(
                        content="⏰ | Hết thời gian — đã huỷ.",
                        embed=None,
                        view=self_v,
                    )
                except Exception:
                    pass

            @discord.ui.button(
                emoji=discord.PartialEmoji.from_str("<:Tick:1495466684520206528>"),
                label="Xác nhận",
                style=discord.ButtonStyle.success,
            )
            async def btn_confirm(self_v, interaction: discord.Interaction, _btn):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải lệnh của bạn.", ephemeral=True
                    )
                self_v.confirmed = True
                self_v.stop()
                await interaction.response.defer()

            @discord.ui.button(
                emoji=discord.PartialEmoji.from_str("<:X_:1495466670616219819>"),
                label="Huỷ",
                style=discord.ButtonStyle.danger,
            )
            async def btn_cancel(self_v, interaction: discord.Interaction, _btn):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải lệnh của bạn.", ephemeral=True
                    )
                self_v.confirmed = False
                self_v.stop()
                await interaction.response.defer()

        view = ForgeConfirmView()
        try:
            file = discord.File(FORGE_IMAGE_PATH, filename="IMG_forge.png")
            view.message = await ctx.send(embed=embed, file=file, view=view)
        except Exception:
            view.message = await ctx.send(embed=embed, view=view)

        await view.wait()

        # ── Huỷ ──────────────────────────────────────────────────────
        if not view.confirmed:
            for child in view.children:
                child.disabled = True
            return await view.message.edit(
                content=f"{ERR} | Đã huỷ repair.",
                embed=None,
                view=view,
            )

        # ── Re-fetch tránh race condition ─────────────────────────────
        user, _ = get_user(author_uid)

        # INTEGRITY: Re-build wi_map from the freshly fetched user document.
        # The pre-confirm wi_map is stale — do not reuse it here.
        wi_map_fresh = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # INTEGRITY: Re-validate balance against canonical "cash" field only.
        if user.get("cash", 0) < total_cost:
            for child in view.children:
                child.disabled = True
            return await view.message.edit(
                content=f"{ERR} | Không đủ tiền! Cần **{total_cost:,}** {COIN_EMOJI}.",
                embed=None,
                view=view,
            )

        # ── Restore durability ────────────────────────────────────────
        # INTEGRITY: Only charge if repair actually occurs.
        # Count expected repairs from slots_info (built from validated data),
        # then confirm each one lands in the fresh instance list before deducting.
        expected_repairs = [s for s in slots_info if s["needs_repair"] and s["uid"]]
        repaired_count   = 0

        for s in expected_repairs:
            uid = s["uid"]
            wi_live = wi_map_fresh.get(uid)

            if wi_live is None:
                # Instance vanished between confirm and now — do not repair or charge.
                print(
                    f"[FORGE INTEGRITY] uid={uid!r} present in pre-confirm snapshot "
                    f"but missing after re-fetch. Skipping repair for this slot."
                )
                continue

            # Re-validate the live instance before writing to it.
            err = _validate_instance(wi_live)
            if err:
                print(
                    f"[FORGE INTEGRITY] uid={uid!r} failed post-confirm validation: "
                    f"{err}. Skipping repair for this slot."
                )
                continue

            # Safe to repair — durability_max is confirmed present and valid.
            wi_live["durability"] = wi_live["durability_max"]
            wi_live["broken"]     = False
            repaired_count       += 1

        # INTEGRITY: Do not charge if no repair actually occurred.
        if repaired_count == 0:
            for child in view.children:
                child.disabled = True
            print(
                f"[FORGE INTEGRITY] user={author_uid} confirmed repair but "
                f"repaired_count=0. Aborting charge."
            )
            return await view.message.edit(
                content=(
                    f"{ERR} | Không thể repair — dữ liệu vũ khí không hợp lệ. "
                    f"Không trừ tiền."
                ),
                embed=None,
                view=view,
            )

        # INTEGRITY: Charge proportionally if only some slots were repairable.
        # Re-compute cost from the slots that were actually repaired.
        if repaired_count < len(expected_repairs):
            repaired_uids    = {s["uid"] for s in expected_repairs[:repaired_count]}
            actual_cost      = sum(
                s["cost"] for s in expected_repairs
                if s["uid"] in repaired_uids
            )
            print(
                f"[FORGE INTEGRITY] user={author_uid} partial repair: "
                f"expected={len(expected_repairs)} actual={repaired_count}. "
                f"Charging {actual_cost} instead of {total_cost}."
            )
        else:
            actual_cost = total_cost

        # Deduct canonical currency field only.
        user["cash"] = user.get("cash", 0) - actual_cost

        if not save_user(author_uid, user):
            for child in view.children:
                child.disabled = True
            return await view.message.edit(
                content=f"{ERR} | Lỗi lưu dữ liệu — không có gì thay đổi.",
                embed=None,
                view=view,
            )

        for child in view.children:
            child.disabled = True

        await view.message.edit(
            content=(
                f"{OK} | Đã repair **{repaired_count}** vũ khí "
                f"— mất **{actual_cost:,}** {COIN_EMOJI}"
            ),
            embed=None,
            view=view,
        )


# ── Setup ─────────────────────────────────────────────────────────────────────

async def setup(bot):
    await bot.add_cog(RPGForge(bot))
