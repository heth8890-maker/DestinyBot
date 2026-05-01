"""
===== FILE: rpg_enchant.py =====
Chứa: RPGEnchant Cog — enchant + upgrade + status commands.

⚡ Fixes (Step 3):
  FIX 1: weapon_upgrade IndentationError — toàn bộ khối if uw is None ở cột 1
          bên ngoài phương thức → module không load được → mọi lệnh chết.
          Đã sửa indent về đúng vị trí trong phương thức.
  FIX 2: weapon_upgrade import create_upgrade_entry từ rpg_addon (không tồn tại)
          → ImportError.  Thay bằng ensure_weapon_uid + ensure_upgrade_entry từ rpg_core.
  FIX 3: weapon_enchant dùng user["inventory"] → đổi thành user["inv"] (key đúng).
  FIX 4: weapon_enchant không xoá base_id khỏi kho sau khi tạo UID
          → ghost duplication (cả "467" lẫn "467-ABC12" tồn tại cùng lúc).
          Đã thêm remove_weapon_from_bag trước add_weapon.
  FIX 5: \\n literal trong f-strings weapon_enchant → \n thực sự.
  FIX 6: weapon_upgrade bag existence check dùng bare `in` → bổ sung base_id fallback.
"""

import asyncio

import discord
from discord.ext import commands
from discord.ui import Button, View

from rpg_weapon import (
    get_weapon_by_id,
    RARITY_COLOR,
    RARITY_LABEL,
    ERR, OK, COIN_EMOJI,
)

# ─── Constants ───────────────────────────────────────────────────────────────
_ENCHANT_STACK_ITEM_ID = "enchant_stack"   # key trong user["inv"]
_ENCHANT_STACK_COST    = 1                 # số stack tiêu thụ mỗi lần enchant


def _rarity_tier(rarity: str) -> str:
    return RARITY_LABEL.get(rarity, rarity)


# ═══════════════════════════════════════════════════════════════════════════════
# UPGRADE VIEW
# ═══════════════════════════════════════════════════════════════════════════════

