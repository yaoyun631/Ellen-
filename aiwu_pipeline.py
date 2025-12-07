import os
import re
import json
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from firebase_client import db, bucket
from firebase_admin import firestore
from firebase_admin import firestore

from flask import render_template, current_app 
from firebase_client import db, bucket 
from flask import render_template

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ========= 愛屋網址 =========

AIWU_LOGIN_URL = "https://es.houseol.com.tw/login.aspx"
# 這個是你說的「銷售物件列表」頁
AIWU_LIST_URL = "https://es.houseol.com.tw/index.aspx?module=SellHouse&file=Object#"

AIWU_JSON_COLLECTION = "aiwu_json"     # 目前不再使用，只保留名稱避免撞名
AIWU_ROWS_COLLECTION = "aiwu_rows"
SEDM_PAGES_COLLECTION = "sedm_pages"
AIWU_TXT_COLLECTION = "aiwu_txt"       # 專門存原始 TXT / 網址清單



# ========= 基本設定 =========

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# ========= HouseOL 圖片工具 =========

def expand_houseol_images(image_url: str):
    """
    給一張 HouseOL 的主圖 ( ...a.jpg )，自動展開成 a～t，
    並用 HEAD 檢查存在的才保留。
    """
    if not image_url:
        return []

    m = re.search(r"^(?P<prefix>.+)[a-zA-Z]\.jpg$", image_url)
    if not m:
        return [image_url]

    prefix = m.group("prefix")
    candidates = []

    for ch in "abcdefghijklmnopqrst":
        url = f"{prefix}{ch}.jpg"
        try:
            resp = requests.head(url, timeout=2)
            if resp.status_code == 200:
                candidates.append(url)
        except requests.RequestException:
            continue

    if not candidates:
        candidates.append(image_url)

    return candidates
# =========================================================================
# 0. 登入愛屋
# =========================================================================

def aiwu_login(driver):
    """
    自動登入愛屋：
    - 進 login.aspx
    - 填入 HouseID / MemberID / MemberPW
    - 點『登入』按鈕
    """
    house_id = os.environ.get("AIWU_HOUSE_ID", "H229")
    member_id = os.environ.get("AIWU_MEMBER_ID", "sp290")
    member_pw = os.environ.get("AIWU_MEMBER_PW", "0000")

    driver.get(AIWU_LOGIN_URL)
    time.sleep(1.5)

    # 填欄位
    house_input = driver.find_element(By.ID, "HouseID")
    member_input = driver.find_element(By.ID, "MemberID")
    pw_input = driver.find_element(By.ID, "MemberPW")

    house_input.clear()
    house_input.send_keys(house_id)

    member_input.clear()
    member_input.send_keys(member_id)

    pw_input.clear()
    pw_input.send_keys(member_pw)

    # 點登入按鈕（LinkButton1）
    login_btn = driver.find_element(By.ID, "LinkButton1")
    login_btn.click()

    time.sleep(2)
    print(f"🟢 已嘗試登入愛屋（{house_id} / {member_id}）")




# =========================================================================
# 1. 自動登入 + 抓列表 + 存 TXT：crawl_aiwu_and_save_txt
# =========================================================================

