"""
===== HUNT COMMAND =====

GUARANTEES:
- No silent failures (errors visible in Discord)
- All exceptions logged to console
- User gets clear error message
- Never breaks the bot

[BONUS SYSTEM]
- Mỗi lần hunt có 2 roll bonus:
    • Roll 1: random 1 trong 3 loại (crate 1% | coin 1.75% | item1099 0.75%)
    • Roll 2: random 1 trong 3 loại, tỉ lệ × 50%
- Mỗi loại: lần đầu trong chu kỳ dùng 60%, sau khi nhận → về tỉ lệ gốc
- Tổng tối đa 10 lần bonus / 6 tiếng (chung cả 3 loại), tự reset sau mỗi chu kỳ
- Thông báo bonus gửi là tin nhắn RIÊNG, không gắn vào container

[SLASH COMMANDS]
- /hunt → hunt bình thường  (plain command, không dùng Group)
"""

import time
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

# Import from rpg_core
from rpg_core import (
    get_item_by_id, get_weapon_by_id,
    roll_hunt_items, handle_egg,
    add_item, calc_hunt_cooldown, parse_effects,
    CRATES, get_base_id,
    get_user, load_data, save_data, get_user_lock,
    calc_hunt_exp, grant_weapon_exp,
)
from rpg_addon import parse_effects_upgraded
from rpg_quest import add_quest_progress
from cash import update_balance_safe
from rpg_instance import decrease_durability

# ─────────────────────────────────────────────────────────
# COSMETICS
# ─────────────────────────────────────────────────────────
COIN_EMOJI  = "<:Coin:1495831576397742241>"
SKULL_EMOJI = "<:2859:1495250145942704189>"
SWORD_EMOJI = "<:2918:1495252941492457502>"
HUNT_CD_SEC = 16

ERR = "<:X_:1495466670616219819>"
OK  = "<:Tick:1495466684520206528>"

# ─────────────────────────────────────────────────────────
# COMPONENTS v2 FLAG  (bit 15 = 32768)
# ─────────────────────────────────────────────────────────
_cv2_flags       = discord.MessageFlags()
_cv2_flags.value = 1 << 15

# _eph_cv2_flags đã bỏ — ephemeral response dùng ephemeral=True, CV2 discord.py tự set qua LayoutView

# ─────────────────────────────────────────────────────────
# HUNT SETTINGS — COLOR & DISPLAY MODE
# ─────────────────────────────────────────────────────────
_COLOR_MAP = {
    "none":   None,
    "blue":   0x3498DB,
    "red":    0xE74C3C,
    "purple": 0x9B59B6,
    "black":  0x23272A,
    "white":  0xFFFFFF,
}
_COLOR_LABELS = {
    "none":   "Không màu",
    "blue":   "Xanh dương",
    "red":    "Đỏ",
    "purple": "Tím",
    "black":  "Đen",
    "white":  "Trắng",
}

# Superscript digit map — dùng cho compact level display  e.g. ᴸⱽ⁰⁶
_SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _lv_superscript(lv: int) -> str:
    """Convert level int → ᴸⱽXX superscript string."""
    return "ᴸⱽ" + str(lv).zfill(2).translate(_SUP_DIGITS)


def _get_hunt_settings(user: dict) -> dict:
    """Return hunt_settings sub-dict, tạo default nếu chưa có."""
    return user.setdefault("hunt_settings", {
        "color":        0x4CAF50,   # màu mặc định (green)
        "display_mode": "normal",   # "normal" | "compact"
    })


# ─────────────────────────────────────────────────────────
# SLASH CHECKS  (guild_only)
# ─────────────────────────────────────────────────────────
def _slash_guild_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Lệnh này chỉ dùng được trong server.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


# ─────────────────────────────────────────────────────────
# BONUS SYSTEM — CONSTANTS
# ─────────────────────────────────────────────────────────
CRATE_DROP_CHANCE        = 0.6    # % tỉ lệ gốc (sau lần đầu)
COIN_DROP_CHANCE         = 0.7    # % tỉ lệ gốc (sau lần đầu)
ITEM_1099_DROP_CHANCE    = 0.3    # % tỉ lệ gốc (sau lần đầu)
BONUS_MAX                = 10     # tối đa lần bonus mỗi chu kỳ (chung cả 3 loại)
BONUS_RESET_SEC          = 6 * 3600  # 6 tiếng
CRATE_FIRST_DROP_CHANCE  = 60.0   # % lần đầu crate trong chu kỳ
COIN_FIRST_DROP_CHANCE   = 60.0   # % lần đầu coin trong chu kỳ
ITEM_1099_FIRST_DROP_CHANCE = 5.0 # % lần đầu item 1099 trong chu kỳ
SECOND_ROLL_MULTIPLIER   = 0.5    # roll 2 = roll 1 × 50%
TREASURE_DECAY_PER_HIT   = 0.15   # giảm 15% tỉ lệ sau mỗi lần ra treasure

