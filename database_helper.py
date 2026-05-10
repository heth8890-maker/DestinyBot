import copy
import pymongo
import pymongo.errors
import os
import json
import logging
import time
from typing import Optional

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("db_helper")

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────
RPG_JSON_FILE      = "rpg_data.json"       # {uid: {inv, weapons, equipped, ...}}
ECONOMY_JSON_FILE  = "economy.json"        # flat: {uid: cash, uid_daily_date: ..., uid_daily_streak: ...}
DB_NAME            = "rpg_bot_db"
DEFAULT_USER       = {"cash": 0, "exp": 0, "equipped": [None, None, None], "weapons": [], "weapon_instances": []}
MAX_RETRIES        = 2
RETRY_DELAY        = 1          # giây
MONGO_TIMEOUT_MS   = 10_000     # 10 giây cho mỗi thao tác


def _default_user(uid_str: str) -> dict:
    return {"_id": uid_str, **copy.deepcopy(DEFAULT_USER)}


# ─────────────────────────────────────────────
#  KẾT NỐI MONGODB  (singleton, lazy-init)
# ─────────────────────────────────────────────
_client: Optional[pymongo.MongoClient] = None


def _get_client() -> pymongo.MongoClient:
    """Trả về MongoClient duy nhất; tự kết nối lại nếu cần."""
    global _client
    if _client is None:
        mongo_url = os.getenv("MONGO_URI")
        if not mongo_url:
            raise EnvironmentError("❌ Biến môi trường MONGO_URI chưa được đặt!")
        _client = pymongo.MongoClient(
            mongo_url,
            serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
            connectTimeoutMS=MONGO_TIMEOUT_MS,
            socketTimeoutMS=MONGO_TIMEOUT_MS,
            retryWrites=True,
            retryReads=True,
        )
        _client.admin.command("ping")
        log.info("✅ Kết nối MongoDB thành công.")
    return _client


def _get_collections():
    """Trả về (economy_col, shop_col) từ client hiện tại."""
    db = _get_client()[DB_NAME]
    return db["economy"], db["shop_data"]


# ─────────────────────────────────────────────
#  RETRY DECORATOR
# ─────────────────────────────────────────────
def _with_retry(fn, *args, **kwargs):
    """Thực thi fn, tự retry tối đa MAX_RETRIES lần nếu gặp lỗi mạng/timeout."""
    global _client
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (pymongo.errors.AutoReconnect, pymongo.errors.ServerSelectionTimeoutError) as e:
            last_err = e
            log.warning(f"⚠️  Connection error (lần {attempt}/{MAX_RETRIES}): {type(e).__name__}: {e}")
            _client = None
            time.sleep(RETRY_DELAY * attempt)
        except pymongo.errors.NetworkTimeout as e:
            last_err = e
            log.warning(f"⚠️  NetworkTimeout (lần {attempt}/{MAX_RETRIES}): {e}")
            time.sleep(RETRY_DELAY * attempt)
        except pymongo.errors.PyMongoError as e:
            log.error(f"❌ Lỗi MongoDB không thể retry: {type(e).__name__}: {e}")
            raise
    log.error(f"❌ Thất bại sau {MAX_RETRIES} lần thử: {type(last_err).__name__}: {last_err}")
    raise last_err


