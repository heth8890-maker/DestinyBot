"""
===== rpg_quest.py =====
Hệ thống Daily Quest + Discord Cog (gộp rpg_quest + rpg_question).

Mỗi ngày user nhận 1–2 nhiệm vụ ngẫu nhiên, reset mỗi 24h.
Lưu trữ: MongoDB (collection "quest_data") qua rpg_core.

Reward system:
  - Mỗi quest có 1 reward riêng: HOẶC crate, HOẶC cash — KHÔNG trộn lẫn.
  - Reward được roll tại lúc reset (user thấy trước), KHÔNG roll lại lúc claim.
  - crate_001: phổ biến nhất, có thể nhận 1–3 cái tuỳ độ khó.
  - crate_002–005: tier cao dần, tỉ lệ xuất hiện càng hiếm.

Commands:
  dtn quest          — xem quest hôm nay
  (reward tự động claim + DM khi hoàn thành)

Public API:
  should_reset_quest / reset_quest
  add_quest_progress          ← trả list quest_type vừa hoàn thành
  notify_quest_complete       ← async: auto-claim + gửi thông báo vào channel
  get_current_quests / get_current_quest  (compat)
  claim_quest_reward          ← trả (bool, str, list[dict])
  QUEST_TYPES / QUEST_PROGRESS_KEYS / QUEST_REWARD_POOLS

Cách dùng notify trong Cog khác (vd: rpg_crate.py):
  completed = add_quest_progress(ctx.author.id, "crates_opened")
  if completed:
      await notify_quest_complete(ctx.channel, ctx.author.id, completed)
"""

# ── Standard library ──────────────────────────────────────────────────────────
import random
import time
import logging

# ── Third-party ───────────────────────────────────────────────────────────────
import discord
from discord.ext import commands
import pymongo

# ── Internal ──────────────────────────────────────────────────────────────────
from rpg_core import _get_client, _with_retry, DB_NAME
from cash import update_balance_safe, get_balance
# TODO: import hàm thêm crate vào kho của bạn, ví dụ:
# from inventory import add_item

log = logging.getLogger("rpg_quest")


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTS — EMOJI / DISPLAY
# ════════════════════════════════════════════════════════════════════════════

COIN_EMOJI = "<:Coin:1495831576397742241>"
ERR        = "<:X_:1495466670616219819>"
OK         = "<:Tick:1495466684520206528>"
INFO       = "<:Info:1496098636247863491>"
CLAIMED    = "<:2245:1493575277605949480>"
TICK       = "<:Tick:1495466684520206528>"

# Tên hiển thị & emoji thật cho từng tier crate (lấy từ rpg_crate.py)
CRATE_META: dict[str, dict] = {
    "crate_001": {"name": "Hòm Thường",      "emoji": "<:Uncomon:1495277191867400284>"},
    "crate_002": {"name": "Hòm Hiếm",        "emoji": "<:Craterare:1496191910765920406>"},
    "crate_003": {"name": "Hòm Tối",         "emoji": "<:Darkcrateopen:1498988761936302210>"},
    "crate_004": {"name": "Soul Crate",      "emoji": "<:Opensoulcrate:1498617029077499935>"},
    "crate_005": {"name": "Paradise Crate",  "emoji": "<:Paradise_crate_open:1505052527157051454>"},
}


# ════════════════════════════════════════════════════════════════════════════
# COLLECTION HELPER
# ════════════════════════════════════════════════════════════════════════════

def _col():
    return _get_client()[DB_NAME]["quest_data"]


# ════════════════════════════════════════════════════════════════════════════
# QUEST TYPES — 8 loại nhiệm vụ
# ════════════════════════════════════════════════════════════════════════════

