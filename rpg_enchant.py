"""
===== FILE: rpg_enchant.py =====
Chứa: RPGEnchant Cog — enchant + upgrade + status commands.

⚡ Fixes (Step 3 — giữ nguyên):
  FIX 1–6: (xem comment gốc)

🗄️  Migration → MongoDB (qua rpg_database + database_helper):
  • load_data() + get_user(uid, data)  →  get_user(user_id)  từ rpg_database
  • await save_data(data)              →  save_user(user_id, user)  (sync)
  • get_balance() / update_balance()  →  user["cash"] trực tiếp
  • _UpgradeView bỏ tham số `data`    →  tự gọi get_user() trong confirm()
"""

import discord
from discord.ext import commands
from discord.ui import Button, View

from rpg_weapon import (
    get_weapon_by_id,
    RARITY_COLOR,
    RARITY_LABEL,
    ERR, OK, COIN_EMOJI,
)
from rpg_database import get_user, save_user

# ─── Constants ───────────────────────────────────────────────────────────────
_ENCHANT_STACK_ITEM_ID = "enchant_stack"
_ENCHANT_STACK_COST    = 1


def _rarity_tier(rarity: str) -> str:
    return RARITY_LABEL.get(rarity, rarity)


# ═══════════════════════════════════════════════════════════════════════════════
# UPGRADE VIEW
# ═══════════════════════════════════════════════════════════════════════════════

