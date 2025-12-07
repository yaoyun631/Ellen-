import os
import json
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

import pandas as pd
from firebase_admin import firestore           # 為了用 SERVER_TIMESTAMP
from firebase_client import db                 # 直接共用你專案的 db 連線


# ===========================================
# Excel → list[dict]
# ===========================================
def excel_to_records(excel_path: str):
    ext = os.path.splitext(excel_path)[1].lower()
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(excel_path, dtype=str)
    elif ext == ".csv":
        df = pd.read_csv(excel_path, dtype=str)
    else:
        raise ValueError("只支援 .xlsx / .xls / .csv 檔案")

    # 全部轉成字串，避免 NaN
    df = df.fillna("")
    records = df.to_dict(orient="records")
    return records


# ===========================================
# 把 records 分 Chunk 存到 Firestore
# ===========================================
def save_records_to_firestore(records, collection_name: str, chunk_size: int = 300):
    # 這裡不再呼叫 init_firestore()，直接用 firebase_client 給的 db
    col_ref = db.collection(collection_name)

    # 先清空舊資料（如果你想保留舊資料，可以把這段註解掉）
    print(f"🧹 先清空集合：{collection_name}")
    for doc in col_ref.stream():
        doc.reference.delete()

    total = len(records)
    if total == 0:
        print("⚠ Excel 沒有任何資料，停止")
        return 0, 0

    print(f"📊 總筆數：{total}，開始分 chunk 寫入 Firestore ...")

    chunk_index = 0
    doc_count = 0

    for start in range(0, total, chunk_size):
        chunk_index += 1
        end = min(start + chunk_size, total)
        chunk_data = records[start:end]

        doc_id = f"chunk_{chunk_index:04d}"
        col_ref.document(doc_id).set({
            "chunk_index": chunk_index,
            "row_count": len(chunk_data),
            "rows": chunk_data,
            "created_at": firestore.SERVER_TIMESTAMP,
        })

        doc_count += 1
        print(f"✔ 已寫入：{doc_id}（{len(chunk_data)} 筆）")

    # 寫一份 meta 資料，方便之後查版本
    meta_ref = db.collection("aiwu_json_meta").document(collection_name)
    meta_ref.set({
        "collection_name": collection_name,
        "row_count": total,
        "chunk_count": doc_count,
        "created_at": firestore.SERVER_TIMESTAMP,
    })

    print("🎉 完成 Firestore 寫入")
    print(f"📁 集合：{collection_name}")
    print(f"📄 chunk 數量：{doc_count}")
    print(f"🧮 總筆數：{total}")

    return doc_count, total


# ===========================================
# GUI：選 Excel → 執行流程
# ===========================================
def main():
    root = tk.Tk()
    root.withdraw()  # 不顯示主視窗

    messagebox.showinfo("Excel → Firestore",
                        "請選擇要上傳的 Excel 檔案（.xlsx / .xls / .csv）")

    file_path = filedialog.askopenfilename(
        title="選擇 Excel 檔案",
        filetypes=[
            ("Excel 檔案", "*.xlsx *.xls"),
            ("CSV 檔案", "*.csv"),
            ("所有檔案", "*.*"),
        ]
    )

    if not file_path:
        messagebox.showwarning("取消", "未選擇任何檔案，程式結束。")
        return

    try:
        print(f"📂 選擇檔案：{file_path}")
        records = excel_to_records(file_path)
        print(f"✅ 讀取完成，共 {len(records)} 筆資料")

        # 集合名稱：用檔名（去掉副檔名）
        base_name = os.path.basename(file_path)
        name_no_ext = os.path.splitext(base_name)[0]

        # 如果怕撞名，也可以加日期：
        # today_str = datetime.now().strftime("%Y%m%d")
        # collection_name = f"{name_no_ext}_{today_str}"
        collection_name = name_no_ext

        chunks, total = save_records_to_firestore(records, collection_name, chunk_size=300)

        messagebox.showinfo(
            "完成",
            f"已上傳至 Firestore！\n\n"
            f"集合名稱：{collection_name}\n"
            f"總筆數：{total}\n"
            f"chunk 數量：{chunks}"
        )

    except Exception as e:
        print("❌ 發生錯誤：", e)
        messagebox.showerror("錯誤", f"上傳過程發生錯誤：\n{e}")


if __name__ == "__main__":
    main()