QUEST_TYPES: dict[str, dict] = {
    "hunt_times": {
        "name":         "Săn liên tiếp",
        "description":  "Hoàn thành {target} lần hunt",
        "target":       50,
        "difficulty":   "medium",
        "progress_key": "hunts",
    },
    "sell_items": {
        "name":         "Nhà buôn",
        "description":  "Bán {target} vật phẩm",
        "target":       1000,
        "difficulty":   "hard",
        "progress_key": "items_sold",
    },
    "equip_weapon": {
        "name":         "Trang bị vũ khí",
        "description":  "Trang bị {target} vũ khí (có thể khác nhau)",
        "target":       3,
        "difficulty":   "easy",
        "progress_key": "weapons_equipped",
    },
    "collect_items": {
        "name":         "Collector",
        "description":  "Thu thập {target} vật phẩm",
        "target":       300,
        "difficulty":   "medium",
        "progress_key": "items_collected",
    },
    "sell_weapons": {
        "name":         "Tháo vũ khí",
        "description":  "Bán {target} vũ khí",
        "target":       3,
        "difficulty":   "easy",
        "progress_key": "weapons_sold",
    },
    "open_crates": {
        "name":         "Mở kho báu",
        "description":  "Mở {target} crate",
        "target":       5,
        "difficulty":   "medium",
        "progress_key": "crates_opened",
    },
    "high_rarity": {
        "name":         "Săn hiếm",
        "description":  "Thu thập {target} vật phẩm rare+",
        "target":       30,
        "difficulty":   "hard",
        "progress_key": "rare_collected",
    },
    "trade_success": {
        "name":         "Buôn bán",
        "description":  "Giao dịch thành công {target} lần",
        "target":       2,
        "difficulty":   "hard",
        "progress_key": "trades_done",
    },
}

QUEST_PROGRESS_KEYS = {k: v["progress_key"] for k, v in QUEST_TYPES.items()}
ALL_PROGRESS_KEYS   = list({v["progress_key"] for v in QUEST_TYPES.values()})


# ════════════════════════════════════════════════════════════════════════════
# REWARD POOLS
#
# Mỗi entry: {"weight": int, "reward": {"type": ..., ...}}
#
#   type "crate" → {"type": "crate", "id": "crate_XXX", "amount": int}
#   type "cash"  → {"type": "cash",  "amount": int}
#
# Quy tắc:
#   easy   — crate_001 ×1–2 chiếm đa số; 002–004 hiếm; 005 không xuất hiện.
#   medium — crate_001 ×2–3 chiếm đa số; 002–004 thỉnh thoảng; 005 cực hiếm.
#   hard   — crate_001 ×3 cơ sở; 002–005 xuất hiện đáng kể; 005 vẫn hiếm.
# ════════════════════════════════════════════════════════════════════════════

QUEST_REWARD_POOLS: dict[str, list[dict]] = {
    "easy": [
        # crate_001 — chiếm 88%
        {"weight": 60, "reward": {"type": "crate", "id": "crate_001", "amount": 1}},
        {"weight": 28, "reward": {"type": "crate", "id": "crate_001", "amount": 2}},
        # tier cao — hiếm
        {"weight":  8, "reward": {"type": "crate", "id": "crate_002", "amount": 1}},
        {"weight":  3, "reward": {"type": "crate", "id": "crate_003", "amount": 1}},
        {"weight":  1, "reward": {"type": "crate", "id": "crate_004", "amount": 1}},
    ],
    "medium": [
        # crate_001 — chiếm 72%
        {"weight": 45, "reward": {"type": "crate", "id": "crate_001", "amount": 2}},
        {"weight": 27, "reward": {"type": "crate", "id": "crate_001", "amount": 3}},
        # tier cao — thỉnh thoảng
        {"weight": 18, "reward": {"type": "crate", "id": "crate_002", "amount": 1}},
        {"weight":  7, "reward": {"type": "crate", "id": "crate_003", "amount": 1}},
        {"weight":  2, "reward": {"type": "crate", "id": "crate_004", "amount": 1}},
        # cực hiếm
        {"weight":  1, "reward": {"type": "crate", "id": "crate_005", "amount": 1}},
    ],
    "hard": [
        # crate_001 ×3 là cơ sở
        {"weight": 35, "reward": {"type": "crate", "id": "crate_001", "amount": 3}},
        # tier cao xuất hiện đáng kể
        {"weight": 30, "reward": {"type": "crate", "id": "crate_002", "amount": 1}},
        {"weight": 20, "reward": {"type": "crate", "id": "crate_003", "amount": 1}},
        {"weight": 10, "reward": {"type": "crate", "id": "crate_004", "amount": 1}},
        # hiếm
        {"weight":  5, "reward": {"type": "crate", "id": "crate_005", "amount": 1}},
    ],
}

