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
JSON_FILE          = "rpg_data.json"
DB_NAME            = "rpg_bot_db"
GLOBAL_WEAPONS_ID  = "upgraded_weapons"
DEFAULT_USER       = {"cash": 0, "exp": 0, "equipped": [None, None, None]}
MAX_RETRIES        = 3
RETRY_DELAY        = 2          # giây
MONGO_TIMEOUT_MS   = 5_000      # 5 giây cho mỗi thao tác

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
            retryWrites=True,       # Mongo tự retry write khi mạng chập chờn
            retryReads=True,
        )
        # Kiểm tra kết nối ngay lúc khởi tạo
        _client.admin.command("ping")
        log.info("✅ Kết nối MongoDB thành công.")
    return _client


def _get_collections():
    """Trả về (economy_col, global_col) từ client hiện tại."""
    db = _get_client()[DB_NAME]
    return db["economy"], db["global_metadata"]


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
        except pymongo.errors.AutoReconnect as e:
            last_err = e
            log.warning(f"⚠️  AutoReconnect (lần {attempt}/{MAX_RETRIES}): {e}")
            _client = None                  # Bắt buộc tạo lại client
            time.sleep(RETRY_DELAY * attempt)
        except pymongo.errors.NetworkTimeout as e:
            last_err = e
            log.warning(f"⚠️  NetworkTimeout (lần {attempt}/{MAX_RETRIES}): {e}")
            time.sleep(RETRY_DELAY * attempt)
        except pymongo.errors.PyMongoError as e:
            # Lỗi nghiêm trọng khác → không retry
            log.error(f"❌ Lỗi MongoDB không thể retry: {e}")
            raise
    log.error(f"❌ Thất bại sau {MAX_RETRIES} lần thử: {last_err}")
    raise last_err


# ─────────────────────────────────────────────
#  MIGRATE TỪ JSON CŨ
# ─────────────────────────────────────────────
def _migrate_from_json(uid_str: str, economy_col) -> Optional[dict]:
    """
    Tìm user trong file JSON cũ, migrate lên Mongo một lần duy nhất.
    Dùng insert với kiểm tra duplicate để tránh ghi đè nếu chạy song song.
    """
    if not os.path.exists(JSON_FILE):
        return None
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            old_db = json.load(f)
        old_user = old_db.get("users", {}).get(uid_str)
        if not old_user:
            return None

        old_user["_id"] = uid_str
        try:
            _with_retry(economy_col.insert_one, old_user)
            log.info(f"✅ Đã migrate user {uid_str} từ JSON lên Mongo.")
        except pymongo.errors.DuplicateKeyError:
            # Có thể bị race-condition: 2 request cùng migrate → bỏ qua
            log.info(f"ℹ️  User {uid_str} đã tồn tại (duplicate key), bỏ qua migrate.")
            old_user = _with_retry(economy_col.find_one, {"_id": uid_str})

        return old_user
    except Exception as e:
        log.error(f"❌ Lỗi khi migrate JSON cho {uid_str}: {e}")
        return None


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
        economy_col, global_col = _get_collections()

        # 1. Tìm user trên Mongo
        user = _with_retry(economy_col.find_one, {"_id": uid_str})

        # 2. Chưa có → thử migrate từ JSON
        if not user:
            user = _migrate_from_json(uid_str, economy_col)

        # 3. Vẫn không có → tạo mới
        if not user:
            user = {"_id": uid_str, **DEFAULT_USER}
            try:
                _with_retry(economy_col.insert_one, user.copy())
                log.info(f"🆕 Đã tạo user mới: {uid_str}")
            except pymongo.errors.DuplicateKeyError:
                # Race-condition: request khác vừa tạo → đọc lại
                user = _with_retry(economy_col.find_one, {"_id": uid_str}) or user

        # 4. Global weapons
        g_data = _with_retry(global_col.find_one, {"_id": GLOBAL_WEAPONS_ID})
        upgraded_weapons = g_data.get("data", {}) if g_data else {}

        return {"user": user, "upgraded_weapons": upgraded_weapons}

    except Exception as e:
        log.error(f"❌ load_core_data thất bại cho {uid_str}: {e}")
        # Trả về dữ liệu mặc định để bot không crash
        return {
            "user": {"_id": uid_str, **DEFAULT_USER},
            "upgraded_weapons": {},
        }


def save_core_data(user_id, user_data: dict, upgraded_weapons: dict) -> bool:
    """
    Lưu dữ liệu user + global weapons.
    Dùng $set nguyên tử cho global weapons để không ghi đè mất đồ người khác.
    Trả về True nếu thành công, False nếu thất bại.
    """
    uid_str = str(user_id)
    try:
        economy_col, global_col = _get_collections()

        # ── Lưu user (chỉ $set các field thay đổi, giữ nguyên _id) ──
        payload = {k: v for k, v in user_data.items() if k != "_id"}
        _with_retry(
            economy_col.update_one,
            {"_id": uid_str},
            {"$set": payload},
            upsert=True,
        )

        # ── Lưu global weapons: $set từng key riêng lẻ ──────────────
        # Tránh đọc-gộp-ghi (race condition) bằng cách dùng dot-notation
        if upgraded_weapons:
            weapon_update = {
                f"data.{wid}": wdata
                for wid, wdata in upgraded_weapons.items()
            }
            _with_retry(
                global_col.update_one,
                {"_id": GLOBAL_WEAPONS_ID},
                {"$set": weapon_update},
                upsert=True,
            )

        return True

    except Exception as e:
        log.error(f"❌ save_core_data thất bại cho {uid_str}: {e}")
        return False


def close_connection():
    """Đóng kết nối MongoDB sạch sẽ (gọi khi bot shutdown)."""
    global _client
    if _client:
        _client.close()
        _client = None
        log.info("🔌 Đã đóng kết nối MongoDB.")