def crawl_aiwu_and_save_txt(headless=True, click_interval=1.0, max_rounds=200):
    """
    流程：
    1. 開 Selenium → 登入愛屋
    2. 進銷售物件列表頁 (AIWU_LIST_URL)
    3. 不斷滾動 + 點『查看更多』，同時收集畫面上所有『型錄』連結
    4. 去除重複，存成 data/愛屋YYYYMMDD.txt

    回傳：(網址數量, txt_path)
    """

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=options)

    try:
        # 1️⃣ 先登入
        aiwu_login(driver)

        # 2️⃣ 進銷售物件列表頁
        driver.get(AIWU_LIST_URL)
        time.sleep(1.5)
        print(f"📄 進入列表頁：{AIWU_LIST_URL}")

        catalog_links = set()
        last_count = 0
        same_count_rounds = 0

        for round_idx in range(1, max_rounds + 1):
            # 滾到最底
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            # 每一輪把畫面上所有「型錄」連結抓起來
            a_tags = driver.find_elements(By.TAG_NAME, "a")
            for a in a_tags:
                text = (a.text or "").strip()
                href = a.get_attribute("href") or ""
                if text == "型錄" and "Ecatalog.aspx" in href:
                    # 處理 //、/ 的相對路徑
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = "https://es.houseol.com.tw" + href
                    catalog_links.add(href)

            now_count = len(catalog_links)
            print(f"🔁 第 {round_idx} 輪，目前抓到 {now_count} 筆型錄連結")

            # 如果連續幾輪數量都沒增加，就判定到底了
            if now_count == last_count:
                same_count_rounds += 1
            else:
                same_count_rounds = 0
            last_count = now_count

            # 嘗試找到「查看更多」
            try:
                load_more = driver.find_element(By.CSS_SELECTOR, "a.load_more")
                if load_more.is_displayed():
                    print("👉 點擊『查看更多』")
                    load_more.click()
                    time.sleep(click_interval)
                else:
                    print("⚠️ load_more 按鈕不可見，準備結束")
                    break
            except Exception:
                print("⚠️ 找不到『查看更多』按鈕，準備結束")
                break

            # 如果連續 3 輪都沒有增加，直接停
            if same_count_rounds >= 3:
                print("⚠️ 連續多輪網址數量沒變，視為已到底，停止")
                break

        links = sorted(catalog_links)
        print(f"🟢 共抓到 {len(links)} 筆型錄網址")

        # 3️⃣ 存 TXT 檔
        date_str = datetime.now().strftime("%Y%m%d")
        txt_path = os.path.join(DATA_DIR, f"愛屋{date_str}.txt")

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(links))

        print(f"📄 已儲存網址清單：{txt_path}")
        return len(links), txt_path

    finally:
        driver.quit()


# =========================================================================
# 2. 單頁解析：extract_info_simple
# =========================================================================

def extract_info_simple(url: str) -> dict:
    """用 requests 抓型錄資料（不使用 Selenium），並加上屋齡 / 環境特色 / 地圖連結等資訊"""
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        result = {"網址": url}

        # ===== 房屋標題與區域 =====
        title_el = soup.select_one(".title h3")
        area_el = soup.select_one("#VarArea .caption")

        result["房屋標題"] = title_el.get_text(strip=True) if title_el else ""
        result["區域"] = area_el.get_text(strip=True) if area_el else ""

        # ===== 表格欄位（統一去掉全形空格/不換行空白）=====
        for row in soup.select(".t-tr"):
            ths = row.select(".t-th")
            tds = row.select(".t-td")
            for th, td in zip(ths, tds):
                label = (
                    th.get_text(strip=True)
                    .replace("：", "")
                    .replace("\xa0", "")
                    .replace("\u3000", "")
                    .strip()
                )
                # 有些 td 裡面會包 <p>，優先取 p 文字
                p = td.select_one("p")
                value = (p.get_text(strip=True) if p else td.get_text(strip=True))
                value = value.replace("\xa0", "").replace("\u3000", "").strip()

                if label and (label not in result):
                    result[label] = value

        # ===== 屋齡（從「屋齡」那一塊區域找數字） =====
        age_div = None
        for div in soup.select("div.title"):
            clean_title = (
                div.get_text(strip=True)
                .replace(" ", "")
                .replace("\u3000", "")
            )
            if "屋齡" in clean_title:
                age_div = div
                break

        age_text = ""
        if age_div:
            next_sib = age_div.find_next_sibling()
            if next_sib:
                # 找 p 裡面的「xx年」
                p_tags = next_sib.find_all("p")
                for p in p_tags:
                    txt = p.get_text(strip=True).replace("\u3000", "")
                    m = re.search(r"(\d+\.?\d*)\s*年", txt)
                    if m:
                        age_text = m.group(1) + "年"
                        break
                # 如果上面沒抓到，就整塊文字塞回去
                if not age_text:
                    age_text = next_sib.get_text(strip=True).replace("\u3000", "")
            else:
                # 沒有兄弟節點，就用父層的文字清理後當屋齡
                age_text = (
                    age_div.parent.get_text(strip=True)
                    .replace("屋齡", "")
                    .replace("\u3000", "")
                    .strip()
                )

        if age_text:
            result["屋齡"] = age_text

        # ===== 環境特色（#GoodSpan 裡的重點） =====
        features = []
        good_span = soup.select_one("#GoodSpan")
        if good_span:
            for pdiv in good_span.select("div.points strong"):
                text = pdiv.get_text(strip=True)
                if text:
                    features.append(text)

        if features:
            # 用換行分隔，之後你要 split 也方便
            result["環境特色"] = "\n".join(features)

        # ===== 地圖連結（a#otherfunc1 的 fancybox 連結） =====
        map_btn = soup.select_one("a#otherfunc1")
        map_url = ""
        if map_btn and map_btn.has_attr("onclick"):
            onclick_text = map_btn["onclick"]
            m = re.search(r"fancybox\('([^']+)'", onclick_text)
            if m:
                map_url = m.group(1)

        if map_url:
            result["地圖連結"] = map_url

        # ===== 物件編號（從網址 query string 抓 No=）=====
        m = re.search(r"[?&]No=([A-Z0-9]+)", url, flags=re.I)
        if m:
            result.setdefault("物件編號", m.group(1))

        return result

    except Exception as e:
        print(f"⚠ extract_info_simple 錯誤：{url} → {e}")
        return {"網址": url, "錯誤": str(e)}


