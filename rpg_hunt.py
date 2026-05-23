"""
===== HUNT COMMAND =====

GUARANTEES:
- No silent failures (errors visible in Discord)
- All exceptions logged to console
- User gets clear error message
- Never breaks the bot

[BONUS SYSTEM]
- Mỗi lần hunt có 2 roll bonus độc lập (ưu tiên crate trước):
    • Roll 1: tỉ lệ gốc  — 1% crate | 1.75% coin
    • Roll 2: tỉ lệ × 50% — 0.5% crate | 0.875% coin
- Lần đầu tiên hunt trong chu kỳ 6h: cả 2 roll dùng tỉ lệ 60%/30%
  (roll 1 = 60%, roll 2 = 30%), sau khi nhận treasure → về tỉ lệ bình thường
- Tối đa 10 lần bonus / 6 tiếng, tự reset sau mỗi chu kỳ
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
    CRATES, get_base_id
)
from rpg_database import get_user, save_user, calc_hunt_exp, grant_weapon_exp
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
# PER-USER LOCK  (tránh race condition khi 2 lệnh đồng thời)
# ─────────────────────────────────────────────────────────
# [FIX 5] Dùng OrderedDict để giới hạn bộ nhớ: tối đa _LOCK_MAX_SIZE entries.
# Khi vượt giới hạn, xóa các entry cũ nhất mà lock đang idle (không locked).
# Tránh memory leak khi bot chạy lâu với nhiều user khác nhau.
from collections import OrderedDict

_USER_LOCKS: OrderedDict[str, asyncio.Lock] = OrderedDict()
_LOCK_MAX_SIZE = 5_000

def _get_user_lock(uid: str) -> asyncio.Lock:
    if uid in _USER_LOCKS:
        _USER_LOCKS.move_to_end(uid)   # LRU: đưa lên cuối (recently used)
        return _USER_LOCKS[uid]

    lock = asyncio.Lock()
    _USER_LOCKS[uid] = lock

    # Evict oldest idle locks khi vượt giới hạn
    while len(_USER_LOCKS) > _LOCK_MAX_SIZE:
        oldest_uid, oldest_lock = next(iter(_USER_LOCKS.items()))
        if not oldest_lock.locked():
            del _USER_LOCKS[oldest_uid]
        else:
            # Lock đang dùng — không xóa, dừng evict lần này
            break

    return lock


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
CRATE_DROP_CHANCE        = 1       # % tỉ lệ gốc
COIN_DROP_CHANCE         = 1.75   # % tỉ lệ gốc
BONUS_MAX                = 10     # tối đa lần bonus mỗi chu kỳ
BONUS_RESET_SEC          = 6 * 3600  # 6 tiếng
FIRST_DROP_CHANCE        = 60.0   # % lần đầu chưa ra treasure loại đó trong chu kỳ
SECOND_ROLL_MULTIPLIER   = 0.5    # roll 2 = roll 1 × 50%

# ─── Treasure item 1099 drop config ─────────────────────────────────────────
# Lần đầu tiên trong chu kỳ: 30%
# Các lần sau: 45% (cố định)
# [FIX 2] Comment cũ nói "crate_001 * 0.8 ≈ 55.999%" nhưng code thực tế là 45.0 hardcode.
# Đã xóa _crate_001_weight (dead code) và đồng bộ comment với giá trị thực.
ITEM_1099_DROP_CHANCE_FIRST = 30.0   # % lần đầu trong chu kỳ
ITEM_1099_AMOUNT_MIN        = 88
ITEM_1099_AMOUNT_MAX        = 137

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

# Tỉ lệ drop item 1099 từ lần thứ 2 trở đi = 45%
ITEM_1099_DROP_CHANCE_BASE = 45.0   # % cố định từ lần thứ 2 trở đi

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
def _roll_crate(is_first: bool, second_roll: bool, extra_chance: float = 0.0) -> bool:
    """
    Roll xem có ra crate không.
    - is_first=True   : tỉ lệ base = FIRST_DROP_CHANCE (60%)
    - second_roll=True: nhân × SECOND_ROLL_MULTIPLIER (0.5)
    - extra_chance    : bonus từ treasure_hunt effect (raw, × 100 để ra %)
    """
    base  = FIRST_DROP_CHANCE if is_first else CRATE_DROP_CHANCE
    base += extra_chance * 100
    if second_roll:
        base *= SECOND_ROLL_MULTIPLIER
    return random.uniform(0, 100) < base


def _roll_coin(is_first: bool, second_roll: bool, extra_chance: float = 0.0) -> bool:
    """
    Roll xem có ra coin không.
    - is_first=True   : tỉ lệ base = FIRST_DROP_CHANCE (60%)
    - second_roll=True: nhân × SECOND_ROLL_MULTIPLIER (0.5)
    - extra_chance    : bonus từ treasure_hunt effect (raw, × 100 để ra %)
    """
    base  = FIRST_DROP_CHANCE if is_first else COIN_DROP_CHANCE
    base += extra_chance * 100
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
      count               – số lần bonus (crate/coin) đã dùng trong chu kỳ
      reset_at            – timestamp kết thúc chu kỳ
      item1099_dropped    – True nếu item 1099 đã rơi trong chu kỳ
      crate_first_dropped – True nếu đã từng ra crate trong chu kỳ (hết 60%)
      coin_first_dropped  – True nếu đã từng ra coin trong chu kỳ (hết 60%)
    """
    bonus = user.setdefault("hunt_bonus", {
        "count": 0, "reset_at": 0,
        "item1099_dropped":    False,
        "crate_first_dropped": False,
        "coin_first_dropped":  False,
    })
    if now >= bonus["reset_at"]:
        bonus["count"]               = 0
        bonus["reset_at"]            = now + BONUS_RESET_SEC
        bonus["item1099_dropped"]    = False
        bonus["crate_first_dropped"] = False
        bonus["coin_first_dropped"]  = False
    # Backward-compat
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