# ─── Coin drop amount ────────────────────────────────────────────────────────
COIN_FIRST_AMOUNT_MIN    = 800
COIN_FIRST_AMOUNT_MAX    = 1200
COIN_AMOUNT_MIN          = 2000
COIN_AMOUNT_MAX          = 6500

# ─── Treasure item 1099 drop config ─────────────────────────────────────────
ITEM_1099_AMOUNT_MIN        = 88
ITEM_1099_AMOUNT_MAX        = 137

# Danh sách loại treasure để random chọn mỗi roll
_TREASURE_TYPES = ["crate", "coin", "item1099"]

# Bảng tỉ lệ crate — tổng = 100%
# Others cố định: 003=5, 004=2, 006=1, 009=0.001 → tổng = 8.001
# → 001 + 002 = 91.999
CRATE_POOL = [
    ("001", 69.999),   # Common      — ~70%
    ("002", 22),       # Rare        —  22%
    ("003",  5),       # Dark        —   5%
    ("004",  2),       # Soul        —   2%
    ("006",  1),       # (crate 006) —   1%
    ("009",  0.001),   # (crate 009) —  ~0%
]
# Tổng: 69.999 + 22 + 5 + 2 + 1 + 0.001 = 100.000 ✓

# Tên fallback nếu CRATES chưa load được
_CRATE_FALLBACK = {
    "001": {"name": "Common Crate",  "emoji": "📦"},
    "002": {"name": "Rare Crate",    "emoji": "🟣"},
    "003": {"name": "Dark Crate",    "emoji": "🖤"},
    "004": {"name": "Soul Crate",    "emoji": "💠"},
    "006": {"name": "Crate 006",     "emoji": "🎁"},
    "009": {"name": "Crate 009",     "emoji": "🧺"},
}


# ─────────────────────────────────────────────────────────
# BONUS HELPERS
# ─────────────────────────────────────────────────────────
def _roll_treasure(roll_type: str, is_first: bool, second_roll: bool, extra_chance: float = 0.0, decay: float = 0.0) -> bool:
    """
    Roll xem treasure loại roll_type có rơi không.
    - roll_type   : "crate" | "coin" | "item1099"
    - is_first    : True → dùng FIRST_DROP_CHANCE riêng từng loại
    - second_roll : True → nhân × SECOND_ROLL_MULTIPLIER (0.5)
    - extra_chance: bonus từ treasure_hunt effect (raw, × 100 để ra %)
    - decay       : tổng hệ số giảm tích luỹ (0.0 → 1.0), áp dụng sau mỗi lần ra treasure
    """
    if roll_type == "crate":
        base = CRATE_FIRST_DROP_CHANCE if is_first else CRATE_DROP_CHANCE
    elif roll_type == "coin":
        base = COIN_FIRST_DROP_CHANCE if is_first else COIN_DROP_CHANCE
    else:  # item1099
        base = ITEM_1099_FIRST_DROP_CHANCE if is_first else ITEM_1099_DROP_CHANCE
    base += extra_chance * 100
    base *= max(0.0, 1.0 - decay)
    if second_roll:
        base *= SECOND_ROLL_MULTIPLIER
    return random.uniform(0, 100) < base


def _roll_crate_id() -> str:
    """Sub-roll xác định loại crate theo bảng tỉ lệ."""
    r = random.uniform(0, 100)
    cumulative = 0
    for crate_id, weight in CRATE_POOL:
        cumulative += weight
        if r < cumulative:
            return crate_id
    return "001"  # fallback


def _get_bonus_data(user: dict, now: int) -> dict:
    """
    Trả về dict hunt_bonus của user, reset tự động nếu hết chu kỳ.
    Fields:
      count               – số lần bonus đã dùng trong chu kỳ (chung cả 3 loại, max 10)
      reset_at            – timestamp kết thúc chu kỳ
      item1099_dropped    – True nếu item 1099 đã rơi trong chu kỳ (hết 60%)
      crate_first_dropped – True nếu đã từng ra crate trong chu kỳ (hết 60%)
      coin_first_dropped  – True nếu đã từng ra coin trong chu kỳ (hết 60%)
    """
    bonus = user.setdefault("hunt_bonus", {
        "count": 0, "reset_at": 0,
        "drop_count":          0,
        "item1099_dropped":    False,
        "crate_first_dropped": False,
        "coin_first_dropped":  False,
    })
    if now >= bonus["reset_at"]:
        bonus["count"]               = 0
        bonus["reset_at"]            = now + BONUS_RESET_SEC
        bonus["drop_count"]          = 0
        bonus["item1099_dropped"]    = False
        bonus["crate_first_dropped"] = False
        bonus["coin_first_dropped"]  = False
    # Backward-compat
    bonus.setdefault("drop_count",          0)
    bonus.setdefault("item1099_dropped",    False)
    bonus.setdefault("crate_first_dropped", False)
    bonus.setdefault("coin_first_dropped",  False)
    return bonus


