# migrate_to_storage.py
import os
from firebase_client import bucket  # 用你已經設好的 firebase_client

# 想要上傳的本機資料夾
LOCAL_FOLDERS = [
    "data",
    "static"
]


def upload_folder_to_storage(local_root: str, storage_prefix: str):
    """
    把 local_root 底下所有檔案，上傳到 Storage：
    - local_root = "data"
    - storage_prefix = "data"
    => data/xxx/yyy.png 會變成 Storage 裡的 data/xxx/yyy.png
    """
    if not os.path.exists(local_root):
        print(f"⚠ 本機資料夾不存在：{local_root}，略過")
        return

    for root, dirs, files in os.walk(local_root):
        for filename in files:
            local_path = os.path.join(root, filename)

            # 算出相對路徑，例如 data/foo/bar.xlsx
            rel_path = os.path.relpath(local_path, local_root)
            rel_path = rel_path.replace("\\", "/")

            # Storage 裡的路徑：storage_prefix/相對路徑
            blob_path = f"{storage_prefix}/{rel_path}"

            print(f"⬆ 上傳 {local_path} → {blob_path} ...")

            blob = bucket.blob(blob_path)

            # 簡單用二進位讀取上傳
            with open(local_path, "rb") as f:
                blob.upload_from_file(f)

            # 如果希望都可以公開讀取，就打開這行
            # （或之後在 Storage 的規則 / console 上另外設定）
            # blob.make_public()

            print(f"✅ 完成：gs://{bucket.name}/{blob_path}")


def main():
    for folder in LOCAL_FOLDERS:
        # Storage 的 prefix 我就直接用同名
        upload_folder_to_storage(folder, folder)


if __name__ == "__main__":
    main()
