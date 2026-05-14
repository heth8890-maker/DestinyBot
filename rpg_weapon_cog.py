"""
===== FILE: rpg_weapon_cog.py =====
Chứa: class RPGWeapon(commands.Cog) + setup().
Import toàn bộ data/helpers từ rpg_weapon_data.py.

Tách từ rpg_weapon.py để tránh phình to.

⚡ Patches áp dụng (từ rpg_weapon_audit.md):
  - PATCH 4: display_weapon_info — detect instance_missing, guard fmt_instance_info,
             guard EXP bar, không fabricate {} default
  - PATCH 5: weapon <uid> detail — guard fmt_instance_info empty return,
             guard exp_to_next <= 0 (tránh fake 100% bar)
"""

import random

import discord
from discord.ext import commands

from rpg_weapon_data import (
    RARITY_COLOR,
    RARITY_LABEL,
    COIN_EMOJI,
    ERR,
    OK,
    get_weapon_by_id,
    parse_rarity_alias,
    get_weapon_sell_price,
    get_sell_candidates,
    build_bulk_sell_embed,
    calculate_combined_effects,
    _fmt_effects_scaled,
    _fmt_combined_effects,
    _rarity_tier,
)
from rpg_instance import resolve_passive


# ═══════════════════════════════════════════════════════════
# COG: WEAPON
# ═══════════════════════════════════════════════════════════