def _crate_display(crate_id: str) -> tuple[str, str]:
    """Trả về (emoji, name) của crate."""
    info  = CRATES.get(crate_id) or _CRATE_FALLBACK.get(crate_id, {})
    emoji = info.get("emoji", "📦")
    name  = info.get("name",  f"Crate {crate_id}")
    return emoji, name



# ─────────────────────────────────────────────────────────
# EQUIPPED DISPLAY
# ─────────────────────────────────────────────────────────
def _equipped_display(equipped: list, user: dict | None = None) -> str:
    """Display 3 equipped slots with support for upgraded weapons."""
    lines  = []
    slots  = list(equipped) + [None] * (3 - len(equipped))
    wi_map = {
        wi["uid"]: wi
        for wi in (user or {}).get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }

    for i, wid in enumerate(slots[:3], 1):
        if wid is None:
            lines.append(f"  `[{i}]` — trống")
        elif wid in wi_map:
            wi = wi_map[wid]
            w  = get_weapon_by_id(wi.get("base_id", ""))
            nm = w["name"]  if w else wid
            em = w["emoji"] if w else "⚔️"
            lv     = wi.get("level", 1)
            broken = wi.get("broken", False)
            if broken:
                lines.append(f"  `[{i}]` {em} ~~**{nm}**~~ _(Lv {lv})_ **Broken**")
            else:
                lines.append(f"  `[{i}]` {em} **{nm}** _(Lv {lv})_")
        else:
            w = get_weapon_by_id(get_base_id(wid))
            if w:
                lines.append(f"  `[{i}]` {w['emoji']} **{w['name']}**")
            else:
                lines.append(f"  `[{i}]` `{wid}`")

    return "\n".join(lines)


def _equipped_display_compact(equipped: list, user: dict | None = None) -> str:
    """Compact single-line equipped display dùng cho display_mode='compact'."""
    wi_map = {
        wi["uid"]: wi
        for wi in (user or {}).get("weapon_instances", [])
        if isinstance(wi, dict) and "uid" in wi
    }
    slots       = list(equipped) + [None] * (3 - len(equipped))
    parts       = []
    empty_count = 0

    for wid in slots[:3]:
        if wid is None:
            empty_count += 1
            parts.append("—")
        elif wid in wi_map:
            wi_item = wi_map[wid]
            w       = get_weapon_by_id(wi_item.get("base_id", ""))
            nm      = w["name"]  if w else wid
            em      = w["emoji"] if w else "⚔️"
            lv      = wi_item.get("level", 1)
            sup     = _lv_superscript(lv)
            broken  = wi_item.get("broken", False)
            if broken:
                parts.append(f"{em}~~**{nm}**~~ {sup} (lv {lv})")
            else:
                parts.append(f"{em}**{nm}** {sup} (lv {lv})")
        else:
            w = get_weapon_by_id(get_base_id(wid))
            if w:
                parts.append(f"{w['emoji']}**{w['name']}**")
            else:
                parts.append(f"`{wid}`")

    result = f"{SWORD_EMOJI} | " + " ".join(parts)
    if empty_count > 0:
        result += f" ({empty_count} ô chưa trang bị)"
    return result


