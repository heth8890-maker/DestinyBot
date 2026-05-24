

import discord
from discord.ext import commands

from rpg_weapon_data import (
    RARITY_COLOR,
    RARITY_LABEL,
    ERR,
    OK,
    get_weapon_by_id,
    calculate_combined_effects,
    _fmt_effects_scaled,
    _fmt_combined_effects,
    _rarity_tier,
)
from rpg_instance import resolve_passive, quality_label


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
        p       = resolve_passive(wi.get("passive", {})) if isinstance(wi, dict) else None
        p_icon  = p.get("emoji", "") if p and p.get("id") else ""
        return f"{em}{p_icon} **{nm}**{eq_tag} • Lv {lv}\n`{wid}`"

    # ─────────────────────────────────────────────────────────
    # MAIN: dtn weapon / dtn weapon <uid> / dtn weapon <uid> <slot>
    # Hybrid: cũng hoạt động như /weapon [weapon_id] [slot]
    # ─────────────────────────────────────────────────────────
    @commands.hybrid_command(name="weapon", aliases=["w"])
    async def weapon(self, ctx, weapon_id: str = None, slot: int = None):
        from rpg_core import WeaponID, get_weapon_entity, get_base_id
        from rpg_database import get_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # ── dtn weapon <uid> <slot> — trang bị nhanh ────────────────
        if weapon_id and slot is not None:
            from rpg_core import equip_weapon, WeaponID
            from rpg_quest import add_quest_progress
            from rpg_database import save_user

            base_id, _ = WeaponID.parse(weapon_id)
            w_new      = get_weapon_by_id(base_id)

            for i, wid in enumerate(user.get("equipped", []), 1):
                if wid is None:
                    continue
                existing_base, _ = WeaponID.parse(str(wid))
                if existing_base == base_id:
                    return await ctx.send(
                        f"{ERR} | Đã có **{w_new['name'] if w_new else base_id}** "
                        f"ở ô [{i}]. Dùng `dtn unequip {i}` trước."
                    )

            ok, msg = equip_weapon(user, weapon_id, slot)
            if ok:
                if not save_user(author_uid, user):
                    return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Thử lại.")
                # [FIX-4] Dùng str() để tránh mismatch nếu equip_weapon
                # lưu UID dưới dạng khác type (int vs str).
                # Fallback: dùng slot arg nếu vẫn không tìm được.
                slot_used = next(
                    (i+1 for i, wid in enumerate(user["equipped"])
                     if str(wid) == str(weapon_id)), slot
                )
                name = w_new["name"] if w_new else weapon_id
                add_quest_progress(ctx.author.id, "weapons_equipped")
                return await ctx.send(
                    f"{OK} | Đã trang bị **{name}** vào ô **[{slot_used}]**.\n"
                    f"-# `{weapon_id}`"
                )
            else:
                return await ctx.send(f"{ERR} | {msg}")

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

            # Lệnh nhanh — đặt ở đầu embed
            embed.insert_field_at(
                0,
                name=" Lệnh nhanh",
                value=(
                    f"`dtn weapon {weapon_id} <slot>` trang bị\n"
                    f"`dtn unequip <slot>` tháo\n"
                    f"`dtn repair` sửa"
                ),
                inline=False,
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

                # Durability — raw text, no bar
                dur     = wi.get("durability", None)
                dur_max = wi.get("durability_max", None)
                if dur is not None and dur_max:
                    embed.add_field(
                        name="Durability",
                        value=f"{dur}/{dur_max}",
                        inline=True,
                    )

                # Quality
                quality = wi.get("quality", None)
                if quality is not None:
                    embed.add_field(
                        name="Quality",
                        value=quality_label(quality),
                        inline=True,
                    )

                # Passive — format: <Icon> | <tên> → Passive → <mô tả>
                _p = resolve_passive(wi.get("passive", {}))
                if _p and _p.get("id"):
                    p_emoji = _p.get("emoji", "🔮")
                    p_name  = _p.get("name", "Passive")
                    p_desc  = _p.get("description") or _p.get("effect") or "—"
                    embed.add_field(
                        name=f"{p_emoji} | {p_name}",
                        value=f"**Passive**\n{p_desc}",
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
                        "-# Use `dtn unequip <slot>` then re-equip to attempt recovery."
                    ),
                    inline=False,
                )

            # UID
            embed.add_field(
                name=" UID",
                value=f"`{weapon_id}`",
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
        equipped_set = {w for w in equipped if w}   # dùng để lọc storage
        # equipped_ordered: list (slot_idx 1-based, wid) — giữ nguyên thứ tự slot
        equipped_ordered = [
            (i + 1, wid)
            for i, wid in enumerate(equipped)
            if wid
        ]
        storage_list = [
            w for w in user.get("weapons", [])
            if w not in equipped_set
        ]
        all_weapons = [wid for _, wid in equipped_ordered] + storage_list

        if not all_weapons:
            return await ctx.send(
                f"<:Hamer:1495462570469888069> **{ctx.author.display_name}** "
                f"chưa có vũ khí nào."
            )

        # ── Build lines — 2 nhóm riêng (VIP style) ─────────────────
        equipped_lines = []
        for slot_idx, wid in equipped_ordered:
            base_id = get_base_id(str(wid)) or str(wid)
            w       = get_weapon_by_id(base_id)
            nm      = w["name"]  if w else base_id
            em      = w["emoji"] if w else "⚔️"
            wi      = wi_map.get(wid)
            lv      = wi.get("level", 1) if wi else 1
            p       = resolve_passive(wi.get("passive", {})) if isinstance(wi, dict) else None
            p_icon  = p.get("emoji", "") if p and p.get("id") else ""
            equipped_lines.append(
                f"**[Ô {slot_idx}]** {em}{p_icon} **{nm}** • Lv {lv}\n`{wid}`"
            )

        storage_lines = []
        for wid in storage_list:
            base_id = get_base_id(str(wid)) or str(wid)
            w       = get_weapon_by_id(base_id)
            nm      = w["name"]  if w else base_id
            em      = w["emoji"] if w else "⚔️"
            wi      = wi_map.get(wid)
            lv      = wi.get("level", 1) if wi else 1
            p       = resolve_passive(wi.get("passive", {})) if isinstance(wi, dict) else None
            p_icon  = p.get("emoji", "") if p and p.get("id") else ""
            storage_lines.append(
                f"{em}{p_icon} **{nm}** • Lv {lv}\n`{wid}`"
            )

        PAGE_SIZE   = 9
        pages       = [storage_lines[i:i+PAGE_SIZE] for i in range(0, max(len(storage_lines), 1), PAGE_SIZE)]
        total_pages = len(pages)

        equipped_value = "\n\n".join(equipped_lines) if equipped_lines else "-# Chưa trang bị vũ khí nào."

        _HELP_MSG = (
            "**Hướng dẫn lệnh vũ khí:**\n"
            "`dtn weapon <uid>` — xem chi tiết vũ khí (level, durability, passive, effect)\n"
            "`dtn weapon <uid> <slot>` — trang bị vũ khí vào ô slot (1/2/3)\n"
            "`dtn unequip <slot>` — tháo vũ khí khỏi ô về kho\n"
            "`dtn repair` — sửa độ bền vũ khí đang trang bị\n"
            "`dtn wi` / `dtn myweapon` — xem 3 ô trang bị & tổng effect\n"
            "`dtn weapon` — danh sách toàn bộ vũ khí trong kho"
        )

        # ── WeaponPages — Components v2 LayoutView ───────────────────
        class WeaponPages(discord.ui.LayoutView):
            def __init__(self):
                super().__init__(timeout=60)
                self.page      = 0
                self.show_help = False
                self.message   = None
                self._build_view(0)

            async def _check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message(
                        "Đây không phải danh sách của bạn.", ephemeral=True
                    )
                    return False
                return True

            def _build_view(self, page_idx: int):
                self.clear_items()
                page_idx  = min(page_idx, total_pages - 1)
                self.page = page_idx

                children = []

                # [1] Title
                children.append(discord.ui.TextDisplay(
                    f"<:Hamer:1495462570469888069> **Weapon của {ctx.author.display_name}**"
                ))

                # [2] Help text nếu show_help=True
                if self.show_help:
                    children.append(discord.ui.TextDisplay(_HELP_MSG))

                # [3] Separator
                children.append(discord.ui.Separator(
                    visible=True,
                    spacing=discord.SeparatorSpacing.small,
                ))

                # [4] Equipped section — chỉ trang đầu, chỉ khi có vũ khí
                if page_idx == 0 and equipped_lines:
                    children.append(discord.ui.TextDisplay(
                        "⚔️ **Đang trang bị**\n\n" + equipped_value
                    ))
                    children.append(discord.ui.Separator(
                        visible=True,
                        spacing=discord.SeparatorSpacing.small,
                    ))

                # [5] Storage section
                page_blocks  = pages[page_idx] if pages and pages[page_idx] else []
                storage_text = (
                    f"🗃️ **Trong kho** — trang {page_idx+1}/{total_pages}\n\n"
                    + ("\n\n".join(page_blocks) if page_blocks else "-# Kho trống.")
                )
                if len(storage_text) > 3900:
                    storage_text = storage_text[:3896] + "\n…"
                children.append(discord.ui.TextDisplay(storage_text))

                # Container — chỉ chứa layout tĩnh (TextDisplay, Separator)
                container = discord.ui.Container(
                    *children,
                    accent_color=discord.Color(0xE91E63),
                )
                self.add_item(container)

                # ── ActionRow [?] — top-level (LayoutView spec) ──────
                btn_help = discord.ui.Button(
                    label="?",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"wpn_help_{ctx.author.id}",
                )

                async def _help_cb(interaction: discord.Interaction):
                    if not await self._check(interaction):
                        return
                    self.show_help = not self.show_help
                    self._build_view(self.page)
                    await interaction.response.edit_message(view=self)

                btn_help.callback = _help_cb
                ar_help = discord.ui.ActionRow()
                ar_help.add_item(btn_help)
                self.add_item(ar_help)

                # ── ActionRow nav ◀ page ▶ — top-level ──────────────
                only_one = total_pages == 1
                btn_prev = discord.ui.Button(
                    label="◀",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"wpn_prev_{ctx.author.id}",
                    disabled=only_one,
                )
                btn_pg = discord.ui.Button(
                    label=f"{page_idx+1}/{total_pages}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"wpn_page_{ctx.author.id}",
                    disabled=True,
                )
                btn_next = discord.ui.Button(
                    label="▶",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"wpn_next_{ctx.author.id}",
                    disabled=only_one,
                )

                async def _prev_cb(interaction: discord.Interaction):
                    if not await self._check(interaction):
                        return
                    self.page = (self.page - 1) % total_pages
                    self._build_view(self.page)
                    await interaction.response.edit_message(view=self)

                async def _next_cb(interaction: discord.Interaction):
                    if not await self._check(interaction):
                        return
                    self.page = (self.page + 1) % total_pages
                    self._build_view(self.page)
                    await interaction.response.edit_message(view=self)

                btn_prev.callback = _prev_cb
                btn_next.callback = _next_cb

                ar_nav = discord.ui.ActionRow()
                ar_nav.add_item(btn_prev)
                ar_nav.add_item(btn_pg)
                ar_nav.add_item(btn_next)
                self.add_item(ar_nav)

            async def on_timeout(self):
                try:
                    self._build_view(self.page)
                except Exception:
                    import traceback; traceback.print_exc()
                for item in self.walk_children():
                    if isinstance(item, (discord.ui.Button, discord.ui.Select,
                                         discord.ui.StringSelect)):
                        item.disabled = True
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        try:
            view = WeaponPages()
            view.message = await ctx.send(view=view)
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                await ctx.send(f"{ERR} | Lỗi hiển thị danh sách vũ khí: `{e}`")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────
    # UNEQUIP — dtn unequip <slot> / /unequip <slot>
    # ─────────────────────────────────────────────────────────
    @commands.hybrid_command(name="unequip", aliases=["ue", "une"])
    async def weapon_unequip(self, ctx, slot: int):
        from rpg_core import unequip_weapon, WeaponID
        from rpg_database import get_user, save_user

        author_uid = str(ctx.author.id)
        user, _    = get_user(author_uid)

        ok, result = unequip_weapon(user, slot)
        if ok:
            if not save_user(author_uid, user):
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

    @weapon_unequip.error
    async def weapon_unequip_error(self, ctx, error):
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send(
                f"{ERR} | Dùng `dtn unequip <slot>` — slot là số **1**, **2** hoặc **3**."
            )
        else:
            # [FIX-6] Log lỗi thật ra console để dev debug được,
            # thay vì nuốt hoàn toàn bằng generic message.
            import traceback
            traceback.print_exception(type(error), error, error.__traceback__)
            await ctx.send(f"{ERR} | Đã xảy ra lỗi khi tháo vũ khí. Thử lại sau.")

    # ─────────────────────────────────────────────────────────
    # WI — Hiển thị 3 weapon slot đang trang bị (Components v2)
    # Hybrid: /wi hoạt động như slash command
    # ─────────────────────────────────────────────────────────
    @commands.hybrid_command(name="wi", aliases=["myw", "myweapon"])
    async def display_weapon_info(self, ctx):
        from rpg_core import get_base_id, get_weapon_entity
        from rpg_database import get_user

        author_uid   = str(ctx.author.id)
        user, _      = get_user(author_uid)
        equipped_raw = user.get("equipped", [None, None, None])

        if not any(equipped_raw):
            return await ctx.send(
                f"<:Hamer:1495462570469888069> "
                f"**{ctx.author.display_name}** chưa trang bị weapon nào.\n"
                f"-# Dùng `dtn weapon <id> <slot>` để trang bị."
            )

        wi_map = {
            wi["uid"]: wi
            for wi in user.get("weapon_instances", [])
            if isinstance(wi, dict) and "uid" in wi
        }

        # ── Tính toán per-slot, trả về text block dọc ────────────────
        def _build_slot_block(slot_idx: int, uid: str) -> str:
            b_id = get_base_id(str(uid)) or str(uid)
            w    = get_weapon_by_id(b_id)

            if not w:
                lines = [
                    f"│ ⚠️ **[Ô {slot_idx}]** — Definition not found",
                    f"│ -# base_id: `{b_id}`",
                    f"│ -# `{uid}`",
                ]
                return "\n".join(lines)

            rarity  = w.get("rarity", "common")
            rlabel  = RARITY_LABEL.get(rarity, rarity)
            em      = w.get("emoji", "⚔️")
            nm      = w.get("name", b_id)
            effects = w.get("effects", {})

            wi_inst          = wi_map.get(uid)
            instance_missing = wi_inst is None
            wi_safe          = wi_inst or {}
            level            = wi_safe.get("level", 1)

            _p      = resolve_passive(wi_safe.get("passive", {})) if isinstance(wi_safe, dict) else {}
            _p_icon = _p.get("emoji", "") if _p and _p.get("id") else ""

            # [FIX-1] _fmt_effects_scaled có thể không nhận kwarg instance_missing
            # (phụ thuộc phiên bản rpg_weapon_data). Dùng try/except để tương thích.
            try:
                effect_lines = _fmt_effects_scaled(
                    effects, level, instance_missing=instance_missing,
                    show_passive=False,
                )
            except TypeError:
                effect_lines = _fmt_effects_scaled(effects, level, show_passive=False)

            # Scale %
            if not instance_missing:
                scale     = round(0.60 + (level - 1) * 0.02857, 3)
                scale_str = f"**{scale:.0%}** gốc"
            else:
                scale_str = "—"

            # EXP bar
            if instance_missing:
                exp_bar_line = "-# ⚠️ Instance record not found — level & EXP unknown"
            else:
                exp      = wi_safe.get("exp", 0)
                exp_next = wi_safe.get("exp_to_next", 40)
                if exp_next <= 0:
                    exp_bar_line = f"-# ⚠️ EXP data corrupt (exp_to_next={exp_next})"
                else:
                    filled       = int(exp / exp_next * 10)
                    bar          = "█" * filled + "░" * (10 - filled)
                    exp_bar_line = (
                        f"Lv **{level}** / 50 │ `{bar}` {exp}/{exp_next} EXP"
                        f" │ {scale_str}"
                    )

            # Durability
            dur_line = None
            if not instance_missing:
                dur     = wi_safe.get("durability", None)
                dur_max = wi_safe.get("durability_max", None)
                if dur is not None and dur_max:
                    dur_line = f"Độ bền: {dur}/{dur_max}"

            # Quality
            quality_line = None
            if not instance_missing:
                quality = wi_safe.get("quality", None)
                if quality is not None:
                    quality_line = f"Quality: {quality_label(quality)}"

            # Passive
            passive_line = None
            if _p and _p.get("id"):
                p_emoji = _p.get("emoji", "🔮")
                p_name  = _p.get("name", "Passive")
                p_desc  = _p.get("description") or _p.get("effect") or "—"
                passive_line = f"{p_emoji} **{p_name}**: {p_desc}"

            # Effects condensed
            effects_condensed = None
            if effect_lines:
                effects_condensed = " • ".join(
                    ln.strip().lstrip("- ").replace("\n", " ")
                    for ln in effect_lines
                    if ln.strip()
                )

            # Assemble dòng │
            raw_lines = [
                f"{em}{_p_icon} **{nm}** — {rlabel}   **[Ô {slot_idx}]**",
                exp_bar_line,
            ]
            # dur + quality cùng dòng nếu cả hai có
            if dur_line and quality_line:
                raw_lines.append(f"{dur_line}  •  {quality_line}")
            elif dur_line:
                raw_lines.append(dur_line)
            elif quality_line:
                raw_lines.append(quality_line)

            if passive_line:
                raw_lines.append(passive_line)
            if effects_condensed:
                raw_lines.append(f"-# {effects_condensed}")
            raw_lines.append(f"-# `{uid}`")

            return "\n".join(f"│ {ln}" for ln in raw_lines)

        # ── Dữ liệu slots (chỉ những slot có uid) ────────────────────
        slot_data = []   # list of (slot_idx, uid, block_text)
        for slot_idx, uid in enumerate(equipped_raw[:3], 1):
            if not uid:
                continue
            block = _build_slot_block(slot_idx, uid)
            slot_data.append((slot_idx, uid, block))

        HELP_TEXT = (
            "`dtn weapon <uid>` chi tiết  •  `dtn weapon <uid> <slot>` trang bị\n"
            "`dtn unequip <slot>` tháo  •  `dtn repair` sửa"
        )

        # ── WiView ────────────────────────────────────────────────────
        class WiView(discord.ui.LayoutView):
            def __init__(self_v):
                super().__init__(timeout=60)
                self_v.show_help = False
                self_v.message   = None
                self_v._build_view()

            def _build_view(self_v):
                self_v.clear_items()

                children = []

                # [1] Title
                children.append(discord.ui.TextDisplay(
                    f"<:Hamer:1495462570469888069> **Weapon trang bị — {ctx.author.display_name}**"
                ))

                # [2] Help text nếu show_help=True
                if self_v.show_help:
                    children.append(discord.ui.TextDisplay(HELP_TEXT))

                # [3]+[4] Weapon blocks (Separator + TextDisplay mỗi slot)
                if slot_data:
                    for _, _, block in slot_data:
                        children.append(discord.ui.Separator(
                            visible=True,
                            spacing=discord.SeparatorSpacing.small,
                        ))
                        children.append(discord.ui.TextDisplay(block))
                else:
                    children.append(discord.ui.Separator(
                        visible=True,
                        spacing=discord.SeparatorSpacing.small,
                    ))
                    children.append(discord.ui.TextDisplay("-# Chưa trang bị vũ khí nào."))

                # ── Combined Effects ───────────────────────────────
                combined = calculate_combined_effects(equipped_raw, wi_map, get_base_id)
                if combined:
                    combined_lines = _fmt_combined_effects(combined)
                    children.append(discord.ui.Separator(
                        visible=True,
                        spacing=discord.SeparatorSpacing.small,
                    ))
                    children.append(discord.ui.TextDisplay(
                        "⚔️ **Total Effects**\n" + "\n".join(combined_lines)
                    ))

                # Container — chỉ chứa layout tĩnh (TextDisplay, Separator)
                container = discord.ui.Container(
                    *children,
                    accent_color=discord.Color(0xE91E63),
                )
                self_v.add_item(container)

                # ── ActionRow [?] — top-level (LayoutView spec) ──────
                btn_help = discord.ui.Button(
                    label="?",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"wi_help_{ctx.author.id}",
                )

                async def _help_cb(interaction: discord.Interaction):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message(
                            "Đây không phải danh sách của bạn.", ephemeral=True
                        )
                    self_v.show_help = not self_v.show_help
                    self_v._build_view()
                    await interaction.response.edit_message(view=self_v)

                btn_help.callback = _help_cb
                ar_help = discord.ui.ActionRow()
                ar_help.add_item(btn_help)
                self_v.add_item(ar_help)

            async def on_timeout(self_v):
                # [FIX] walk_children() + không break → disable TẤT CẢ interactive items
                # [FIX-3] Bọc _build_view trong try/except — nếu rebuild thất bại
                # thì vẫn cố disable và edit message thay vì crash silently.
                try:
                    self_v._build_view()
                except Exception:
                    import traceback; traceback.print_exc()
                for item in self_v.walk_children():
                    if isinstance(item, (discord.ui.Button, discord.ui.Select,
                                         discord.ui.StringSelect)):
                        item.disabled = True
                try:
                    await self_v.message.edit(view=self_v)
                except Exception:
                    pass

        # [FIX] Bọc try/except — trước đây exception trong WiView() gây silence hoàn toàn
        try:
            view = WiView()
            view.message = await ctx.send(view=view)
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                await ctx.send(f"{ERR} | Lỗi hiển thị weapon info: `{e}`")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────
    # GIVE WEAPON (admin) — prefix only, không có slash
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

        # FIX: kiểm tra return value của save_user
        if not save_user(str(member.id), user):
            return await ctx.send(f"{ERR} | Lỗi lưu dữ liệu. Vũ khí chưa được trao.")

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


# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGWeapon(bot))