# Precomputed weights — tránh rebuild list mỗi lần roll
_POOL_WEIGHTS: dict[str, list[int]] = {
    diff: [e["weight"] for e in pool]
    for diff, pool in QUEST_REWARD_POOLS.items()
}


def _roll_quest_reward(difficulty: str) -> dict:
    """Roll 1 reward từ pool tương ứng difficulty. Trả về reward dict (copy)."""
    pool = QUEST_REWARD_POOLS[difficulty]
    return random.choices(pool, weights=_POOL_WEIGHTS[difficulty], k=1)[0]["reward"].copy()


# ════════════════════════════════════════════════════════════════════════════
# PROFILE HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _blank_progress() -> dict:
    return {k: 0 for k in ALL_PROGRESS_KEYS}


def _ensure_profile(uid_str: str) -> dict:
    """Lấy profile từ MongoDB, tạo mới nếu chưa có."""
    col     = _col()
    profile = _with_retry(col.find_one, {"_id": uid_str})

    if not profile:
        profile = {"_id": uid_str, "last_reset": 0, "quests": [], **_blank_progress()}
        try:
            _with_retry(col.insert_one, profile.copy())
        except pymongo.errors.DuplicateKeyError:
            profile = _with_retry(col.find_one, {"_id": uid_str})
    else:
        # Đảm bảo các progress key tồn tại khi thêm quest type mới
        missing = {k: 0 for k in ALL_PROGRESS_KEYS if k not in profile}
        if missing:
            _with_retry(col.update_one, {"_id": uid_str}, {"$set": missing})
            profile.update(missing)

    return profile


# ════════════════════════════════════════════════════════════════════════════
# RESET
# ════════════════════════════════════════════════════════════════════════════

def should_reset_quest(uid: int | str) -> bool:
    uid_str = str(uid)
    col     = _col()
    doc     = _with_retry(col.find_one, {"_id": uid_str}, {"last_reset": 1})
    if not doc:
        return True
    return (time.time() - doc.get("last_reset", 0)) >= 86400


def reset_quest(uid: int | str) -> list[str]:
    """
    Reset quest, gán 1–2 nhiệm vụ ngẫu nhiên.
    Reward được roll ngay tại đây và lưu vào từng quest document.
    Trả về list quest_type đã chọn.
    """
    uid_str      = str(uid)
    count        = random.randint(1, 2)
    chosen_types = random.sample(list(QUEST_TYPES.keys()), k=count)
    new_quests   = [
        {
            "type":      t,
            "completed": False,
            "claimed":   False,
            "reward":    _roll_quest_reward(QUEST_TYPES[t]["difficulty"]),
        }
        for t in chosen_types
    ]

    _with_retry(
        _col().update_one,
        {"_id": uid_str},
        {"$set": {"last_reset": time.time(), "quests": new_quests, **_blank_progress()}},
        upsert=True,
    )
    return chosen_types


# ════════════════════════════════════════════════════════════════════════════
# PROGRESS
# ════════════════════════════════════════════════════════════════════════════

def add_quest_progress(uid: int | str, progress_key: str, amount: int = 1) -> list[str]:
    """
    Cộng progress. Tự đánh dấu completed nếu đạt target.
    Trả về list quest_type vừa hoàn thành (dùng để gửi thông báo).
    """
    uid_str = str(uid)
    col     = _col()

    profile = _with_retry(col.find_one, {"_id": uid_str})
    if not profile:
        return []

    new_val       = profile.get(progress_key, 0) + amount
    quests        = profile.get("quests", [])
    completed_now = []

    for q in quests:
        if q.get("claimed") or q.get("completed"):
            continue
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata = QUEST_TYPES[qt]
        if qdata["progress_key"] != progress_key:
            continue
        if new_val >= qdata["target"]:
            q["completed"] = True
            completed_now.append(qt)

    _with_retry(
        col.update_one,
        {"_id": uid_str},
        {"$set": {progress_key: new_val, "quests": quests}},
        upsert=True,
    )
    return completed_now