class RPGWeapon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─────────────────────────────────────────────────────────
    # HELPER: build weapon line cho list
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _weapon_line(wid: str, wi_map: dict, equipped_set: set,
                     get_base_id_fn, get_weapon_by_id_fn) -> str:
        base_id = get_base_id_fn(str(wid)) or str(wid)
        w       = get_weapon_by_id_fn(base_id)
        nm      = w["name"]  if w else base_id
        em      = w["emoji"] if w else "⚔️"
        wi      = wi_map.get(wid)
        lv      = wi.get("level", 1) if wi else 1
        eq_tag  = " **[E]**" if wid in equipped_set else ""
        p       = resolve_passive(wi) if isinstance(wi, dict) else {}
        p_tag   = f" {p.get('emoji', '')} {p.get('name', '')}" if p.get("id") else ""
        return f"{em} **{nm}**{p_tag}{eq_tag} • Lv {lv}\n`{wid}`"

    # ─────────────────────────────────────────────────────────
    # MAIN: dtn weapon / dtn weapon <uid>
    # ─────────────────────────────────────────────────────────
    @commands.group(name="weapon", aliases=["w"], invoke_without_command=True)
    async def weapon(self, ctx, *, weapon_id: str = None):
        from rpg_core import WeaponID, get_weapon_entity, get_base_id
        from rpg_database import get_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # ── dtn weapon <uid> — chi tiết ──────────────────────────────
        if weapon_id:
            base_id = get_base_id(weapon_id) or weapon_id
            w       = get_weapon_by_id(base_id)
            entity  = get_weapon_entity(user, weapon_id)

            if not entity and not w:
                return await ctx.send(
                    f"{ERR} | Không tìm thấy vũ khí `{weapon_id}`."
                )

            embed = entity.build_embed() if entity else discord.Embed(
                title=f"⚔️ {weapon_id}",
                color=0xE91E63,
            )

            # Trạng thái equip
            equipped     = user.get("equipped", [])
            equip_status = "—"
            for i, wid in enumerate(equipped, 1):
                if wid == weapon_id:
                    equip_status = f"Ô **[{i}]**"
                    break
            embed.add_field(
                name="️ Trạng thái",
                value=equip_status,
                inline=True,
            )

            # ── PATCH 5: Level / EXP — guard missing instance & corrupt exp_to_next ──
            wi = wi_map.get(weapon_id)  # None = no instance record
            if wi is not None:
                from rpg_instance import fmt_instance_info
                level    = wi.get("level", 1)
                exp      = wi.get("exp", 0)
                exp_next = wi.get("exp_to_next", 40)

                # Guard corrupt exp_to_next — avoid a fake 100% bar
                if exp_next <= 0:
                    embed.add_field(
                        name="⚠️ Level & EXP",
                        value=f"-# EXP data corrupt: exp_to_next={exp_next}",
                        inline=False,
                    )
                else:
                    filled   = int(exp / exp_next * 20)
                    bar      = "█" * filled + "░" * (20 - filled)
                    pct      = int(exp / exp_next * 100)
                    scale    = round(0.60 + (level - 1) * 0.02857, 3)
                    cap_note = " _(Max!)_" if level >= 50 else ""
                    embed.add_field(
                        name=" Level & EXP",
                        value=(
                            f"**Lv {level}** / 50{cap_note}\n"
                            f"`{bar}` {exp:,} / {exp_next:,} ({pct}%)\n"
                            f"Effect: **{scale:.0%}** base"
                        ),
                        inline=False,
                    )

                # SAFETY: guard fmt_instance_info early-return
                # Empty string = function detected bad state and returned nothing.
                # Surface that explicitly instead of hiding the field.
                instance_text = fmt_instance_info(wi)
                if instance_text and instance_text.strip():
                    embed.add_field(
                        name="⚙️ Chi tiết",
                        value=instance_text,
                        inline=False,
                    )
                else:
                    # fmt_instance_info returned empty — do NOT silently skip
                    embed.add_field(
                        name="⚙️ Chi tiết",
                        value="-# ⚠️ Instance info returned empty — state may be corrupt",
                        inline=False,
                    )
            else:
                # No instance record exists at all for this weapon UID.
                # Show explicit warning — do NOT fabricate Lv1 defaults.
                embed.add_field(
                    name="⚠️ Instance Record",
                    value=(
                        "-# No instance record found for this UID.\n"
                        "-# Level, EXP, and passive effects are unknown.\n"
                        "-# Use `dtn weapon unequip` then re-equip to attempt recovery."
                    ),
                    inline=False,
                )

            # UID + lệnh nhanh
            embed.add_field(
                name=" UID",
                value=f"`{weapon_id}`",
                inline=False,
            )
            embed.add_field(
                name=" Lệnh nhanh",
                value=(
                    f"`dtn weapon equip {weapon_id}`\n"
                    f"`dtn weapon unequip <slot>`"
                ),
                inline=False,
            )
            if w and w.get("rarity") != "special":
                embed.add_field(
                    name="<:Key:1496098633395998740> Tỉ lệ crate",
                    value=f"**{w['chance']}%**",
                    inline=True,
                )
            embed.set_footer(text=f"UID: {weapon_id}")
            return await ctx.send(embed=embed)

        # ── dtn weapon (no args) — danh sách tất cả ─────────────────
        equipped     = user.get("equipped", [None, None, None])
        equipped_set = {w for w in equipped if w}
        all_weapons  = list(equipped_set) + [
            w for w in user.get("weapons", [])
            if w not in equipped_set
        ]

        if not all_weapons:
            return await ctx.send(
                f"<:Hamer:1495462570469888069> **{ctx.author.display_name}** "
                f"chưa có vũ khí nào."
            )

        # Build lines
        lines = []
        for wid in all_weapons:
            lines.append(
                self._weapon_line(
                    wid, wi_map, equipped_set,
                    get_base_id, get_weapon_by_id,
                )
            )

        # Pagination — 16 weapon/trang
        PAGE_SIZE   = 16
        pages       = [lines[i:i+PAGE_SIZE] for i in range(0, len(lines), PAGE_SIZE)]
        total_pages = len(pages)

        def build_embed(page_idx: int) -> discord.Embed:
            e = discord.Embed(
                title=(
                    f"<:Hamer:1495462570469888069> "
                    f"Weapon của {ctx.author.display_name}"
                ),
                description="\n\n".join(pages[page_idx]),
                color=0xE91E63,
            )
            e.set_footer(
                text=(
                    f"Trang {page_idx+1}/{total_pages}  │  "
                    f"[E] = đang trang bị  │  "
                    f"dtn weapon <uid> để xem chi tiết"
                )
            )
            return e

        if total_pages == 1:
            return await ctx.send(embed=build_embed(0))

        # Multi-page
        class WeaponPages(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.page = 0
                self.message = None  # FIX 2: lưu message ref để disable khi timeout

            async def on_timeout(self):  # FIX 2: disable buttons khi timeout
                for child in self.children:
                    child.disabled = True
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải danh sách của bạn.", ephemeral=True
                    )  # FIX 2: ephemeral thay vì silent defer
                self.page = (self.page - 1) % total_pages
                await interaction.response.edit_message(
                    embed=build_embed(self.page)
                )

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction,
                           button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(
                        "Đây không phải danh sách của bạn.", ephemeral=True
                    )  # FIX 2: ephemeral thay vì silent defer
                self.page = (self.page + 1) % total_pages
                await interaction.response.edit_message(
                    embed=build_embed(self.page)
                )

        view = WeaponPages()  # FIX 2: lưu view để gán message ref
        view.message = await ctx.send(embed=build_embed(0), view=view)

    # ─────────────────────────────────────────────────────────
    # EQUIP
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="equip", aliases=["e", "eq"])
    async def weapon_equip(self, ctx, weapon_id: str, slot: int = None):
        from rpg_core import equip_weapon, WeaponID
        from rpg_quest import add_quest_progress
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        base_id, _ = WeaponID.parse(weapon_id)
        w_new      = get_weapon_by_id(base_id)

        # Chặn equip trùng base_id
        for i, wid in enumerate(user.get("equipped", []), 1):
            if wid is None:
                continue
            existing_base, _ = WeaponID.parse(str(wid))
            if existing_base == base_id:
                return await ctx.send(
                    f"{ERR} | Đã có **{w_new['name'] if w_new else base_id}** "
                    f"ở ô [{i}]. Dùng `dtn weapon unequip {i}` trước."
                )

        ok, msg = equip_weapon(user, weapon_id, slot)
        if ok:
            if not save_user(author_uid, user):  # FIX 3: check return value
                return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Thử lại.")
            slot_used = next(
                (i+1 for i, wid in enumerate(user["equipped"])
                 if wid == weapon_id), "?"
            )
            name = w_new["name"] if w_new else weapon_id
            add_quest_progress(ctx.author.id, "weapons_equipped")
            await ctx.send(
                f"{OK} | Đã trang bị **{name}** vào ô **[{slot_used}]**.\n"
                f"-# `{weapon_id}`"
            )
        else:
            await ctx.send(f"{ERR} | {msg}")

    # ─────────────────────────────────────────────────────────
    # UNEQUIP
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="unequip", aliases=["ue", "une"])
    async def weapon_unequip(self, ctx, slot: int):
        from rpg_core import unequip_weapon, WeaponID
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        ok, result = unequip_weapon(user, slot)
        if ok:
            if not save_user(author_uid, user):  # FIX 3: check return value
                return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Thử lại.")
            base_id, _ = WeaponID.parse(result)
            w    = get_weapon_by_id(base_id)
            name = w["name"] if w else result
            await ctx.send(
                f"{OK} | Đã bỏ trang bị ô **[{slot}]**: "
                f"**{name}** về kho.\n-# `{result}`"
            )
        else:
            await ctx.send(f"{ERR} | {result}")

    # ─────────────────────────────────────────────────────────
    # PATCH B — SELL: Bulk sell theo base_id hoặc rarity
    # Cú pháp:
    #   dtn weapon sell <base_id> [<amount>|all]
    #   dtn weapon sell <rarity>  (vd: r, rare, legend, l, epic)
    # ─────────────────────────────────────────────────────────
    @weapon.command(name="sell", aliases=["s"])
    async def weapon_sell(self, ctx, arg1: str = None, arg2: str = None):
        from rpg_database import get_user, save_user

        if arg1 is None:
            return await ctx.send(
                f"{ERR} | **Cú pháp:**\n"
                f"`dtn weapon sell <base_id> <số lượng|all>`\n"
                f"`dtn weapon sell <rarity>`  _(vd: r, rare, legend, l, epic)_"
            )

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        # ── Parse loại sell ──────────────────────────────────
        rarity_target  = None
        base_id_target = None
        amount         = None   # None = bán tất cả

        parsed_rarity = parse_rarity_alias(arg1)

        if parsed_rarity:
            # Sell by rarity — không nhận arg2
            rarity_target = parsed_rarity
            if arg2 is not None:
                return await ctx.send(
                    f"{ERR} | Sell theo rarity không cần thêm tham số. "
                    f"Dùng `dtn weapon sell {arg1}`."
                )

        elif arg1.isdigit():
            # Sell by base_id
            base_id_target = arg1
            if arg2 is None or arg2.lower() == "all":
                amount = None
            elif arg2.isdigit() and int(arg2) > 0:
                amount = int(arg2)
            else:
                return await ctx.send(
                    f"{ERR} | Số lượng không hợp lệ: `{arg2}`.\n"
                    f"Dùng số nguyên dương hoặc `all`."
                )

        else:
            return await ctx.send(
                f"{ERR} | Không nhận ra `{arg1}`.\n"
                f"Nhập **base_id** _(vd: `463`)_ hoặc "
                f"**rarity** _(vd: `rare`, `r`, `legend`)_."
            )

        # ── Lấy candidates (1 lần duy nhất) ─────────────────
        candidates = get_sell_candidates(
            user,
            base_id=base_id_target,
            rarity=rarity_target,
        )

        # ── Safety checks ─────────────────────────────────────
        if not candidates:
            if base_id_target:
                w_info = get_weapon_by_id(base_id_target)
                nm = w_info["name"] if w_info else base_id_target
                return await ctx.send(
                    f"{ERR} | Không có **{nm}** (`{base_id_target}`) nào có thể bán.\n"
                    f"-# _(Có thể đang equip hết hoặc chưa có trong kho)_"
                )
            rlabel = RARITY_LABEL.get(rarity_target, rarity_target)
            return await ctx.send(
                f"{ERR} | Không có weapon **{rlabel}** nào có thể bán."
            )

        # Sort: level thấp → cao, tie-break random
        candidates.sort(key=lambda c: (c["level"], random.random()))

        if amount is not None:
            if amount > len(candidates):
                return await ctx.send(
                    f"{ERR} | Chỉ có **{len(candidates)}** weapon hợp lệ, "
                    f"không đủ **{amount}** để bán."
                )
            candidates = candidates[:amount]

        # ── Build embed preview ──────────────────────────────
        if base_id_target:
            w_info   = get_weapon_by_id(base_id_target)
            nm       = w_info["name"] if w_info else base_id_target
            color    = RARITY_COLOR.get(
                w_info.get("rarity", "common"), 0xFFA500
            ) if w_info else 0xFFA500
            title_ex = f"\n{nm} (ID: {base_id_target})"
        else:
            color    = RARITY_COLOR.get(rarity_target, 0xFFA500)
            title_ex = f"\n{RARITY_LABEL.get(rarity_target, rarity_target)}"

        embed = build_bulk_sell_embed(candidates, title_extra=title_ex, color=color)

        # ── Confirm View ──────────────────────────────────────
        class ConfirmSellView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)
                self_v.confirmed = None
                self_v.message   = None

            async def on_timeout(self_v):
                for child in self_v.children:
                    child.disabled = True
                try:
                    await self_v.message.edit(
                        content="⏰ Hết thời gian — đã huỷ.",
                        embed=None, view=self_v,
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

        view         = ConfirmSellView()
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

        # ── Huỷ ──────────────────────────────────────────────
        if not view.confirmed:
            for child in view.children:
                child.disabled = True
            return await view.message.edit(
                content=f"{ERR} | Đã huỷ bán.", embed=None, view=view,
            )

        # ── Thực hiện bán (re-fetch tránh race condition) ─────
        user, _ = get_user(author_uid)

        current_bag = set(user.get("weapons", []))
        sold_uids   = {c["uid"] for c in candidates if c["uid"] in current_bag}

        if not sold_uids:
            return await view.message.edit(
                content=f"{ERR} | Weapon đã không còn trong kho.",
                embed=None, view=None,
            )

        actual_total = sum(c["price"] for c in candidates if c["uid"] in sold_uids)

        user["weapons"] = [
            w for w in user.get("weapons", []) if w not in sold_uids
        ]
        user["weapon_instances"] = [
            wi for wi in user.get("weapon_instances", [])
            if not (isinstance(wi, dict) and wi.get("uid") in sold_uids)
        ]
        user["cash"] = user.get("cash", 0) + actual_total

        if not save_user(author_uid, user):
            return await view.message.edit(
                content=f"{ERR} | Lỗi lưu dữ liệu — không có gì bị bán.",
                embed=None, view=None,
            )

        for child in view.children:
            child.disabled = True

        await view.message.edit(
            content=(
                f"{OK} | Đã bán **{len(sold_uids)}** weapon "
                f"— nhận **{actual_total:,}** {COIN_EMOJI}"
            ),
            embed=None, view=view,
        )

    # ─────────────────────────────────────────────────────────
    # GIVE WEAPON (admin)
    # ─────────────────────────────────────────────────────────
    @commands.command(name="givew")
    @commands.is_owner()
    async def give_weapon(self, ctx, member: discord.Member, weapon_id: str):
        from rpg_core import add_weapon, get_weapon_entity
        from rpg_database import get_user, save_user

        w = get_weapon_by_id(weapon_id)
        if not w:
            return await ctx.send(
                f"{ERR} | Không tìm thấy vũ khí ID `{weapon_id}`."
            )

        user, _ = get_user(str(member.id))
        new_uid = add_weapon(user, weapon_id)
        save_user(str(member.id), user)

        entity       = get_weapon_entity(user, new_uid)
        rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)
        display_name = entity.fmt_name() if entity else f"`{new_uid}`"
        stats_value  = entity.fmt_stats() if entity else "—"

        embed = discord.Embed(
            title=f"{OK} | Trao tặng vũ khí",
            description=(
                f"**Creator** đã trao cho {member.mention}:\n"
                f"{display_name}"
            ),
            color=rarity_color,
        )
        embed.add_field(
            name="<:Effect:1495466103047061679> Chỉ số",
            value=stats_value,
            inline=False,
        )
        embed.add_field(
            name=" Mô tả",
            value=w.get("description", "—"),
            inline=False,
        )
        embed.add_field(
            name="<:Key:1496098633395998740> Độ hiếm",
            value=_rarity_tier(w.get("rarity", "common")),
            inline=True,
        )
        embed.add_field(
            name="UID",
            value=f"`{new_uid}`",
            inline=False,
        )
        embed.set_footer(text=f"Trao bởi {ctx.author}")
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────
    # WID — danh sách text để copy ID
    # ─────────────────────────────────────────────────────────
    @commands.command(name="wid")
    async def weapon_id_list(self, ctx):
        from rpg_core import get_base_id
        from rpg_database import get_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        wi_map       = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }
        equipped     = user.get("equipped", [])
        equipped_set = {w for w in equipped if w}

        lines = [f"**=== VŨ KHÍ CỦA {ctx.author.display_name.upper()} ===**"]

        all_weapons = list(equipped_set) + [
            w for w in user.get("weapons", []) if w not in equipped_set
        ]

        if not all_weapons:
            lines.append("_(Không có vũ khí)_")
        else:
            for wid in all_weapons:
                base_id = get_base_id(str(wid)) or str(wid)
                w       = get_weapon_by_id(base_id)
                nm      = w["name"]  if w else base_id
                em      = w["emoji"] if w else "⚔️"
                wi      = wi_map.get(wid)
                lv      = wi.get("level", 1) if wi else 1
                eq_tag  = " [EQUIPPED]" if wid in equipped_set else ""
                lines.append(f"{em} **{nm}** Lv{lv}{eq_tag}")
                lines.append(f"`{wid}`")

        full_text = "\n".join(lines)
        parts = [full_text[i:i+1900] for i in range(0, len(full_text), 1900)]
        for part in parts:
            await ctx.send(part)

    # ─────────────────────────────────────────────────────────
    # PATCH C + PATCH 4 — DWI / DWE: Hiển thị 3 weapon equip + combined effects
    # PATCH 4: detect instance_missing, guard fmt_instance_info, guard EXP bar
    # ─────────────────────────────────────────────────────────
    @commands.command(name="wi", aliases=["myw", "myweapon"])
    async def display_weapon_info(self, ctx):
        from rpg_core import get_base_id, get_weapon_entity
        from rpg_database import get_user
        from rpg_instance import fmt_instance_info

        author_uid   = str(ctx.author.id)
        user, _      = get_user(author_uid)
        equipped_raw = user.get("equipped", [None, None, None])

        if not any(equipped_raw):
            return await ctx.send(
                f"<:Hamer:1495462570469888069> "
                f"**{ctx.author.display_name}** chưa trang bị weapon nào.\n"
                f"-# Dùng `dtn weapon equip <uid>` để trang bị."
            )

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        embed = discord.Embed(
            title=(
                f"<:Hamer:1495462570469888069> "
                f"Weapon trang bị — {ctx.author.display_name}"
            ),
            color=0xE91E63,
        )

        for slot_idx, uid in enumerate(equipped_raw[:3], 1):
            slot_header = f"Ô [{slot_idx}]"

            if not uid:
                embed.add_field(
                    name=slot_header,
                    value="`🔲 None`",
                    inline=False,
                )
                continue

            b_id = get_base_id(str(uid)) or str(uid)
            w    = get_weapon_by_id(b_id)

            # PATCH 4 SAFETY: explicitly distinguish missing vs present instance.
            # Never use {} as a fallback — that fabricates a fake Lv1 record.
            wi               = wi_map.get(uid)       # None = genuinely absent
            instance_missing = wi is None
            wi_safe          = wi or {}              # only for .get() calls below
            level            = wi_safe.get("level", 1)

            if not w:
                embed.add_field(
                    name=slot_header,
                    value=(
                        f"`{uid}`\n"
                        f"-# ⚠️ Weapon definition not found for base_id `{b_id}`"
                    ),
                    inline=False,
                )
                continue

            rarity  = w.get("rarity", "common")
            rlabel  = RARITY_LABEL.get(rarity, rarity)
            em      = w.get("emoji", "⚔️")
            nm      = w.get("name", b_id)
            effects = w.get("effects", {})

            # Pass instance_missing so effect formatter can flag approx values
            # and exclude unverifiable passives
            effect_lines = _fmt_effects_scaled(
                effects, level, instance_missing=instance_missing
            )

            # ── EXP bar — only render if instance record is present ──
            if instance_missing:
                # Do NOT fabricate a 0/40 bar; that implies a valid fresh weapon
                exp_bar_line = "-# ⚠️ Instance record not found — level & EXP unknown"
            else:
                exp      = wi_safe.get("exp", 0)
                exp_next = wi_safe.get("exp_to_next", 40)
                # Guard against corrupt exp_to_next = 0 (would produce a full bar)
                if exp_next <= 0:
                    exp_bar_line = f"-# ⚠️ EXP data corrupt (exp_to_next={exp_next})"
                else:
                    filled   = int(exp / exp_next * 10)
                    bar      = "█" * filled + "░" * (10 - filled)
                    exp_bar_line = f"Lv **{level}** / 50 │ `{bar}` {exp}/{exp_next} EXP"

            # ── fmt_instance_info guard ──────────────────────────────────
            # fmt_instance_info may early-return "" for bad/empty state.
            # Never insert a raw empty string or None into field_parts.
            if not instance_missing:
                raw_info = fmt_instance_info(wi_safe)
                if raw_info and raw_info.strip():
                    instance_info_line = raw_info
                else:
                    # Present but returned empty — expose the corrupt field
                    uid_dbg = wi_safe.get("uid", "?") if isinstance(wi_safe, dict) else "?"
                    dur_dbg = wi_safe.get("durability_max", "MISSING") if isinstance(wi_safe, dict) else "?"
                    instance_info_line = f"-# ⚠️ Instance data incomplete (uid={uid_dbg}, dur_max={dur_dbg})"
            else:
                instance_info_line = None   # already warned via exp_bar_line above

            _p      = resolve_passive(wi_safe) if isinstance(wi_safe, dict) else {}
            _p_tag  = f" {_p.get('emoji', '')} {_p.get('name', '')}" if _p.get("id") else ""

            field_parts = [
                f"{em} **{nm}**{_p_tag} — {rlabel}",
                exp_bar_line,
                f"-# `{uid}`",
            ]
            if instance_info_line:
                field_parts.append(instance_info_line)
            if effect_lines:
                field_parts.extend(effect_lines)

            embed.add_field(
                name=slot_header,
                value="\n".join(field_parts),
                inline=False,
            )

        # ── Combined Effects ──────────────────────────────────
        combined = calculate_combined_effects(equipped_raw, wi_map, get_base_id)
        if combined:
            combined_lines = _fmt_combined_effects(combined)
            embed.add_field(
                name="Tổng hiệu ứng",
                value="\n".join(combined_lines),
                inline=False,
            )

        embed.set_footer(
            text=(
                "dtn weapon <uid> để xem chi tiết  •  "
                "dtn weapon equip/unequip"
            )
        )
        await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGWeapon(bot))
