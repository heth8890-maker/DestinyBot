import pymongo
import os
import json

# --- KẾT NỐI MONGODB ---
MONGO_URL = os.getenv("MONGO_URI")
if not MONGO_URL:
    # Dùng để test local, nhớ KHÔNG commit link thật lên GitHub
    MONGO_URL = "Link_Của_Bạn_Ở_Đây"

client = pymongo.MongoClient(MONGO_URL)
db = client["rpg_bot_db"]
economy_col = db["economy"]

# File JSON cũ để tự động chuyển dữ liệu
JSON_FILE = 'economy.json'


# --- HÀM LOAD DATA ---
def load_data_mongo(user_id=None):
    if user_id is None:
        print("⚠️ Cảnh báo: Gọi load_data mà không có user_id!")
        return {}

    uid_str = str(user_id)

    # 1. Mongo trước
    user = economy_col.find_one({"_id": uid_str})
    if user:
        return user

    # 2. JSON → Mongo (migrate)
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)

            if uid_str in old_data:
                data = dict(old_data[uid_str])  # copy tránh sửa file gốc
                data["_id"] = uid_str

                economy_col.replace_one(
                    {"_id": uid_str},
                    data,
                    upsert=True
                )

                print(f"✅ Đã migrate user {uid_str} sang MongoDB")
                return data

        except Exception as e:
            print(f"❌ Lỗi migrate: {e}")

    # 3. User mới
    new_user = {
        "_id": uid_str,
        "cash": 0,
        "exp": 0,
        "equipped": [None, None, None]
    }

    try:
        economy_col.insert_one(new_user)
    except Exception:
        # tránh crash nếu bị race condition
        existing = economy_col.find_one({"_id": uid_str})
        if existing:
            return existing

    return new_user


# --- HÀM SAVE DATA ---
def save_data_mongo(user_id, data):
    if user_id is None:
        return

    uid_str = str(user_id)

    temp_data = dict(data)
    temp_data.pop("_id", None)

    try:
        economy_col.update_one(
            {"_id": uid_str},
            {"$set": temp_data},
            upsert=True
        )
    except Exception as e:
        print(f"❌ Lỗi save Mongo: {e}")


# --- HÀM TIỆN ÍCH (AN TOÀN HƠN CHO TIỀN/EXP) ---
def add_cash(user_id, amount):
    economy_col.update_one(
        {"_id": str(user_id)},
        {"$inc": {"cash": amount}},
        upsert=True
    )

def add_exp(user_id, amount):
    economy_col.update_one(
        {"_id": str(user_id)},
        {"$inc": {"exp": amount}},
        upsert=True
    )