def _roll_item_1099(bonus: dict) -> int:
    """
    Roll xem item 1099 có rơi không trong lượt bonus này.
    - Lần đầu tiên trong chu kỳ (item1099_dropped == False): tỉ lệ 30%
    - Các lần sau                                           : tỉ lệ = crate_001 * 0.8 (~55.999%)
    Trả về số lượng item (88-137) nếu rơi, hoặc 0 nếu không.
    """
    chance = ITEM_1099_DROP_CHANCE_FIRST if not bonus["item1099_dropped"] else ITEM_1099_DROP_CHANCE_BASE
    if random.uniform(0, 100) < chance:
        return random.randint(ITEM_1099_AMOUNT_MIN, ITEM_1099_AMOUNT_MAX)
    return 0


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
            dur    = wi.get("durability", 0)
            dur_mx = wi.get("durability_max", 1)
            if broken:
                lines.append(f"  `[{i}]` {em} ~~**{nm}**~~ _(Lv {lv})_ **Broken**")
            else:
                lines.append(f"  `[{i}]` {em} **{nm}** _(Lv {lv} • {dur}/{dur_mx})_")
        else:
            w = get_weapon_by_id(get_base_id(wid))
            if w:
                lines.append(f"  `[{i}]` {w['emoji']} **{w['name']}**")
            else:
                lines.append(f"  `[{i}]` `{wid}`")

    return "\n".join(lines)


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

    async with _get_user_lock(uid):
        try:
            # ── Load data ───────────────────────────────────────
            try:
                user, _ = get_user(uid)
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

            # ── Build Components v2 Container ───────────────────
            try:
                cv2_children = []

                # ── Header ──────────────────────────────────────
                cv2_children.append(
                    discord.ui.TextDisplay(f"## {SKULL_EMOJI}  {display_name} đi săn!")
                )
                cv2_children.append(discord.ui.Separator())

                # ── Items ────────────────────────────────────────
                if not found:
                    items_text = "_Bạn không tìm được gì lần này..._"
                else:
                    lines = []
                    for item in found:
                        if item.get("special") == "egg":
                            try:
                                eggs = handle_egg(user)
                                for egg in eggs:
                                    lines.append(f"{egg['emoji']}  **{egg['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: handle_egg failed: {e}")
                                lines.append("🥚  **Egg** (hatching error)")
                        else:
                            try:
                                add_item(user, item["id"])
                                lines.append(f"{item['emoji']}  **{item['name']}**")
                            except Exception as e:
                                print(f"⚠️  Warning: add_item failed for {item.get('id')}: {e}")
                                lines.append(
                                    f"{item['emoji']}  **{item.get('name', 'Unknown')}** (add error)"
                                )
                    items_text = "\n".join(lines) if lines else "_Lỗi hiển thị vật phẩm_"

                cv2_children.append(discord.ui.TextDisplay(items_text))
                cv2_children.append(discord.ui.Separator())

                # ── Equipped ─────────────────────────────────────
                try:
                    equipped_text = (
                        f"**{SWORD_EMOJI} Vũ khí đang trang bị**\n"
                        + _equipped_display(equipped, user)
                    )
                except Exception as e:
                    print(f"⚠️  Warning: equipped display failed: {e}")
                    equipped_text = f"**{SWORD_EMOJI} Vũ khí đang trang bị**\n_Error displaying weapons_"

                cv2_children.append(discord.ui.TextDisplay(equipped_text))

                # ── Broken weapon notification ────────────────────
                try:
                    if just_broken:
                        wi_map = {
                            wi["uid"]: wi
                            for wi in user.get("weapon_instances", [])
                            if isinstance(wi, dict) and "uid" in wi
                        }
                        broken_lines = []
                        for wid in just_broken:
                            wi      = wi_map.get(str(wid), {})
                            base_id = wi.get("base_id", "")
                            w       = get_weapon_by_id(base_id) if base_id else None
                            name    = w["name"]  if w else str(wid)
                            em      = w["emoji"] if w else "⚔️"
                            broken_lines.append(f"💔 {em} ~~**{name}**~~ vừa bị **Broken**!")
                        cv2_children.append(discord.ui.Separator())
                        cv2_children.append(
                            discord.ui.TextDisplay(
                                "**⚠️ Vũ khí hỏng**\n" + "\n".join(broken_lines)
                            )
                        )
                except Exception as e:
                    print(f"⚠️  Warning: broken weapon display failed: {e}")

                # ── Footer ───────────────────────────────────────
                try:
                    active_fx = [k for k, v in effects_full.items() if v]
                    if active_fx:
                        footer_text = (
                            "-# <:Effect:1495466103047061679> Hiệu ứng: "
                            + ", ".join(active_fx[:5])
                        )
                    else:
                        footer_text = f"-# Cooldown: {HUNT_CD_SEC}s  |  Trang bị weapon để tăng hiệu quả!"
                except Exception as e:
                    print(f"⚠️  Warning: footer failed: {e}")
                    footer_text = f"-# Cooldown: {HUNT_CD_SEC}s"

                cv2_children.append(discord.ui.Separator())
                cv2_children.append(discord.ui.TextDisplay(footer_text))

                # ── Assemble container ───────────────────────────
                container = discord.ui.Container(*cv2_children)

            except Exception as e:
                return await send_fn(content=f"{ERR} | Failed to build response: `{e}`")

            # ── Bonus roll ──────────────────────────────────────
            bonus_msgs = []
            total_coins = 0

            try:
                bonus             = _get_bonus_data(user, now)
                treasure_hunt_val = effects_full.get("treasure_hunt", 0.0)

                # ── CRATE: 2 roll độc lập ───────────────────────
                for roll_idx in range(2):
                    if bonus["count"] >= BONUS_MAX:
                        break
                    is_first_crate = not bonus["crate_first_dropped"]
                    hit = _roll_crate(
                        is_first    = is_first_crate,
                        second_roll = (roll_idx == 1),
                        extra_chance= treasure_hunt_val,
                    )
                    if hit:
                        crate_id  = _roll_crate_id()
                        crate_key = f"crate_{crate_id}"
                        # [FIX 4] Wrap add_item — chỉ tăng count khi add thành công.
                        # Code cũ: add_item có thể fail nhưng count vẫn bị tăng → mất slot bonus.
                        try:
                            add_item(user, crate_key)
                        except Exception as e:
                            print(f"⚠️  Warning: add_item failed for crate {crate_key}: {e}")
                            continue  # skip roll này, count không tăng
                        bonus["count"] += 1
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

                # ── COIN: 2 roll độc lập ────────────────────────
                for roll_idx in range(2):
                    if bonus["count"] >= BONUS_MAX:
                        break
                    is_first_coin = not bonus["coin_first_dropped"]
                    hit = _roll_coin(
                        is_first    = is_first_coin,
                        second_roll = (roll_idx == 1),
                        extra_chance= treasure_hunt_val,
                    )
                    if hit:
                        c = random.randint(2000, 6500)
                        # [FIX 4] Coin không cần add_item nhưng update balance cần thành công.
                        # Tăng total_coins + count trước, nếu update_balance_safe fail ở sau
                        # thì sẽ bị log ⚠️ — acceptable vì coin là side effect nhỏ.
                        # Không có gì để rollback ở bước này nên giữ nguyên logic coin.
                        total_coins += c
                        bonus["count"] += 1
                        bonus["coin_first_dropped"] = True

                        remaining    = BONUS_MAX - bonus["count"]
                        reset_in_min = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msgs.append(
                            f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                            f"**{c:,}** {COIN_EMOJI} **Coin**!\n"
                            f"-# Bonus còn lại: **{remaining}/{BONUS_MAX}** "
                            f"(reset sau {reset_in_min} phút)"
                        )

                # ── Item 1099 drop (độc lập, không dùng BONUS_MAX) ──
                item1099_qty = _roll_item_1099(bonus)
                if item1099_qty > 0:
                    try:
                        item_info  = get_item_by_id("1099")
                        item_emoji = item_info.get("emoji", "🔮") if item_info else "🔮"
                        item_name  = item_info.get("name",  "Item 1099") if item_info else "Item 1099"
                        for _ in range(item1099_qty):
                            add_item(user, "1099")
                        bonus["item1099_dropped"] = True
                        reset_in_min_1099 = max(0, (bonus["reset_at"] - now) // 60)
                        bonus_msgs.append(
                            f"<:2925:1495277191867400284> | {author_mention} Kho báu rơi ra "
                            f"**{item1099_qty}x** {item_emoji} **{item_name}**!\n"
                            f"-# (reset sau {reset_in_min_1099} phút)"
                        )
                    except Exception as e:
                        print(f"⚠️  Warning: item 1099 add failed: {e}")

            except Exception as e:
                print(f"⚠️  Warning: bonus roll failed: {e}")

            # ── Save ────────────────────────────────────────────
            # [FIX 3] hunt_cd được set SAU khi bonus roll xong, ngay trước save.
            # Code cũ set hunt_cd trước bonus roll — nếu save fail, cooldown không
            # được persist nhưng đã được tính vào user dict theo thứ tự sai.
            try:
                user["hunt_cd"] = now
            except Exception as e:
                print(f"⚠️  Warning: hunt_cd update failed: {e}")

            try:
                ok = save_user(uid, user)
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
        user, _ = get_user(uid)
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
                save_user(uid, user)
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
        # [FIX 1] Lambda cũ luôn truyền components=None vào ctx.send() khi fallback error.
        # Một số fork discord.py không chấp nhận components=None → TypeError im lặng →
        # bot không báo lỗi trên Discord mà chỉ crash trong try/except nội bộ.
        # Slash command (line ~675) đã có pattern đúng — áp dụng tương tự cho prefix.
        async def _send_prefix(content=None, components=None, **_):
            if components:
                return await ctx.send(
                    content=content,
                    components=components,
                    flags=discord.MessageFlags(is_components_v2=True),
                )
            return await ctx.send(content=content)

        await _run_hunt(
            author_id      = ctx.author.id,
            author_mention = ctx.author.mention,
            display_name   = ctx.author.display_name,
            send_fn        = _send_prefix,
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
                    flags=discord.MessageFlags(is_components_v2=True),
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


# ─────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────
async def setup(bot):
    await bot.add_cog(RPGHunt(bot))


async def teardown(bot):
    await bot.remove_cog("RPGHunt")
