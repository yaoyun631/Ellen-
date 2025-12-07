# firebase_client.py
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, storage


BUCKET_NAME = "ellenmyhomie-5c2ed.firebasestorage.app"  # 你的 bucket 名稱


# ============================================================
# Firebase 初始化 — Firestore + Storage
# ============================================================
def init_firebase():
    print(">>> 初始化 Firebase...")

    # --------------------------------------------------------
    # 如果已經有 Firebase App：直接用，但 bucket 要手動指定名稱
    # --------------------------------------------------------
    if firebase_admin._apps:
        print(">>> Firebase 已存在，不重新初始化")
        db = firestore.client()
        # ⭐ 這裡改成「指定 bucket 名稱」，不再用空的 storage.bucket()
        bucket = storage.bucket(BUCKET_NAME)
        return db, bucket

    # --------------------------------------------------------
    # Step 1：讀取憑證（環境變數或本機 serviceAccountKey.json）
    # --------------------------------------------------------
    cred_obj = None

    # ① 伺服器 / VPS / Render：使用 FIREBASE_CREDENTIALS
    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if cred_json:
        try:
            cred_obj = credentials.Certificate(json.loads(cred_json))
            print("✅ 使用 FIREBASE_CREDENTIALS 初始化")
        except Exception as e:
            print("⚠️ FIREBASE_CREDENTIALS 解析失敗：", e)

    # ② 本機：讀取 serviceAccountKey.json
    if cred_obj is None and os.path.exists("serviceAccountKey.json"):
        try:
            cred_obj = credentials.Certificate("serviceAccountKey.json")
            print("✅ 使用 serviceAccountKey.json 初始化")
        except Exception as e:
            print("⚠️ 無法讀取 serviceAccountKey.json：", e)

    # 若仍沒有憑證 → 錯誤
    if cred_obj is None:
        raise RuntimeError("❌ 找不到 Firebase 憑證（缺少 FIREBASE_CREDENTIALS 或 serviceAccountKey.json）")

    # --------------------------------------------------------
    # Step 2：初始化 Firebase App，並指定 Storage Bucket
    # --------------------------------------------------------
    try:
        firebase_admin.initialize_app(
            cred_obj,
            {
                "storageBucket": BUCKET_NAME
            }
        )
        print("✅ Firebase 初始化成功（含 Storage bucket）")
    except Exception as e:
        print("❌ Firebase 初始化失敗：", e)
        raise e

    # --------------------------------------------------------
    # Step 3：建立 Firestore & Storage 操作物件
    # --------------------------------------------------------
    db = firestore.client()
    # 這裡一樣保險指定名稱
    bucket = storage.bucket(BUCKET_NAME)

    print(">>> Firestore + Storage 就緒！")
    return db, bucket


# ============================================================
# 建立全域可共用的 db, bucket
# ============================================================
db, bucket = init_firebase()