# ════════════════════════════════════════════════════════════════════════════
# QUERY
# ════════════════════════════════════════════════════════════════════════════

def get_current_quests(uid: int | str) -> list[dict]:
    """Trả về list thông tin quest hiện tại (đầy đủ), bao gồm reward đã roll."""
    uid_str = str(uid)
    profile = _ensure_profile(uid_str)

    result = []
    for q in profile.get("quests", []):
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue
        qdata    = QUEST_TYPES[qt]
        pkey     = qdata["progress_key"]
        progress = profile.get(pkey, 0)
        result.append({
            "type":        qt,
            "name":        qdata["name"],
            "description": qdata["description"].format(target=qdata["target"]),
            "difficulty":  qdata["difficulty"],
            "target":      qdata["target"],
            "progress":    min(progress, qdata["target"]),
            "completed":   q.get("completed", False),
            "claimed":     q.get("claimed",   False),
            "reward":      q.get("reward"),   # đã roll tại reset, không thay đổi
        })
    return result


def get_current_quest(uid: int | str) -> dict | None:
    """Backward-compat: trả về quest đầu tiên."""
    quests = get_current_quests(uid)
    return quests[0] if quests else None


# ════════════════════════════════════════════════════════════════════════════
# CLAIM REWARD
# ════════════════════════════════════════════════════════════════════════════

def claim_quest_reward(uid: int | str) -> tuple[bool, str, list[dict]]:
    """
    Claim TẤT CẢ quest completed & chưa claimed.
    Reward đã được roll sẵn tại reset — chỉ đọc và trả về.

    Returns:
        (success, message, rewards)
        rewards = list of reward dict per quest:
            {"type": "crate", "id": "crate_XXX", "amount": int}
            {"type": "cash",  "amount": int}
        Khi success = False: rewards = []

    Caller chịu trách nhiệm phân phối reward:
        for r in rewards:
            if r["type"] == "crate":
                add_item(uid, r["id"], r["amount"])
            elif r["type"] == "cash":
                update_balance_safe(uid, r["amount"])
    """
    uid_str = str(uid)
    col     = _col()

    profile = _with_retry(col.find_one, {"_id": uid_str})
    if not profile:
        return False, "Không tìm thấy dữ liệu quest.", []

    rewards = []
    names   = []
    quests  = profile.get("quests", [])

    for q in quests:
        if not (q.get("completed") and not q.get("claimed")):
            continue
        qt = q.get("type")
        if qt not in QUEST_TYPES:
            continue

        reward = q.get("reward")
        if reward:
            rewards.append(reward)
        names.append(QUEST_TYPES[qt]["name"])
        q["claimed"] = True

    if not names:
        return False, "Không có quest hoàn thành nào để nhận.", []

    _with_retry(col.update_one, {"_id": uid_str}, {"$set": {"quests": quests}})
    joined = "**, **".join(names)
    return True, f"Nhận reward từ **{joined}**!", rewards


# ════════════════════════════════════════════════════════════════════════════
# AUTO-NOTIFY — gọi từ bên ngoài sau add_quest_progress
# ════════════════════════════════════════════════════════════════════════════