class _UpgradeView(View):
    """
    View gồm 1 nút "Nâng cấp" và 1 nút "Huỷ".
    Sau khi bấm Nâng cấp: kiểm tra coin → deduct → update effect_levels → save.
    """

    def __init__(self, ctx, user: dict, uid: str, effect_key: str, data: dict):
        super().__init__(timeout=60)
        self.ctx        = ctx
        self.user       = user
        self.uid        = uid
        self.effect_key = effect_key
        self.data       = data

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Đây không phải lệnh của bạn.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⚡ Nâng cấp", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        from rpg_core import (
            save_data, get_user,
            get_weapon_entity, get_base_id,
        )
        from cash import get_balance, update_balance
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, upgrade_cost, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        # Use already-saved data (saved in weapon_upgrade before view was sent)
        data  = self.data
        uid   = str(self.ctx.author.id)
        user  = get_user(uid, data)

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

        bal = get_balance(self.ctx.author.id)
        if bal < cost:
            await interaction.response.send_message(
                f"{ERR} | Không đủ coin. Cần **{cost:,}**, có **{bal:,}**.",
                ephemeral=True,
            )
            self.stop()
            return

        # Deduct coin (economy.json) + increment level (rpg_data.json)
        update_balance(self.ctx.author.id, -cost)
        uw["effect_levels"][self.effect_key] = lv + 1

        await save_data(data)

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
                f"Coin còn lại: **{get_balance(self.ctx.author.id):,}** <:Coin:1495831576397742241>"
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

        Dùng WeaponID.parse() — KHÔNG dùng .split("-") trực tiếp.
        """
        # FIX 2: removed non-existent `create_upgrade_entry` from rpg_addon import.
        # UID promotion + entry creation now done via rpg_core functions.
        from rpg_core import (
            load_data, save_data, get_user, WeaponID, get_weapon_entity,
            ensure_weapon_uid, ensure_upgrade_entry, get_base_id,
        )
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, upgrade_cost, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

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
            # FIX 1: khối này trước đây ở cột 1 bên ngoài phương thức →
            # IndentationError → toàn bộ module không import được → mọi lệnh chết.
            # FIX 6: bag check hỗ trợ base_id fallback (bare `in` chỉ exact match).
            bag         = user.get("weapons", [])
            target_base = get_base_id(weapon_ref)
            in_bag = (
                weapon_ref in bag
                or any(get_base_id(w) == target_base for w in bag)
            )
            if not in_bag:
                return await ctx.send(f"{ERR} | Không có vũ khí `{weapon_ref}` trong túi.")

            # Promote base_id → UID (lazy, in-place), then create upgrade entry
            actual_uid = ensure_weapon_uid(user, weapon_ref)
            if not actual_uid:
                return await ctx.send(f"{ERR} | Lỗi nhận diện ID vũ khí.")

            ensure_upgrade_entry(user, actual_uid)
            await save_data(data)
            uw         = get_upgraded_weapon(user, actual_uid)
            weapon_ref = actual_uid

            if uw is None:
                # Cực kỳ hiếm — guard an toàn tuyệt đối
                return await ctx.send(f"{ERR} | Lỗi tạo dữ liệu nâng cấp. Vui lòng thử lại.")

        # Resolve entity — SINGLE ENTRY POINT
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

        # Clamp level (backward-compat)
        lv = min(max(1, uw["effect_levels"].get(effect_key, 1)), UPGRADE_MAX_LEVEL)
        uw["effect_levels"][effect_key] = lv

        if lv >= UPGRADE_MAX_LEVEL:
            return await ctx.send(
                f"<:Effect:1495466103047061679> Effect `{effect_key}` "
                f"đã đạt **Lv {UPGRADE_MAX_LEVEL}** tối đa!"
            )

        # Tính values hiển thị
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

        view = _UpgradeView(ctx, user, uw["uid"], effect_key, data)
        await ctx.send(panel, view=view)

    # ── dtn enchant <base_id> ─────────────────────────────────────────────────
    @commands.command(name="enchant")
    async def weapon_enchant(self, ctx, weapon_id: str):
        """
        Tiêu 1x Enchantment Stack để phù phép vũ khí stack → Unique ID.
        Chỉ chấp nhận base ID (VD: 467).  UID đã phù phép thì không cần enchant lại.

        Flow đúng:
          1. Kiểm tra base_id tồn tại trong kho (không tính slot equip)
          2. Kiểm tra Enchantment Stack trong user["inv"]  ← FIX 3
          3. Trừ stack
          4. Xoá base_id khỏi kho                          ← FIX 4
          5. Tạo UID mới (add_weapon make_unique=True)
          6. Lưu data
        """
        # FIX 4: added remove_weapon_from_bag to import list
        from rpg_core import (
            load_data, save_data, get_user,
            add_weapon, remove_weapon_from_bag,
            get_weapon_entity, WeaponID,
        )

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        # 1. Resolve & validate — chỉ chấp nhận base ID
        target_base_id, is_unique = WeaponID.parse(weapon_id)

        if is_unique:
            # FIX 5: was \\n (literal backslash-n) → now proper \n
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
            # FIX 5: was \\n (literal) → now proper \n
            hint = (
                "\n(Vũ khí đang được trang bị — dùng `dtn weapon unequip <slot>` trước.)"
                if target_base_id in equipped_base_ids else ""
            )
            return await ctx.send(
                f"{ERR} | Không tìm thấy `{target_base_id}` trong kho.{hint}"
            )

        # 3. Kiểm tra Enchantment Stack
        # FIX 3: was user.setdefault("inventory", {}) — correct field is user["inv"]
        inventory   = user.setdefault("inv", {})
        stack_count = inventory.get(_ENCHANT_STACK_ITEM_ID, 0)
        if stack_count < _ENCHANT_STACK_COST:
            # FIX 5: was \\n (literal) → now proper \n
            return await ctx.send(
                f"{ERR} | Cần **{_ENCHANT_STACK_COST}x Enchantment Stack** để phù phép.\n"
                f"Bạn đang có: **{stack_count}x**."
            )

        # 4. Trừ Stack → Xoá base_id khỏi kho → Tạo UID
        inventory[_ENCHANT_STACK_ITEM_ID] = stack_count - _ENCHANT_STACK_COST
        if inventory[_ENCHANT_STACK_ITEM_ID] <= 0:
            del inventory[_ENCHANT_STACK_ITEM_ID]

        # FIX 4: previous code added UID but NEVER removed the original base_id,
        # leaving the user with both "467" (stack) and "467-ABC12" (uid) in the
        # bag — a ghost duplication.  Correct order: remove first, then add.
        remove_weapon_from_bag(user, target_base_id)

        # ⚠️  ĐIỂM DUY NHẤT trong toàn bộ project gọi add_weapon(make_unique=True)
        new_uid = add_weapon(user, target_base_id, make_unique=True)

        if not new_uid:
            # Rollback: restore stack item + restore base_id in bag
            inventory[_ENCHANT_STACK_ITEM_ID] = (
                inventory.get(_ENCHANT_STACK_ITEM_ID, 0) + _ENCHANT_STACK_COST
            )
            user["weapons"].append(target_base_id)   # restore original
            return await ctx.send(
                f"{ERR} | Enchantment thất bại — không thể tạo Unique ID. "
                "Stack đã được hoàn lại."
            )

        # 5. Lưu data
        await save_data(data)

        # 6. Build embed xác nhận
        entity       = get_weapon_entity(user, new_uid)
        rarity_color = RARITY_COLOR.get(w.get("rarity", "common"), 0xFFFFFF)
        display_name = entity.fmt_name() if entity else f"`{new_uid}`"
        stats_value  = entity.fmt_stats() if entity else "—"

        embed = discord.Embed(
            title="<:Effect:1495466103047061679> | Enchantment thành công!",
            # FIX 5: was \\n (literal) → now proper \n
            description=(
                f"Stack `{target_base_id}` đã được phù phép thành Unique:\n"
                f"{display_name}"
            ),
            color=rarity_color,
        )
        embed.add_field(
            name="<:Effect:1495466103047061679> | Chỉ số (base)",
            value=stats_value,
            inline=False,
        )
        embed.add_field(
            name="<:Key:1496098633395998740> | Độ hiếm",
            value=_rarity_tier(w.get("rarity", "common")),
            inline=True,
        )
        embed.add_field(
            name="📋 Unique ID (tap để copy)",
            value=f"`{new_uid}`",
            inline=True,
        )
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
            load_data, get_user, WeaponID, get_weapon_entity, get_base_id,
        )
        from rpg_addon import (
            get_upgraded_weapon,
            effect_value_at_level, fmt_effect_val,
            UPGRADE_MAX_LEVEL,
        )

        data = load_data()
        uid  = str(ctx.author.id)
        user = get_user(uid, data)

        # ── Không truyền ID → hiển thị equipped ──────────────────────────────
        if weapon_ref is None:
            equipped = [e for e in user.get("equipped", []) if e is not None]
            if not equipped:
                return await ctx.send(f"{ERR} | Bạn chưa trang bị vũ khí nào.")

            embeds = []
            for wid in equipped:
                entity = get_weapon_entity(user, wid)
                if entity:
                    embeds.append(entity.build_embed())

            for em in embeds:
                await ctx.send(embed=em)
            return

        # ── Truyền ID → hiển thị vũ khí cụ thể ──────────────────────────────
        base_id, _ = WeaponID.parse(weapon_ref)
        entity     = get_weapon_entity(user, weapon_ref)
        if entity is None:
            # Thử tra cứu theo base_id thuần (weapon chưa trong kho người dùng)
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
            embed.add_field(name="📖 Độ hiếm", value=_rarity_tier(w.get("rarity", "common")), inline=True)
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