def _grant_exp_to_equipped(user: dict, found: list, equipped: list) -> None:
    """
    Grant EXP cho tất cả weapon đang equipped.
    Chỉ grant cho UID có dấu "-".
    Mutate user in-place.
    """
    if not found:
        return

    active = []
    for w in equipped:
        if w is None:
            continue
        if "-" not in str(w):
            print(f"⚠️  equipped weapon '{w}' is not a UID — skipping EXP")
            continue
        active.append(w)

    if not active:
        return

    total_exp = calc_hunt_exp(found)
    if total_exp <= 0:
        return

    exp_each = max(1, total_exp // len(active))
    for wid in active:
        grant_weapon_exp(user, wid, exp_each)


# ─────────────────────────────────────────────────────────
# INTERNAL: core hunt logic (shared prefix + slash)
# ─────────────────────────────────────────────────────────
async def _run_hunt(
    author_id: int,
    author_mention: str,
    display_name: str,
    send_fn,          # async (content=, components=, flags=) → None
    send_bonus_fn,    # async (content=) → None  (tin nhắn riêng)
) -> None:
    uid = str(author_id)

    async with get_user_lock(uid):
        try:
            # ── Load data ───────────────────────────────────────
            try:
                data = load_data(uid)
                user = get_user(uid, data)
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to get user data: `{e}`")

            try:
                equipped = user.get("equipped", [])
                if not isinstance(equipped, list) or len(equipped) != 3:
                    equipped = [None, None, None]
                    user["equipped"] = equipped
            except Exception as e:
                return await send_fn(content=f"{ERR} | Invalid equipped weapons data: `{e}`")

            # ── Cooldown ────────────────────────────────────────
            try:
                now = int(time.time())
                try:
                    actual_cd = calc_hunt_cooldown(equipped, float(HUNT_CD_SEC), user)
                except Exception as e:
                    actual_cd = float(HUNT_CD_SEC)
                    print(f"⚠️  Warning: calc_hunt_cooldown failed: {e}, using default CD")

                last_hunt = user.get("hunt_cd", 0)
                if now - last_hunt < actual_cd:
                    remaining = int(actual_cd - (now - last_hunt))
                    return await send_fn(content=f"{ERR} | Hồi chiêu còn **{remaining}s**.")
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to check cooldown: `{e}`")

            # ── Effects & items ─────────────────────────────────
            try:
                effects_full = parse_effects_upgraded(equipped, user)
            except Exception as e:
                print(f"⚠️  Warning: parse_effects_upgraded failed: {e}, using fallback")
                effects_full = parse_effects(equipped, user)

            try:
                found = roll_hunt_items(equipped, user)
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to roll hunt items: `{e}`")

            try:
                if found:
                    _grant_exp_to_equipped(user, found, equipped)
            except Exception as e:
                print(f"⚠️  Warning: weapon exp grant failed: {e}")

            try:
                just_broken = decrease_durability(user, equipped, effects_full)
            except Exception as e:
                just_broken = []
                print(f"⚠️  Warning: durability decrease failed: {e}")

            # ── Hunt settings (color + display mode) ────────────
            try:
                _hs      = _get_hunt_settings(user)
                _compact = _hs.get("display_mode", "normal") == "compact"
                _raw_col = _hs.get("color", 0x4CAF50)
                _accent  = discord.Color(int(_raw_col)) if _raw_col is not None else None
            except Exception:
                _compact = False
                _accent  = discord.Color(0x4CAF50)

            # ── Build Components v2 Container ───────────────────
            try:
                cv2_children = []

                # ── Header ──────────────────────────────────────
                cv2_children.append(
                    discord.ui.TextDisplay(f"## {SKULL_EMOJI}  {display_name} đi săn!")
                )

                # ── Menu select (ngay trong container, dưới title) ─
                _menu_ar = discord.ui.ActionRow(
                    discord.ui.StringSelect(
                        custom_id=f"hunt_menu_{author_id}",
                        placeholder="⚙️ Cài đặt hunt...",
                        options=[
                            discord.SelectOption(
                                label="Đổi màu container",
                                value="switch_color",
                                description="Chọn màu accent cho container hunt",
                                emoji="🎨",
                            ),
                            discord.SelectOption(
                                label="Đổi chế độ hiển thị",
                                value="switch_display",
                                description="Chuyển giữa Normal và Compact",
                                emoji="📋",
                            ),
                        ],
                        min_values=1,
                        max_values=1,
                    )
                )
                cv2_children.append(_menu_ar)

                if not _compact:
                    cv2_children.append(discord.ui.Separator())

                # ── Items ────────────────────────────────────────
                if not found:
                    items_text = (
                        "| _Không tìm được gì lần này..._"
                        if _compact else
                        "_Bạn không tìm được gì lần này..._"
                    )
                else:
                    lines         = []
                    compact_parts = []
                    for item in found:
                        if item.get("special") == "egg":
                            try:
                                eggs = handle_egg(user)
                                for egg in eggs:
                                    lines.append(f"{egg['emoji']}  **{egg['name']}**")
                                    compact_parts.append(f"{egg['emoji']}**{egg['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: handle_egg failed: {e}")
                                lines.append("🥚  **Egg** (hatching error)")
                                compact_parts.append("🥚**Egg**")
                        else:
                            try:
                                add_item(user, item["id"])
                                lines.append(f"{item['emoji']}  **{item['name']}**")
                                compact_parts.append(f"{item['emoji']}**{item['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: add_item failed for {item.get('id')}: {e}")
                                lines.append(
                                    f"{item['emoji']}  **{item.get('name', 'Unknown')}** (add error)"
                                )
                                compact_parts.append(
                                    f"{item.get('emoji', '?')}**{item.get('name', '?')}**"
                                )
                    if _compact:
                        items_text = (
                            "| " + "".join(compact_parts)
                            if compact_parts else "| _Lỗi hiển thị vật phẩm_"
                        )
                    else:
                        items_text = "\n".join(lines) if lines else "_Lỗi hiển thị vật phẩm_"

                cv2_children.append(discord.ui.TextDisplay(items_text))
                if not _compact:
                    cv2_children.append(discord.ui.Separator())

                # ── Equipped ─────────────────────────────────────
                try:
                    if _compact:
                        equipped_text = _equipped_display_compact(equipped, user)
                    else:
                        equipped_text = (
                            f"**{SWORD_EMOJI} Vũ khí đang trang bị**\n"
                            + _equipped_display(equipped, user)
                        )
                except Exception as e:
                    print(f"⚠️  Warning: equipped display failed: {e}")
                    equipped_text = (
                        f"{SWORD_EMOJI} | _Error displaying weapons_"
                        if _compact else
                        f"**{SWORD_EMOJI} Vũ khí đang trang bị**\n_Error displaying weapons_"
                    )

                cv2_children.append(discord.ui.TextDisplay(equipped_text))

                # ── Footer — EXP per weapon ──────────────────────
                try:
                    active_slots = [
                        w for w in equipped
                        if w is not None and "-" in str(w)
                    ]
                    total_exp    = calc_hunt_exp(found) if found else 0
                    weapon_count = len(active_slots)

                    if _compact:
                        exp_val     = total_exp if (weapon_count > 0 and total_exp > 0) else 0
                        footer_text = f"# +{exp_val:,}xp  (+{exp_val:,} exp).\n------"
                    else:
                        if weapon_count > 0 and total_exp > 0:
                            footer_text = f"-# {weapon_count} Weapon  | +{total_exp:,} exp"
                        elif weapon_count > 0:
                            footer_text = f"-# {weapon_count} Weapon  | +0 exp"
                        else:
                            footer_text = "-# Trang bị weapon để nhận EXP khi hunt!"
                except Exception as e:
                    print(f"⚠️  Warning: footer failed: {e}")
                    footer_text = (
                        "# +0xp  (+0 exp).\n------"
                        if _compact else
                        f"-# Cooldown: {HUNT_CD_SEC}s"
                    )

                if not _compact:
                    cv2_children.append(discord.ui.Separator())
                cv2_children.append(discord.ui.TextDisplay(footer_text))

                # ── Assemble container ──────────────────────────
                # ActionRow (StringSelect menu) nằm trong Container ngay dưới title
                container = discord.ui.Container(*cv2_children, accent_color=_accent)

            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to build response: `{e}`")

            # ── Cooldown save ───────────────────────────────────
            try:
                user["hunt_cd"] = now
            except Exception as e:
                print(f"⚠️  Warning: hunt_cd update failed: {e}")

            # ── Broken weapon messages ──────────────────────────
            broken_msgs = []
            try:
                if just_broken:
                    wi_map_b = {
                        wi["uid"]: wi
                        for wi in user.get("weapon_instances", [])
                        if isinstance(wi, dict) and "uid" in wi
                    }
                    for wid in just_broken:
                        wi_b    = wi_map_b.get(str(wid), {})
                        base_id = wi_b.get("base_id", "")
                        w_b     = get_weapon_by_id(base_id) if base_id else None
                        name_b  = w_b["name"]  if w_b else str(wid)
                        em_b    = w_b["emoji"] if w_b else "⚔️"
                        broken_msgs.append(
                            f"Weapon {em_b} **{name_b}** hiện đang bị hỏng, "
                            f"dùng lệnh `dtn repair` để sửa ngay!"
                        )
            except Exception as e:
                print(f"⚠️  Warning: broken weapon message build failed: {e}")

            # ── Bonus roll ──────────────────────────────────────
            bonus_msgs = []
            total_coins = 0

            try:
                bonus             = _get_bonus_data(user, now)
                treasure_hunt_val = effects_full.get("treasure_hunt", 0.0)

                # ── 2 roll: mỗi roll random 1 loại treasure ────────
                for roll_idx in range(2):
                    if bonus["count"] >= BONUS_MAX:
                        break
                    roll_type   = random.choice(_TREASURE_TYPES)
                    second_roll = (roll_idx == 1)
                    decay       = bonus["drop_count"] * TREASURE_DECAY_PER_HIT

                    if roll_type == "crate":
                        is_first = not bonus["crate_first_dropped"]
                        hit = _roll_treasure(
                            roll_type    = "crate",
                            is_first     = is_first,
                            second_roll  = second_roll,
                            extra_chance = treasure_hunt_val,
                            decay        = decay,
                        )
                        if hit:
                            crate_id  = _roll_crate_id()
                            crate_key = f"crate_{crate_id}"
                            add_item(user, crate_key)
                            bonus["count"] += 1
                            bonus["drop_count"] += 1
                            bonus["crate_first_dropped"] = True

                            emoji, name  = _crate_display(crate_id)
                            remaining    = BONUS_MAX - bonus["count"]
                            reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                            bonus_msgs.append(
                                f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                                f"{emoji} **{name}**!\n"
                                f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                                f"(reset sau {reset_in_min} phút)"
                            )

                    elif roll_type == "coin":
                        is_first = not bonus["coin_first_dropped"]
                        hit = _roll_treasure(
                            roll_type    = "coin",
                            is_first     = is_first,
                            second_roll  = second_roll,
                            extra_chance = treasure_hunt_val,
                            decay        = decay,
                        )
                        if hit:
                            if not bonus["coin_first_dropped"]:
                                c = random.randint(COIN_FIRST_AMOUNT_MIN, COIN_FIRST_AMOUNT_MAX)
                            else:
                                c = random.randint(COIN_AMOUNT_MIN, COIN_AMOUNT_MAX)
                            total_coins += c
                            bonus["count"] += 1
                            bonus["drop_count"] += 1
                            bonus["coin_first_dropped"] = True

                            remaining    = BONUS_MAX - bonus["count"]
                            reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                            bonus_msgs.append(
                                f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                                f"**{c:,}** {COIN_EMOJI} **Coin**!\n"
                                f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                                f"(reset sau {reset_in_min} phút)"
                            )

                    else:  # item1099
                        is_first = not bonus["item1099_dropped"]
                        hit = _roll_treasure(
                            roll_type    = "item1099",
                            is_first     = is_first,
                            second_roll  = second_roll,
                            extra_chance = treasure_hunt_val,
                            decay        = decay,
                        )
                        if hit:
                            try:
                                item_info  = get_item_by_id("1099")
                                item_emoji = item_info.get("emoji", "🔮") if item_info else "🔮"
                                item_name  = item_info.get("name",  "Item 1099") if item_info else "Item 1099"
                                qty = random.randint(ITEM_1099_AMOUNT_MIN, ITEM_1099_AMOUNT_MAX)
                                for _ in range(qty):
                                    add_item(user, "1099")
                                bonus["count"] += 1
                                bonus["drop_count"] += 1
                                bonus["item1099_dropped"] = True
                                reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                                bonus_msgs.append(
                                    f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                                    f"**{qty}x** {item_emoji} **{item_name}**!\n"
                                    f"-# Bonus còn lại: **{BONUS_MAX - bonus['count']}/{BONUS_MAX}** "
                                    f"(reset sau {reset_in_min} phút)"
                                )
                            except Exception as e:
                                print(f"⚠️  Warning: item 1099 add failed: {e}")

            except Exception as e:
                print(f"⚠️  Warning: bonus roll failed: {e}")

            # ── Save ────────────────────────────────────────────
            try:
                ok = await save_data(data, uid)
                if not ok:
                    return await send_fn(content=f"{ERR} | Failed to save data.")
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to save data: `{e}`")

            if total_coins > 0:
                try:
                    await update_balance_safe(author_id, total_coins)
                except Exception as e:
                    print(f"⚠️  Warning: update_balance_safe (bonus coin) failed: {e}")

            # ── Quest ───────────────────────────────────────────
            try:
                add_quest_progress(author_id, "hunts")
                if found:
                    add_quest_progress(author_id, "items_collected", len(found))
                    rare_count = sum(
                        1 for it in found
                        if it.get("rarity") in ("rare", "epic", "legendary")
                    )
                    if rare_count > 0:
                        add_quest_progress(author_id, "rare_collected", rare_count)
            except Exception as e:
                print(f"⚠️  Warning: quest progress update failed: {e}")

            # ── Send ────────────────────────────────────────────
            try:
                await send_fn(components=[container])
            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to send response: `{e}`")

            if broken_msgs:
                for msg in broken_msgs:
                    try:
                        await send_bonus_fn(content=msg)
                    except Exception as e:
                        print(f"⚠️  Warning: failed to send broken weapon message: {e}")

            if bonus_msgs:
                for msg in bonus_msgs:
                    try:
                        await send_bonus_fn(content=msg)
                    except Exception as e:
                        print(f"⚠️  Warning: failed to send bonus message: {e}")

        except Exception as e:
            print(f"❌ CRITICAL ERROR in hunt command: {type(e).__name__}: {e}")
            try:
                await send_fn(content=f"❌ **Critical Error**: `{e}`\nPlease contact the bot owner.")
            except Exception as send_err:
                print(f"❌ Failed to send error message: {send_err}")


async def _run_hunt_bonus(author_id: int, display_name: str, send_fn) -> None:
    """Core logic hunt bonus — dùng chung prefix + slash."""
    try:
        uid     = str(author_id)
        data    = load_data(uid)
        user    = get_user(uid, data)
        now     = int(time.time())

        old_reset  = user.get("hunt_bonus", {}).get("reset_at", 0)
        bonus      = _get_bonus_data(user, now)
        needs_save = (bonus["reset_at"] != old_reset)

        remaining    = BONUS_MAX - bonus["count"]
        reset_in_sec = max(0, bonus["reset_at"] - now)
        h, rem       = divmod(reset_in_sec, 3600)
        m            = rem // 60

        if needs_save:
            try:
                await save_data(data, uid)
            except Exception as e:
                print(f"⚠️  Warning: hunt_bonus save failed: {e}")

        await send_fn(
            content=(
                f"🎁 **Bonus hunt của {display_name}**\n"
                f"• Đã dùng: **{bonus['count']}/{BONUS_MAX}** lần\n"
                f"• Còn lại: **{remaining}** lần\n"
                f"• Reset sau: **{h}h {m}m**"
            )
        )

    except Exception as e:
        print(f"❌ Error in hunt_bonus: {type(e).__name__}: {e}")
        await send_fn(content=f"❌ Error: `{e}`")


# ─────────────────────────────────────────────────────────
# PREFIX SEND HELPER  (discord.py 2.7+: ctx.send hỗ trợ components= trực tiếp)
# ─────────────────────────────────────────────────────────
def _make_prefix_send(ctx):
    """Trả về send_fn dùng components= trực tiếp (2.7+), không wrap LayoutView."""
    async def _send(content=None, components=None, **_):
        if components:
            return await ctx.send(components=components, flags=_cv2_flags)
        return await ctx.send(content=content)
    return _send


# ─────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────
class RPGHunt(commands.Cog):
    """Hunt command with comprehensive error handling."""

    def __init__(self, bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════
    # PREFIX COMMANDS
    # ══════════════════════════════════════════════════════

    @commands.group(name="hunt", aliases=["h"], invoke_without_command=True)
    async def hunt(self, ctx):
        """dtn hunt — đi săn vật phẩm."""
        await _run_hunt(
            author_id      = ctx.author.id,
            author_mention = ctx.author.mention,
            display_name   = ctx.author.display_name,
            send_fn        = _make_prefix_send(ctx),
            send_bonus_fn  = lambda content=None, **_: ctx.send(content=content),
        )

    @hunt.command(name="bonus")
    async def hunt_bonus(self, ctx):
        """dtn hunt bonus — xem số lần bonus còn lại trong chu kỳ."""
        await _run_hunt_bonus(
            author_id    = ctx.author.id,
            display_name = ctx.author.display_name,
            send_fn      = lambda content=None, **_: ctx.send(content=content),
        )

    # ══════════════════════════════════════════════════════
    # SLASH COMMANDS  — plain commands, không dùng Group
    # → /hunt invoke trực tiếp, không cần /hunt go
    # (slash /hunt bonus không khả thi nếu không có Group,
    #  dùng prefix "hunt bonus" thay thế)
    # ══════════════════════════════════════════════════════

    @app_commands.command(name="hunt", description="Đi săn vật phẩm")
    @_slash_guild_only()
    async def slash_hunt(self, interaction: discord.Interaction):
        """/hunt"""
        await interaction.response.defer()

        async def _send(content=None, components=None, **_):
            if components:
                await interaction.followup.send(
                    components=components,
                    flags=_cv2_flags,
                )
            else:
                await interaction.followup.send(content=content)

        async def _send_bonus(content=None, **_):
            await interaction.followup.send(content=content)

        await _run_hunt(
            author_id      = interaction.user.id,
            author_mention = interaction.user.mention,
            display_name   = interaction.user.display_name,
            send_fn        = _send,
            send_bonus_fn  = _send_bonus,
        )

    # ══════════════════════════════════════════════════════
    # INTERACTION HANDLER — Hunt buttons & selects
    # ══════════════════════════════════════════════════════

    @commands.Cog.listener("on_interaction")
    async def on_hunt_buttons(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        data = interaction.data or {}
        cid  = data.get("custom_id", "")
        uid  = str(interaction.user.id)

        # ── Route theo custom_id ─────────────────────────────
        if cid == f"hunt_menu_{uid}":
            values = data.get("values", [])
            chosen = values[0] if values else ""
            if chosen == "switch_color":
                await self._handle_switch_color(interaction)
            elif chosen == "switch_display":
                await self._handle_switch_display(interaction)

        elif cid == f"hunt_colorsel_{uid}":
            values = data.get("values", [])
            await self._handle_color_select(interaction, values[0] if values else "none")

        elif cid == f"hunt_confirmdsp_{uid}":
            await self._handle_confirm_display(interaction)

        elif cid == f"hunt_canceldsp_{uid}":
            await self._handle_cancel_display(interaction)

        # Người khác dùng menu của user này → báo lỗi nhẹ
        elif any(cid.startswith(p) for p in (
            "hunt_menu_",
            "hunt_colorsel_", "hunt_confirmdsp_", "hunt_canceldsp_",
        )):
            try:
                await interaction.response.send_message(
                    "Nút này không dành cho bạn.", ephemeral=True
                )
            except Exception:
                pass

    # ── Switch color: hiển thị ephemeral chọn màu ────────────
    async def _handle_switch_color(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        color_select = discord.ui.StringSelect(
            custom_id=f"hunt_colorsel_{uid}",
            placeholder="Chọn màu container...",
            options=[
                discord.SelectOption(label="Không màu",  value="none",   description="Xóa màu container"),
                discord.SelectOption(label="Xanh dương", value="blue",   description="#3498DB"),
                discord.SelectOption(label="Đỏ",         value="red",    description="#E74C3C"),
                discord.SelectOption(label="Tím",        value="purple", description="#9B59B6"),
                discord.SelectOption(label="Đen",        value="black",  description="#23272A"),
                discord.SelectOption(label="Trắng",      value="white",  description="#FFFFFF"),
            ],
        )
        # ActionRow trong Container — hợp lệ 2.7+ (ref §3.7)
        _container = discord.ui.Container(
            discord.ui.TextDisplay("### 🎨 Màu container hunt"),
            discord.ui.Separator(),
            discord.ui.ActionRow(color_select),
        )
        try:
            await interaction.response.send_message(
                components=[_container],
                flags=_cv2_flags,
                ephemeral=True,
            )
        except Exception as e:
            print(f"⚠️  Warning: switch_color send failed: {e}")

    # ── Switch display: hiển thị ephemeral xác nhận ──────────
    async def _handle_switch_display(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            _data = load_data(uid)
            _user = get_user(uid, _data)
            _hs   = _get_hunt_settings(_user)
            cur   = _hs.get("display_mode", "normal")
        except Exception:
            cur = "normal"

        cur_label = "thường" if cur == "normal" else "gọn"
        new_label = "gọn"    if cur == "normal" else "thường"

        preview = (
            "| item1**Tên item**item2**Tên item**\n"
            f"{SWORD_EMOJI} | emoji**Tên vũ khí** ᴸⱽ⁰¹ (lv 1) — (2 ô chưa trang bị)\n"
            "# +36xp  (+36 exp).\n------"
        )
        # ActionRow trong Container — hợp lệ 2.7+ (ref §3.7)
        _container = discord.ui.Container(
            discord.ui.TextDisplay(
                f"### 📋 Chuyển chế độ hiển thị?\n"
                f"Hiện tại: **{cur_label}** → Chuyển sang: **{new_label}**"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**Preview chế độ gọn:**\n{preview}"),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="Xác nhận",
                    custom_id=f"hunt_confirmdsp_{uid}",
                    style=discord.ButtonStyle.success,
                ),
                discord.ui.Button(
                    label="Hủy",
                    custom_id=f"hunt_canceldsp_{uid}",
                    style=discord.ButtonStyle.danger,
                ),
            ),
        )
        try:
            await interaction.response.send_message(
                components=[_container],
                flags=_cv2_flags,
                ephemeral=True,
            )
        except Exception as e:
            print(f"⚠️  Warning: switch_display send failed: {e}")

    # ── Áp dụng màu được chọn ────────────────────────────────
    async def _handle_color_select(self, interaction: discord.Interaction, color_key: str):
        uid = str(interaction.user.id)
        try:
            async with get_user_lock(uid):
                _data = load_data(uid)
                _user = get_user(uid, _data)
                _hs   = _get_hunt_settings(_user)
                if color_key in _COLOR_MAP:
                    _hs["color"] = _COLOR_MAP[color_key]
                ok = await save_data(_data, uid)
            label = _COLOR_LABELS.get(color_key, color_key)
            if ok:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(f"{OK} Đã đổi màu container sang **{label}**!"),
                )
            else:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(f"{ERR} Lưu thất bại, thử lại sau."),
                )
            await interaction.response.edit_message(components=[_c], flags=_cv2_flags)
        except Exception as e:
            print(f"⚠️  Warning: color_select failed: {e}")
            try:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(f"{ERR} Lỗi: `{e}`"),
                )
                await interaction.response.edit_message(components=[_c], flags=_cv2_flags)
            except Exception:
                pass

    # ── Xác nhận chuyển display mode ─────────────────────────
    async def _handle_confirm_display(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            async with get_user_lock(uid):
                _data    = load_data(uid)
                _user    = get_user(uid, _data)
                _hs      = _get_hunt_settings(_user)
                cur      = _hs.get("display_mode", "normal")
                new_mode = "compact" if cur == "normal" else "normal"
                _hs["display_mode"] = new_mode
                ok = await save_data(_data, uid)
            label = "gọn" if new_mode == "compact" else "thường"
            if ok:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(
                        f"{OK} Đã chuyển sang chế độ hiển thị **{label}**!\n"
                        f"-# Hunt tiếp theo sẽ áp dụng chế độ mới."
                    ),
                )
            else:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(f"{ERR} Lưu thất bại, thử lại sau."),
                )
            await interaction.response.edit_message(components=[_c], flags=_cv2_flags)
        except Exception as e:
            print(f"⚠️  Warning: confirm_display failed: {e}")
            try:
                _c = discord.ui.Container(
                    discord.ui.TextDisplay(f"{ERR} Lỗi: `{e}`"),
                )
                await interaction.response.edit_message(components=[_c], flags=_cv2_flags)
            except Exception:
                pass

    # ── Hủy chuyển display mode ───────────────────────────────
    async def _handle_cancel_display(self, interaction: discord.Interaction):
        try:
            _c = discord.ui.Container(
                discord.ui.TextDisplay("Đã hủy."),
            )
            await interaction.response.edit_message(components=[_c], flags=_cv2_flags)
        except Exception as e:
            print(f"⚠️  Warning: cancel_display failed: {e}")


# ─────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────
async def setup(bot):
    await bot.add_cog(RPGHunt(bot))


async def teardown(bot):
    await bot.remove_cog("RPGHunt")