# =========================================================================
# 3. TXT → chunk Collection 存 Firestore：generate_aiwu_json_from_txt
# =========================================================================

def generate_aiwu_json_from_txt(txt_path: str, chunk_size: int = 300):
    """
    讀取 TXT → 逐筆抓 Ecatalog 資料 → 得到 all_rows (list[dict])
    然後切成多個 chunk，寫入：
      集合名稱：完整型錄資料YYYYMMDD
      文件：chunk_0001, chunk_0002, ...
      欄位：
        - chunk_index  (第幾個 chunk，從 1 開始)
        - row_count    (這個 chunk 裡有幾筆)
        - rows         (實際的物件陣列)
        - created_at   (時間戳)

    ✅ 不再寫入 aiwu_json（避免之前 nested entity 的錯誤）
    ✅ 仍會在 aiwu_txt 裡存一份網址列表與原始 TXT 內容（方便你查）
    """
    log_lines = []

    def log(msg: str):
        print(msg)
        log_lines.append(msg)

    log(f"🟢 讀取 TXT：{txt_path}")

    # 讀 TXT 裡的網址
    with open(txt_path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        msg = "TXT 檔裡沒有任何網址，無法產生 chunk。"
        log(f"❌ {msg}")
        raise RuntimeError(msg)

    total = len(urls)
    log(f"🔢 本次實際要處理 {total} 筆網址")

    all_rows = []
    for i, url in enumerate(urls, 1):
        log(f"[{i}/{total}] 擷取：{url}")
        info = extract_info_simple(url)
        all_rows.append(info)

    # ========= 在 Firestore 存 aiwu_txt 紀錄（原始網址） =========
    date_str = datetime.now().strftime("%Y%m%d")
    txt_doc_id = f"愛屋{date_str}"
    txt_content = "\n".join(urls)

    try:
        db.collection(AIWU_TXT_COLLECTION).document(txt_doc_id).set({
            "created_at": firestore.SERVER_TIMESTAMP,
            "filename": os.path.basename(txt_path),
            "url_count": len(urls),
            "urls": urls,
            "raw_txt": txt_content,
        })
        log(f"☁ 已儲存 TXT 至 {AIWU_TXT_COLLECTION}/{txt_doc_id}")
    except Exception as e:
        log(f"⚠ 寫入 {AIWU_TXT_COLLECTION}/{txt_doc_id} 失敗：{e}")

    # ========= 建立 chunk Collection：完整型錄資料YYYYMMDD =========
    collection_name = f"完整型錄資料{date_str}"
    log(f"📚 開始寫入 Firestore：集合 {collection_name}")

    # 先清掉同名舊集合（避免混資料）
    try:
        for doc in db.collection(collection_name).stream():
            db.collection(collection_name).document(doc.id).delete()
        log(f"🧹 已清空舊集合：{collection_name}")
    except Exception as e:
        log(f"⚠ 清空舊集合 {collection_name} 失敗（可能本來就不存在）：{e}")

    # 分 chunk
    chunks = [
        all_rows[i:i + chunk_size]
        for i in range(0, len(all_rows), chunk_size)
    ]

    for idx, chunk in enumerate(chunks, start=1):
        chunk_id = f"chunk_{idx:04d}"
        try:
            db.collection(collection_name).document(chunk_id).set({
                "chunk_index": idx,
                "row_count": len(chunk),
                "rows": chunk,
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            log(f"📄 已寫入 {collection_name}/{chunk_id} 共 {len(chunk)} 筆")
        except Exception as e:
            log(f"⚠ 寫入 {collection_name}/{chunk_id} 失敗：{e}")

    log("✅ TXT → CHUNK → FIRESTORE 完成")

    return {
        "doc_id": collection_name,          # 讓後台訊息可以顯示集合名稱
        "count": len(all_rows),             # 總筆數
        "chunks": len(chunks),              # chunk 數量
        "log": "\n".join(log_lines),
    }


# =========================================================================
# 4. 共用欄位處理
# =========================================================================

def build_image_url(link: str):
    """從 Ecatalog 連結推回主圖 a.jpg"""
    try:
        no_match = re.search(r'[?&]No=([A-Z0-9]+)', link, flags=re.I)
        aid_match = re.search(r'[?&]AID=([A-Z0-9]+)', link, flags=re.I)
        if no_match and aid_match:
            no = no_match.group(1)
            aid = aid_match.group(1)
            return f"https://hq.houseol.com.tw/images/pictures/{aid}{no}a.jpg"
    except Exception:
        return None
    return None


def normalize_row_for_aiwu_rows(raw: dict) -> dict:
    """
    把各種來源的欄位整理成統一格式
    給前台用的 df_raw / aiwu_rows
    """
    row = dict(raw)

    # 1. 網址 / EDM 連結
    if not row.get("網址"):
        if row.get("EDM連結"):
            row["網址"] = row["EDM連結"]
        elif row.get("網址連結"):
            row["網址"] = row["網址連結"]

    # 2. 物件編號
    house_id = str(row.get("物件編號", "")).strip()
    if not house_id:
        url = row.get("網址") or row.get("EDM連結") or ""
        m = re.search(r"[?&]No=([A-Z0-9]+)", url, flags=re.I)
        if m:
            house_id = m.group(1)
    if house_id:
        row["物件編號"] = house_id

    # 3. 價格欄位 → 委託總價
    if "委託總價" not in row:
        for key in ["總價", "總價(萬)", "委託總價(萬)"]:
            if key in row and row.get(key):
                row["委託總價"] = row[key]
                break

    # 4. 主建物坪 / 建物面積
    if "主建物坪" not in row:
        for key in ["主建物坪", "建物面積", "建坪"]:
            if key in row and row.get(key):
                row["主建物坪"] = row[key]
                break

    # 5. 屋齡
    if "屋齡" not in row:
        for key in ["屋齡(年)", "屋齡年數"]:
            if key in row and row.get(key):
                row["屋齡"] = row[key]
                break

    # 6. 圖片網址（主圖）
    if "image_url" not in row or not row.get("image_url"):
        if row.get("圖片連結"):
            imgs = str(row["圖片連結"]).split(",")
            if imgs:
                row["image_url"] = imgs[0].strip()
        elif row.get("網址"):
            img = build_image_url(str(row["網址"]))
            if img:
                row["image_url"] = img

    return row


def add_image_list_to_row(row: dict) -> dict:
    """
    在同步階段就把圖片 a～t 全部塞進 row['image_list'] 裡
    優先使用『圖片連結』欄位，否則由 image_url 推斷 a~t
    """
    # 若已經有 image_list，就不重複處理
    if isinstance(row.get("image_list"), list) and row["image_list"]:
        return row

    # 1️⃣ 若有「圖片連結」欄位（多個逗號分隔）
    imgs_field = row.get("圖片連結")
    if imgs_field:
        img_list = [u.strip() for u in str(imgs_field).split(",") if u.strip()]
        if img_list:
            row["image_list"] = img_list
            return row

    # 2️⃣ 退回 image_url 推 a~t
    image_url = row.get("image_url")

    if not image_url:
        # 再由網址反推
        link = row.get("網址") or row.get("EDM連結")
        if link:
            image_url = build_image_url(str(link))

    if not image_url:
        return row  # 沒有圖片資訊，直接回傳

    # 嘗試用 regex 擷取 prefix（去掉最後一個字母+副檔名）
    # 例如: https://.../H229QQ00007697a.jpg → prefix = .../H229QQ00007697
    m = re.search(r"^(?P<prefix>.+)[a-zA-Z]\.(jpg|jpeg|png|gif)$", image_url)
    if not m:
        # 格式不是 a.jpg 類型，就直接用單張
        row["image_list"] = [image_url]
        return row

    prefix = m.group("prefix")

    img_list = []
    for ch in "abcdefghijklmnopqrst":
        url = f"{prefix}{ch}.jpg"
        img_list.append(url)

    row["image_list"] = img_list
    return row


# =========================================================================
# 5. 單一物件 HTML：generate_one_html_from_json
# =========================================================================
def generate_one_html_from_json(house_id: str, row: dict):
    """
    用 sedm.html 模板產生一個物件頁 HTML，
    上傳到 Firebase Storage，並在 sedm_pages 裡紀錄 page_url。
    """

    image_list = _build_image_list(row)

    # 靜態檔案 base URL
    static_base = "https://ellenfindhome.com/static"

    # 在 Flask app context 裡 render sedm.html
    with current_app.app_context():
        html = render_template(
            "sedm.html",
            image_list=image_list,
            title=row.get("房屋標題", ""),
            region=row.get("區域", ""),
            total_price=row.get("委託總價", ""),
            reg_area=row.get("登記坪數", ""),
            building_area=row.get("建物面積", ""),
            main_area=row.get("主建物坪", ""),
            sub_area=row.get("附屬建物", ""),
            public_area=row.get("公設建坪", ""),
            public_ratio=row.get("公設比", ""),
            unit_price=row.get("每坪單價", ""),
            land_status=row.get("土地登記", ""),
            usage_zone=row.get("使用分區", ""),
            base_area=row.get("總基地坪", ""),
            floor_info=row.get("樓別/樓高", ""),
            layout=row.get("房/廳/衛", ""),
            parking_type=row.get("車位型式", ""),
            parking_num=row.get("車位/編號", ""),
            status_type=row.get("現況類別/謄本用途", ""),
            building_type=row.get("類型/現況", ""),
            community=row.get("社區/建物", ""),
            management_fee=row.get("管理費|車位管理費", ""),
            direction=row.get("物件座向", ""),
            road_width=row.get("面臨路寬", ""),
            build_date=row.get("竣工日期", ""),
            age=row.get("屋齡", ""),
            appearance=row.get("建物外觀", ""),
            structure=row.get("建物結構", ""),
            near_park=row.get("鄰近公園", ""),
            near_market=row.get("鄰近市場", ""),
            near_school=row.get("鄰近學校", ""),
            circle=row.get("生活圈", ""),
            house_id=house_id,
            key_status=row.get("鑰匙/帶看", ""),
            gas=row.get("瓦斯", ""),
            units_per_floor=row.get("每層戶數", ""),
            corner=row.get("邊間", ""),
            elevators=row.get("電梯總數", ""),
            feature=row.get("環境特色", ""),
            map_link=row.get("地圖連結", ""),
            static_base=static_base,
        )

    # 上傳到 Storage
    blob_path = f"sedm_pages/{house_id}.html"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    # ⭐ 關鍵：設為公開
    blob.make_public()
    page_url = blob.public_url

    # 寫回 Firestore：sedm_pages
    db.collection(SEDM_PAGES_COLLECTION).document(house_id).set(
        {
            "house_id": house_id,
            "page_url": page_url,
            "blob_path": blob_path,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    print(f"✅ 產出並上傳 sedm_pages/{house_id}.html → {page_url}")

# =========================================================================
# 6. 讀取「最新」完整型錄資料（chunk 版本）
# =========================================================================

def _load_latest_from_chunk_collections():
    """
    掃描所有 collection，找出名字以『完整型錄資料』開頭的，
    選日期最大的那個，把底下的 chunk_000x / rows[] 串在一起。
    """
    latest_name = None

    for coll in db.collections():
        coll_id = coll.id
        if coll_id.startswith("完整型錄資料"):
            if (latest_name is None) or (coll_id > latest_name):
                latest_name = coll_id

    if not latest_name:
        return None, None

    print(f"📚 使用 chunk 版完整型錄：{latest_name}")
    all_rows = []

    for doc in db.collection(latest_name).stream():
        d = doc.to_dict() or {}
        rows = d.get("rows") or []
        if isinstance(rows, list):
            all_rows.extend(rows)

    return latest_name, all_rows


# =========================================================================
# 7. 核心同步：sync_html_from_firestore_json（其實是 chunk）
# =========================================================================

def sync_html_from_firestore_json():
    """
    從 Firestore 取得「最新完整型錄資料」：
    - 只用 chunk 版本：完整型錄資料YYYYMMDD / chunk_000x / rows[]
    然後同步：
    - 寫入 / 更新 aiwu_rows（給前台列表用）
    - 產生 / 更新 sedm_pages：把 sedm.html 渲染好，上傳到 Storage，並寫入 page_url
    - 多出來的舊物件會刪除（代表下架）
    """

    # 1️⃣ 先把「最新的完整型錄資料YYYYMMDD」整批 rows 抓出來
    src_id, data = _load_latest_from_chunk_collections()
    if not data:
        raise RuntimeError(
            "找不到名稱開頭為『完整型錄資料』的 chunk collection，"
            "請先執行『搜尋愛屋網頁』產生最新完整型錄。"
        )

    print(f"🟢 使用來源：{src_id}，原始筆數：{len(data)}")

    # 2️⃣ 整理成：{物件編號: row}
    rows_by_id = {}
    for item in data:
        # 正規化欄位名稱 / 資料型態，方便前台統一使用
        row = normalize_row_for_aiwu_rows(item)

        # 這裡如果還想在 row 裡保留展開後的圖片清單，可以加這行；
        # 之後 generate_one_html_from_json 會再保險一次處理圖片
        row = add_image_list_to_row(row)

        house_id = str(row.get("物件編號", "")).strip()
        if not house_id:
            continue
        rows_by_id[house_id] = row

    new_ids = set(rows_by_id.keys())
    print(f"📌 有效物件編號數量：{len(new_ids)}")

    if not new_ids:
        raise RuntimeError(
            "完整型錄資料裡找不到任何有效的『物件編號』，"
            "為避免把舊資料全部刪光，這次同步不會動任何資料。"
        )

    # 3️⃣ 讀現在 sedm_pages 裡已經存在的物件，用來判斷新增 / 更新 / 刪除
    existing_sedm_docs = {
        d.id: d.to_dict() for d in db.collection(SEDM_PAGES_COLLECTION).stream()
    }
    old_ids = set(existing_sedm_docs.keys())

    missing = new_ids - old_ids   # 新增
    common = new_ids & old_ids    # 更新
    to_delete = old_ids - new_ids # 下架

    print(f"➕ 新增 {len(missing)}，🔁 更新 {len(common)}，🗑 準備刪除 {len(to_delete)}")

    # 4️⃣ 新增 & 更新（aiwu_rows + sedm_pages）
    for house_id in sorted(new_ids):
        row = rows_by_id[house_id]

        # --- 4-1. 更新 / 寫入 aiwu_rows ---
        try:
            save_row = dict(row)
            save_row["物件編號"] = house_id
            # 給前台列表用的詳細頁連結（/house/<house_id>，會再 redirect 到 Storage 靜態頁）
            save_row["detail_url"] = f"/house/{house_id}"

            db.collection(AIWU_ROWS_COLLECTION).document(house_id).set(
                save_row,
                merge=True,
            )
        except Exception as e:
            print(f"⚠ 寫入 {AIWU_ROWS_COLLECTION}/{house_id} 失敗：{e}")

        # --- 4-2. 產生 / 更新 sedm_pages HTML ---
        #   generate_one_html_from_json：
        #   - 用 sedm.html 模板 render 出完整 HTML
        #   - 上傳到 Storage：sedm_pages/<house_id>.html
        #   - 在 sedm_pages collection 裡寫入 page_url
        try:
            generate_one_html_from_json(house_id, row)
        except Exception as e:
            print(f"⚠ 產生 sedm_pages/{house_id}.html 失敗：{e}")

    # 5️⃣ 刪除「已下架」物件（sedm_pages + aiwu_rows）
    for house_id in sorted(to_delete):
        try:
            db.collection(SEDM_PAGES_COLLECTION).document(house_id).delete()
            db.collection(AIWU_ROWS_COLLECTION).document(house_id).delete()
            print(f"🗑 已刪除 {house_id}（sedm_pages + aiwu_rows）")
        except Exception as e:
            print(f"⚠ 刪除 {house_id} 失敗：{e}")

    result = {
        "added": len(missing),
        "updated": len(common),
        "deleted": len(to_delete),
    }
    print(f"📊 sync_html_from_firestore_json 完成：{result}")
    return result


# =========================================================================
# 8. 一鍵 Pipeline（給 /admin/aiwu_update?action=pipeline 用）
# =========================================================================

def run_aiwu_pipeline(headless=True):
    """
    一鍵：登入 + 抓網址存 TXT → 轉 chunk Collection → 同步 HTML / aiwu_rows / sedm_pages
    """
    url_count, txt_path = crawl_aiwu_and_save_txt(headless=headless)
    json_result = generate_aiwu_json_from_txt(txt_path)
    sync_result = sync_html_from_firestore_json()

    return {
        "url_count": url_count,
        "json_count": json_result["count"],
        "added_html": sync_result["added"],
        "changed_html": sync_result["updated"],
        "deleted_html": sync_result["deleted"],
    }

from urllib.parse import quote
def upload_html_to_storage(house_id: str, html: str):
    blob_path = f"sedm/{house_id}.html"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    # ✅ 這個 URL 會走 Firebase Storage + Rules
    encoded_path = quote(blob_path, safe="")
    page_url = (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/"
        f"{encoded_path}?alt=media"
    )

    db.collection("sedm_pages").document(house_id).set(
        {
            "page_url": page_url,
            "storage_path": blob_path,
        },
        merge=True,
    )
    return page_url

def _build_image_list(row: dict):
    """
    統一幫一筆 row 生出 image_list：

    1. 如果 row 本來就有 image_list，就直接用
    2. 優先用「圖片連結」欄位（逗號分隔）
    3. 再用 image_url 展開 a～t
    4. 再不行，從 網址 / EDM 連結 推出 a.jpg，再展開 a～t
    """

    # 1️⃣ 已經有 image_list 了就直接用
    image_list = row.get("image_list")
    if isinstance(image_list, list) and image_list:
        return image_list

    # 2️⃣ Firestore 裡的「圖片連結」欄位（多個逗號分隔）
    imgs_field = row.get("圖片連結")
    image_list = []
    if imgs_field:
        image_list = [u.strip() for u in str(imgs_field).split(",") if u.strip()]

    # 3️⃣ 沒有的話，用 image_url 展開 a～t
    if not image_list:
        image_url = row.get("image_url")
        if image_url:
            image_list = expand_houseol_images(image_url)
        else:
            # 4️⃣ 再退一步：從 網址 / EDM 連結 推 a.jpg，再展開
            url = row.get("網址") or row.get("EDM連結")
            if url:
                img = build_image_url(str(url))
                if img:
                    image_list = expand_houseol_images(img)

    return image_list or []


def upload_html_to_storage(house_id: str, html: str):
    """
    把單一物件的 HTML 上傳到 Firebase Storage，
    路徑：sedm/<house_id>.html，並設為公開，最後寫回 Firestore。
    """
    blob_path = f"sedm/{house_id}.html"
    blob = bucket.blob(blob_path)

    # 寫入 HTML
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    # 一定要設 public，前台訪客才不會 AccessDenied
    blob.make_public()
    page_url = blob.public_url

    # 存回 sedm_pages 集合
    db.collection(SEDM_PAGES_COLLECTION).document(house_id).set(
        {
            "page_url": page_url,
            "storage_path": blob_path,
        },
        merge=True,
    )

    print(f"✅ 已產生並上傳 {blob_path} → {page_url}")
    return page_url