class _UpgradeView(View):
    """
    View gồm 1 nút "Nâng cấp" và 1 nút "Huỷ".

    Không còn nhận `data`/`user` — confirm() tự gọi get_user() để lấy
    dữ liệu mới nhất từ MongoDB, tránh stale data khi timeout dài.
    """

    def __init__(self, ctx, uid: str, effect_key: str):
        super().__init__(timeout=60)
        self.ctx        = ctx
        self.uid        = uid          # weapon unique ID
        self.effect_key = effect_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Đây không phải lệnh của bạn.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⚡ Nâng cấp", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        from rpg_core import get_weapon_entity
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, upgrade_cost, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        # Fetch user mới nhất từ MongoDB
        user, _ = get_user(self.ctx.author.id)

        uw = get_upgraded_weapon(user, self.uid)
        if not uw:
            await interaction.response.send_message(
                f"{ERR} | Dữ liệu nâng cấp không còn tồn tại.", ephemeral=True
            )
            self.stop()
            return

        lv = uw["effect_levels"].get(self.effect_key, 1)
        if lv >= UPGRADE_MAX_LEVEL:
            await interaction.response.send_message(
                f"Effect `{self.effect_key}` đã đạt Lv tối đa!", ephemeral=True
            )
            self.stop()
            return

        entity = get_weapon_entity(user, self.uid)
        if not entity:
            await interaction.response.send_message(
                f"{ERR} | Lỗi entity vũ khí.", ephemeral=True
            )
            self.stop()
            return

        w            = entity.base_data
        base_effects = w.get("effects", {})
        cost         = upgrade_cost(w["max"], lv, w.get("rarity", "common"), base_effects)

        # cash nằm trong user doc
        bal = user.get("cash", 0)
        if bal < cost:
            await interaction.response.send_message(
                f"{ERR} | Không đủ coin. Cần **{cost:,}**, có **{bal:,}**.",
                ephemeral=True,
            )
            self.stop()
            return

        # Trừ coin + tăng level → lưu 1 lần
        user["cash"] = bal - cost
        uw["effect_levels"][self.effect_key] = lv + 1
        save_user(self.ctx.author.id, user)

        bv       = base_effects[self.effect_key]
        new_val  = effect_value_at_level(bv, lv + 1, self.effect_key)
        new_str  = fmt_effect_val(self.effect_key, new_val)

        filled = int((lv + 1) / UPGRADE_MAX_LEVEL * 20)
        bar    = "█" * filled + "░" * (20 - filled)

        await interaction.response.edit_message(
            content=(
                f"✅ Nâng cấp thành công!\n"
                f"{entity.fmt_name()} | `{self.uid}`\n"
                f"**{new_str}** _(Lv{lv + 1})_\n"
                f"`{bar}`\n"
                f"Coin còn lại: **{user['cash']:,}** <:Coin:1495831576397742241>"
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="✖ Huỷ", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="Đã huỷ nâng cấp.", view=None)
        self.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════════════════════════

class RPGEnchant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── dtn upgrade <id|uid> <effect> ────────────────────────────────────────
    @commands.command(name="upgrade", aliases=["up"])
    async def weapon_upgrade(self, ctx, weapon_ref: str, effect_key: str):
        """
        Hiện panel nâng cấp (chỉ số hiện tại + cost lv tiếp) + nút Nâng cấp.
        Nếu <weapon_ref> là base ID (vd: 467)     → lần đầu nâng, tạo unique ID.
        Nếu <weapon_ref> là unique ID (467-A3B2C1) → tiếp tục nâng.
        """
        from rpg_core import (
            WeaponID, get_weapon_entity,
            ensure_weapon_uid, ensure_upgrade_entry, get_base_id,
        )
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, upgrade_cost, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        user, _ = get_user(ctx.author.id)

        # Dùng WeaponID.parse() — KHÔNG dùng .split("-")
        base_id, _ = WeaponID.parse(weapon_ref)
        w_check    = get_weapon_by_id(base_id)
        if w_check and w_check.get("no_upgrade"):
            return await ctx.send(
                f"{ERR} | Vũ khí **{w_check['name']}** không thể nâng cấp."
            )

        # Chặn upgrade khi đang equip
        equipped_list = [e for e in user.get("equipped", []) if e is not None]
        if weapon_ref in equipped_list or base_id in equipped_list:
            return await ctx.send(
                f"{ERR} | Không thể nâng cấp vũ khí đang trang bị!\n"
                "Dùng `dtn weapon unequip <slot>` trước."
            )

        # Xác định upgrade entry — hỗ trợ cả base_id lẫn uid
        uw = get_upgraded_weapon(user, weapon_ref)

        if uw is None:
            # FIX 1: indent fix
            # FIX 6: base_id fallback
            bag         = user.get("weapons", [])
            target_base = get_base_id(weapon_ref)
            in_bag = (
                weapon_ref in bag
                or any(get_base_id(w) == target_base for w in bag)
            )
            if not in_bag:
                return await ctx.send(f"{ERR} | Không có vũ khí `{weapon_ref}` trong túi.")

            actual_uid = ensure_weapon_uid(user, weapon_ref)
            if not actual_uid:
                return await ctx.send(f"{ERR} | Lỗi nhận diện ID vũ khí.")

            ensure_upgrade_entry(user, actual_uid)
            save_user(ctx.author.id, user)

            uw         = get_upgraded_weapon(user, actual_uid)
            weapon_ref = actual_uid

            if uw is None:
                return await ctx.send(f"{ERR} | Lỗi tạo dữ liệu nâng cấp. Vui lòng thử lại.")

        entity = get_weapon_entity(user, uw["uid"])
        if entity is None:
            return await ctx.send(f"{ERR} | Lỗi dữ liệu vũ khí.")

        w            = entity.base_data
        base_effects = w.get("effects", {})
        if effect_key not in base_effects:
            available = ", ".join(f"`{k}`" for k in base_effects)
            return await ctx.send(
                f"{ERR} | Effect `{effect_key}` không có trên vũ khí này.\n"
                f"Effect khả dụng: {available}"
            )

        lv = min(max(1, uw["effect_levels"].get(effect_key, 1)), UPGRADE_MAX_LEVEL)
        uw["effect_levels"][effect_key] = lv

        if lv >= UPGRADE_MAX_LEVEL:
            return await ctx.send(
                f"<:Effect:1495466103047061679> Effect `{effect_key}` "
                f"đã đạt **Lv {UPGRADE_MAX_LEVEL}** tối đa!"
            )

        bv       = base_effects[effect_key]
        cur_val  = effect_value_at_level(bv, lv,     effect_key)
        next_val = effect_value_at_level(bv, lv + 1, effect_key)
        cost     = upgrade_cost(w["max"], lv, w.get("rarity", "common"), base_effects)

        filled = int(lv / UPGRADE_MAX_LEVEL * 20)
        bar    = "█" * filled + "░" * (20 - filled)

        cur_str  = fmt_effect_val(effect_key, cur_val)
        next_str = fmt_effect_val(effect_key, next_val)

        panel = (
            f"{entity.fmt_name()} | `{uw['uid']}`\n"
            f"**{cur_str}**\n"
            f"`{bar}`\n"
            f"Lv{lv + 1}: {next_str}  |  <:2245:1493575277605949480> {cost:,}"
        )

        view = _UpgradeView(ctx, uw["uid"], effect_key)
        await ctx.send(panel, view=view)

    # ── dtn enchant <base_id> ─────────────────────────────────────────────────
    @commands.command(name="enchant")
    async def weapon_enchant(self, ctx, weapon_id: str):
        """
        Tiêu 1x Enchantment Stack để phù phép vũ khí stack → Unique ID.
        Chỉ chấp nhận base ID (VD: 467).
        """
        from rpg_core import (
            add_weapon, remove_weapon_from_bag,
            get_weapon_entity, WeaponID,
        )

        user, _ = get_user(ctx.author.id)

        # 1. Resolve & validate — chỉ chấp nhận base ID
        target_base_id, is_unique = WeaponID.parse(weapon_id)

        if is_unique:
            return await ctx.send(
                f"{ERR} | `{weapon_id}` đã là Unique ID.\n"
                "Chỉ enchant được **Stack version** (base ID, ví dụ: `467`)."
            )

        w = get_weapon_by_id(target_base_id)
        if not w:
            return await ctx.send(
                f"{ERR} | Không tìm thấy vũ khí ID `{target_base_id}`."
            )

        if w.get("no_upgrade"):
            return await ctx.send(
                f"{ERR} | **{w['name']}** không thể enchant (no_upgrade flag)."
            )

        # 2. Kiểm tra sở hữu trong kho (không tính slot equip)
        bag = user.get("weapons", [])
        if target_base_id not in bag:
            equipped_base_ids = []
            for wid in user.get("equipped", []):
                if wid:
                    bid, _ = WeaponID.parse(str(wid))
                    equipped_base_ids.append(bid)
            hint = (
                "\n(Vũ khí đang được trang bị — dùng `dtn weapon unequip <slot>` trước.)"
                if target_base_id in equipped_base_ids else ""
            )
            return await ctx.send(
                f"{ERR} | Không tìm thấy `{target_base_id}` trong kho.{hint}"
            )

        # 3. Kiểm tra Enchantment Stack — FIX 3: field đúng là user["inv"]
        inventory   = user.setdefault("inv", {})
        stack_count = inventory.get(_ENCHANT_STACK_ITEM_ID, 0)
        if stack_count < _ENCHANT_STACK_COST:
            return await ctx.send(
                f"{ERR} | Cần **{_ENCHANT_STACK_COST}x Enchantment Stack** để phù phép.\n"
                f"Bạn đang có: **{stack_count}x**."
            )

        # 4. Trừ Stack → Xoá base_id → Tạo UID
        inventory[_ENCHANT_STACK_ITEM_ID] = stack_count - _ENCHANT_STACK_COST
        if inventory[_ENCHANT_STACK_ITEM_ID] <= 0:
            del inventory[_ENCHANT_STACK_ITEM_ID]

        # FIX 4: remove trước, add sau — tránh ghost duplication
        remove_weapon_from_bag(user, target_base_id)
        new_uid = add_weapon(user, target_base_id, make_unique=True)

        if not new_uid:
            # Rollback
            inventory[_ENCHANT_STACK_ITEM_ID] = (
                inventory.get(_ENCHANT_STACK_ITEM_ID, 0) + _ENCHANT_STACK_COST
            )
            user["weapons"].append(target_base_id)
            return await ctx.send(
                f"{ERR} | Enchantment thất bại — không thể tạo Unique ID. "
                "Stack đã được hoàn lại."
            )

        # 5. Lưu lên MongoDB
        save_user(ctx.author.id, user)

        # 6. Build embed
        entity       = get_weapon_entity(user, new_uid)
        rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)
        display_name = entity.fmt_name() if entity else f"`{new_uid}`"
        stats_value  = entity.fmt_stats() if entity else "—"

        embed = discord.Embed(
            title="<:Effect:1495466103047061679> | Enchantment thành công!",
            description=(
                f"Stack `{target_base_id}` đã được phù phép thành Unique:\n"
                f"{display_name}"
            ),
            color=rarity_color,
        )
        embed.add_field(name="<:Effect:1495466103047061679> | Chỉ số (base)", value=stats_value, inline=False)
        embed.add_field(name="<:Key:1496098633395998740> | Độ hiếm", value=_rarity_tier(w.get("rarity", "common")), inline=True)
        embed.add_field(name="📋 Unique ID (tap để copy)", value=f"`{new_uid}`", inline=True)
        embed.add_field(
            name="⚡ Bước tiếp theo",
            value=(
                f"`dtn upgrade {new_uid} <effect>` — bắt đầu nâng cấp\n"
                f"`dtn weapon equip {new_uid}` — trang bị ngay"
            ),
            inline=False,
        )
        embed.set_footer(
            text=f"Enchantment Stack còn lại: {inventory.get(_ENCHANT_STACK_ITEM_ID, 0)}x"
        )
        await ctx.send(embed=embed)

    # ── dtn status [uid|base_id] ──────────────────────────────────────────────
    @commands.command(name="status", aliases=["stat"])
    async def weapon_status(self, ctx, weapon_ref: str = None):
        """
        Hiển thị chỉ số đầy đủ của vũ khí (base + upgrade bonuses).
        Nếu không truyền ID → hiển thị tất cả vũ khí đang trang bị.
        """
        from rpg_core import (
            WeaponID, get_weapon_entity, get_base_id,
        )
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        user, _ = get_user(ctx.author.id)

        # ── Không truyền ID → hiển thị equipped ──────────────────────────────
        if weapon_ref is None:
            equipped = [e for e in user.get("equipped", []) if e is not None]
            if not equipped:
                return await ctx.send(f"{ERR} | Bạn chưa trang bị vũ khí nào.")

            for wid in equipped:
                entity = get_weapon_entity(user, wid)
                if entity:
                    await ctx.send(embed=entity.build_embed())
            return

        # ── Truyền ID → hiển thị vũ khí cụ thể ──────────────────────────────
        base_id, _ = WeaponID.parse(weapon_ref)
        entity     = get_weapon_entity(user, weapon_ref)
        if entity is None:
            w = get_weapon_by_id(base_id)
            if not w:
                return await ctx.send(
                    f"{ERR} | Không tìm thấy vũ khí `{weapon_ref}`."
                )
            rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)
            embed = discord.Embed(
                title=f"{w['emoji']} {w['name']}",
                description=w.get("description", "—"),
                color=rarity_color,
            )
            embed.add_field(name="📖 Độ hiếm",           value=_rarity_tier(w.get("rarity", "common")), inline=True)
            embed.add_field(name=f"{COIN_EMOJI} Giá trị", value=f"**{w.get('min', 0):,}** – **{w.get('max', 0):,}**", inline=True)
            if w.get("effects"):
                eff_lines = [f"• `{k}`: {v}" for k, v in w["effects"].items()]
                embed.add_field(name="<:Effect:1495466103047061679> Effects (base)", value="\n".join(eff_lines), inline=False)
            embed.set_footer(text=f"ID: {w['id']}  |  Chưa có trong kho")
            return await ctx.send(embed=embed)

        await ctx.send(embed=entity.build_embed())

        uw = get_upgraded_weapon(user, weapon_ref)
        if uw and uw.get("effect_levels"):
            w            = entity.base_data
            base_effects = w.get("effects", {})
            lines        = []
            for ek, lv in uw["effect_levels"].items():
                lv = min(max(1, lv), UPGRADE_MAX_LEVEL)
                if ek not in base_effects:
                    continue
                bv       = base_effects[ek]
                next_lv  = min(lv + 1, UPGRADE_MAX_LEVEL)
                next_val = effect_value_at_level(bv, next_lv, ek)
                next_str = fmt_effect_val(ek, next_val)
                filled   = int(lv / UPGRADE_MAX_LEVEL * 20)
                bar      = "█" * filled + "░" * (20 - filled)
                lines.append(
                    f"`{ek}` | `{bar}` Lv {lv}/{UPGRADE_MAX_LEVEL}"
                    f"  →  Lv {next_lv}: {next_str}"
                )
            if lines:
                await ctx.send("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGEnchant(bot))