# ─────────────────────────────────────────────
#  MIGRATE TỪ JSON CŨ  ← ĐÃ SỬA
# ─────────────────────────────────────────────
def _migrate_from_json(uid_str: str, economy_col) -> Optional[dict]:
    """
    Merge dữ liệu từ 2 file JSON cũ vào 1 document MongoDB:

    • economy.json  — flat key: uid → cash, uid_daily_date, uid_daily_streak
    • rpg_data.json — nested:   {uid: {inv, weapons, equipped, upgraded_weapons, ...}}

    Nếu cả 2 file đều không có uid → trả về None (sẽ tạo user mới).
    """
    # ── Đọc RPG data (inv, weapons, ...) ──
    rpg_user = {}
    if os.path.exists(RPG_JSON_FILE):
        try:
            with open(RPG_JSON_FILE, "r", encoding="utf-8") as f:
                rpg_db = json.load(f)
            # ✅ Cấu trúc đúng: {uid: {inv, weapons, ...}} — không có key "users"
            rpg_user = rpg_db.get(uid_str, {})
        except Exception as e:
            log.error(f"❌ Đọc {RPG_JSON_FILE} lỗi cho {uid_str}: {e}")

    # ── Đọc Economy data (cash, daily) ──
    cash        = 0
    daily_date  = None
    daily_streak = 0
    if os.path.exists(ECONOMY_JSON_FILE):
        try:
            with open(ECONOMY_JSON_FILE, "r", encoding="utf-8") as f:
                eco_db = json.load(f)
            # ✅ Cấu trúc đúng: flat key — uid thẳng là cash
            if uid_str in eco_db:
                cash         = int(eco_db.get(uid_str, 0) or 0)
                daily_date   = eco_db.get(f"{uid_str}_daily_date")
                daily_streak = int(eco_db.get(f"{uid_str}_daily_streak", 0) or 0)
        except Exception as e:
            log.error(f"❌ Đọc {ECONOMY_JSON_FILE} lỗi cho {uid_str}: {e}")

    # Không có dữ liệu gì trong cả 2 file → không migrate
    if not rpg_user and cash == 0 and daily_date is None:
        return None

    # ── Merge thành 1 document ──
    user = {
        "_id":              uid_str,
        "cash":             cash,
        "daily_date":       daily_date,
        "daily_streak":     daily_streak,
        "exp":              rpg_user.get("exp", 0),
        "inv":              rpg_user.get("inv", {}),
        "weapons":          rpg_user.get("weapons", []),
        "equipped":         rpg_user.get("equipped", [None, None, None]),
        "weapon_instances": [],
        "upgraded_weapons": rpg_user.get("upgraded_weapons", []),
        "cooldown":         rpg_user.get("cooldown", 0),
        "hunt_cd":          rpg_user.get("hunt_cd", 0),
        "crate_cd":         rpg_user.get("crate_cd", 0),
        "passives":         rpg_user.get("passives", {}),
    }

    try:
        _with_retry(economy_col.insert_one, user.copy())
        log.info(f"✅ Migrate user {uid_str} từ JSON → MongoDB (cash={cash})")
    except pymongo.errors.DuplicateKeyError:
        log.info(f"ℹ️  User {uid_str} đã tồn tại, bỏ qua migrate.")
        user = _with_retry(economy_col.find_one, {"_id": uid_str})

    return user


# ─────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────
def load_core_data(user_id) -> dict:
    """
    Tải dữ liệu user + global weapons.
    Tự động migrate từ JSON hoặc tạo mới nếu chưa có.
    Không bao giờ raise — trả về dữ liệu mặc định nếu mọi thứ thất bại.
    """
    uid_str = str(user_id)
    try:
        economy_col, _ = _get_collections()

        user = _with_retry(economy_col.find_one, {"_id": uid_str})

        if not user:
            user = _migrate_from_json(uid_str, economy_col)

        if not user:
            user = _default_user(uid_str)
            try:
                _with_retry(economy_col.insert_one, user.copy())
                log.info(f"🆕 Đã tạo user mới: {uid_str}")
            except pymongo.errors.DuplicateKeyError:
                user = _with_retry(economy_col.find_one, {"_id": uid_str}) or user

        upgraded_weapons = user.get("upgraded_weapons", [])

        return {"user": user, "upgraded_weapons": upgraded_weapons}

    except Exception as e:
        log.error(f"❌ load_core_data thất bại cho {uid_str}: {e}")
        return {
            "user": _default_user(uid_str),
            "upgraded_weapons": [],
        }


def save_core_data(user_id, user_data: dict) -> bool:
    """
    Lưu dữ liệu user. Nhận trực tiếp user document (không phải wrapper).
    Trả về True nếu thành công, False nếu thất bại.
    """
    uid_str = str(user_id)
    try:
        economy_col, _ = _get_collections()

        payload = {k: v for k, v in user_data.items() if k != "_id"}
        _with_retry(
            economy_col.update_one,
            {"_id": uid_str},
            {"$set": payload},
            upsert=True,
        )

        return True

    except Exception as e:
        log.error(f"❌ save_core_data thất bại cho {uid_str}: {type(e).__name__}: {e}")
        return False


_SHOP_DOC_ID = "weapon_shop"


def load_shop_data() -> dict:
    """Tải shop từ collection shop_data. Trả về {} nếu chưa có."""
    try:
        _, shop_col = _get_collections()
        doc = _with_retry(shop_col.find_one, {"_id": _SHOP_DOC_ID})
        if doc:
            doc.pop("_id", None)
        return doc or {}
    except Exception as e:
        log.error(f"❌ load_shop_data thất bại: {e}")
        return {}


def save_shop_data(data: dict) -> None:
    """Lưu shop lên collection shop_data."""
    try:
        _, shop_col = _get_collections()
        payload = {k: v for k, v in data.items() if k != "_id"}
        _with_retry(
            shop_col.update_one,
            {"_id": _SHOP_DOC_ID},
            {"$set": payload},
            upsert=True,
        )
    except Exception as e:
        log.error(f"❌ save_shop_data thất bại: {e}")


def close_connection():
    """Đóng kết nối MongoDB sạch sẽ (gọi khi bot shutdown)."""
    global _client
    if _client:
        _client.close()
        _client = None
        log.info("🔌 Đã đóng kết nối MongoDB.")


if __name__ == "__main__":
    try:
        c = _get_client()
        print("✅ Kết nối OK:", c.server_info()["version"])
    except Exception as e:
        print("❌ Lỗi:", e)