async def notify_quest_complete(
    channel: discord.abc.Messageable,
    uid: int | str,
    completed_types: list[str],
) -> None:
    """
    Tự động claim reward và gửi thông báo thẳng vào channel hiện tại.

    Gọi ngay sau add_quest_progress ở bất kỳ Cog nào:
        completed = add_quest_progress(ctx.author.id, "crates_opened")
        if completed:
            await notify_quest_complete(ctx.channel, ctx.author.id, completed)

    - Nếu không có quest nào completed: return ngay.
    """
    if not completed_types:
        return

    # Auto-claim tất cả quest completed & chưa claimed
    ok, _, rewards = claim_quest_reward(uid)
    if not ok:
        return

    # Phân phối reward
    lines: list[str] = []
    for r in rewards:
        if r["type"] == "crate":
            # add_item(uid, r["id"], r["amount"])   ← bỏ comment khi đã import
            lines.append(_format_reward(r))
        elif r["type"] == "cash":
            update_balance_safe(uid, r["amount"])
            lines.append(_format_reward(r))

    # Build embed
    quest_names  = [QUEST_TYPES[t]["name"] for t in completed_types if t in QUEST_TYPES]
    names_str    = ", ".join(f"**{n}**" for n in quest_names)
    reward_block = "\n".join(f"• {l}" for l in lines) if lines else ""

    embed = discord.Embed(
        title=f"{TICK} | Hoàn Thành Nhiệm Vụ!",
        description=f"<@{uid}> vừa hoàn thành {names_str}!\n\n{reward_block}",
        color=0x4CAF50,
    )
    embed.set_footer(text="dtn quest để xem chi tiết nhiệm vụ")

    await channel.send(embed=embed)


# ════════════════════════════════════════════════════════════════════════════
# DISCORD COG — DISPLAY HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _progress_bar(progress: int, target: int, length: int = 10) -> str:
    filled = int((progress / target) * length) if target > 0 else 0
    return "█" * min(filled, length) + "░" * (length - min(filled, length))


def _format_reward(reward: dict | None) -> str:
    """Trả về chuỗi mô tả reward để hiển thị trong embed."""
    if not reward:
        return "Không có"
    if reward["type"] == "crate":
        meta   = CRATE_META.get(reward["id"], {"name": reward["id"], "emoji": "📦"})
        amount = reward["amount"]
        return f"{meta['emoji']} **{amount}×** {meta['name']}"
    if reward["type"] == "cash":
        return f"**{reward['amount']:,}** {COIN_EMOJI}"
    return "Không rõ"



# ════════════════════════════════════════════════════════════════════════════
# DISCORD COG
# ════════════════════════════════════════════════════════════════════════════

class RPGQuest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── dtn quest ────────────────────────────────────────────────────────────
    @commands.group(name="quest", invoke_without_command=True)
    async def quest(self, ctx):
        """Xem nhiệm vụ hàng ngày (1–2 quest)."""
        uid = ctx.author.id

        if should_reset_quest(uid):
            reset_quest(uid)

        quests = get_current_quests(uid)
        if not quests:
            reset_quest(uid)
            quests = get_current_quests(uid)
        if not quests:
            return await ctx.send(f"{ERR} | Không thể tải quest. Thử lại sau.")

        all_claimed   = all(q["claimed"] for q in quests)
        any_completed = any(q["completed"] and not q["claimed"] for q in quests)

        embed = discord.Embed(
            title=f"{INFO} | Daily Quest — {ctx.author.display_name}",
            description=f"Hôm nay có **{len(quests)}** nhiệm vụ. Reset mỗi 24h.",
            color=(
                0x4CAF50 if all_claimed
                else 0xFFC107 if any_completed
                else 0x5865F2
            ),
        )

        for q in quests:
            pct = int(q["progress"] / q["target"] * 100) if q["target"] > 0 else 0
            bar = _progress_bar(q["progress"], q["target"])

            if q["claimed"]:
                status = f"{CLAIMED} Đã nhận thưởng"
                icon   = "🏆"
            elif q["completed"]:
                status = f"{TICK} Hoàn thành! Phần thưởng đã gửi qua DM"
                icon   = f"{TICK}"
            else:
                status = f"🔄 {q['progress']}/{q['target']} ({pct}%)"
                icon   = "🔲"

            embed.add_field(
                name=f"{icon} {q['name']}",
                value=(
                    f"{q['description']}\n"
                    f"`{bar}` {pct}%\n"
                    f"<:2245:1493575277605949480> | {_format_reward(q['reward'])}  •  {status}"
                ),
                inline=False,
            )

        embed.set_footer(text="Số dư: {:,} {}".format(get_balance(uid), COIN_EMOJI))

        await ctx.send(embed=embed)



# ════════════════════════════════════════════════════════════════════════════
# SETUP
# ════════════════════════════════════════════════════════════════════════════

async def setup(bot):
    await bot.add_cog(RPGQuest(bot))
