import pymongo
import os
from dotenv import load_dotenv

load_dotenv()

# Kết nối tới MongoDB Atlas
mongo_url = os.getenv("MONGO_URI")
client = pymongo.MongoClient(mongo_url)
db = client["rpg_bot_db"]

# Định nghĩa các "bảng" dữ liệu
economy_col = db["economy"]      # Thay thế cho economy.json
rpg_col = db["rpg_data"]        # Thay thế cho rpg_data_base.json
