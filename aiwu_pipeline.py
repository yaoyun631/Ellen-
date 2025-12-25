# aiwu_pipeline.py
# -*- coding: utf-8 -*-
"""
目標：
A) chunk（完整型錄資料YYYYMMDD）存「型錄等級」rows：不去重（例如 1078）
B) aiwu_items：前台顯示用（不去重）=> 1078（或更多），每筆都有 batch_id
C) aiwu_rows + sedm_pages：唯一 No 去重後（例如 1057），只更新有變動/缺漏（fingerprint）
D) aiwu_meta/latest：紀錄最新 batch_id，避免前台混舊資料
"""

import os
import re
import json
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from firebase_client import db, bucket
from firebase_admin import firestore
from flask import render_template, current_app

# ========= 愛屋網址 =========
AIWU_LOGIN_URL = "https://es.houseol.com.tw/login.aspx"
AIWU_LIST_URL = "https://es.houseol.com.tw/index.aspx?module=SellHouse&file=Object#"

# ========= Firestore Collections =========
AIWU_ITEMS_COLLECTION = "aiwu_items"      # ✅ 前台：型錄級（不去重）
AIWU_ROWS_COLLECTION = "aiwu_rows"        # ✅ 後台：唯一 No（去重後）
SEDM_PAGES_COLLECTION = "sedm_pages"      # ✅ 靜態頁
AIWU_TXT_COLLECTION = "aiwu_txt"          # 原始 TXT
AIWU_META_COLLECTION = "aiwu_meta"        # ✅ 存 latest batch_id
AIWU_LATEST_DOC = "latest"

# chunk collections: 完整型錄資料YYYYMMDD
CHUNK_PREFIX = "完整型錄資料"

# ========= 基本設定 =========
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

__all__ = [
    "crawl_aiwu_and_save_txt",
    "generate_aiwu_json_from_txt",
    "sync_items_from_latest_chunk",       # ✅ 補上（你之前 ImportError 就是缺這個）
    "sync_html_from_firestore_json",      # ✅ 同步唯一 No（aiwu_rows + sedm_pages）
    "sync_items_and_rows_from_latest_chunk",
    "run_aiwu_pipeline",
    "count_unique_house_ids_from_txt",
    "build_image_url",
    "expand_houseol_images",
    "get_latest_batch_id",
]

# =========================================================================
# Log helper（終端機即時）
# =========================================================================
def tlog(*args):
    ts = datetime.now().strftime("%H:%M:%S")
    msg = " ".join(str(a) for a in args)
    print(f"[{ts}] {msg}", flush=True)

# =========================================================================
# Firestore 安全化：避免某筆資料害整批中斷
# =========================================================================
def _sanitize_firestore_value(v):
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, datetime):
        return v
    if isinstance(v, list):
        return [_sanitize_firestore_value(x) for x in v]
    if isinstance(v, dict):
        return _sanitize_firestore_dict(v)
    if isinstance(v, set):
        return [_sanitize_firestore_value(x) for x in sorted(list(v))]
    return str(v)

def _sanitize_firestore_key(k: str) -> str:
    k = (k or "").strip()
    k = k.replace(".", "．")
    k = k.replace("\u0000", "")
    return k

def _sanitize_firestore_dict(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        sk = _sanitize_firestore_key(str(k))
        out[sk] = _sanitize_firestore_value(v)
    return out

# =========================================================================
# 小工具：從網址抓 No / AID
# =========================================================================
def extract_house_id_from_url(url: str) -> str:
    m = re.search(r"[?&]No=([A-Z0-9]+)", url or "", flags=re.I)
    return (m.group(1) if m else "").strip()

def extract_aid_from_url(url: str) -> str:
    m = re.search(r"[?&]AID=([A-Z0-9]+)", url or "", flags=re.I)
    return (m.group(1) if m else "").strip()

def count_unique_house_ids_from_txt(txt_path: str) -> Tuple[int, set]:
    ids = set()
    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            u = line.strip()
            if not u:
                continue
            hid = extract_house_id_from_url(u)
            if hid:
                ids.add(hid)
    return len(ids), ids

# =========================================================================
# HouseOL 圖片：主圖 a.jpg → 展開 a~t（只保留存在的）
# =========================================================================
def expand_houseol_images(image_url: str) -> List[str]:
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

    return candidates or [image_url]

def build_image_url(ecatalog_url: str) -> Optional[str]:
    """
    從 Ecatalog 連結推回主圖 a.jpg：
    https://hq.houseol.com.tw/images/pictures/{AID}{No}a.jpg
    """
    no = extract_house_id_from_url(ecatalog_url)
    aid = extract_aid_from_url(ecatalog_url)
    if no and aid:
        return f"https://hq.houseol.com.tw/images/pictures/{aid}{no}a.jpg"
    return None

# =========================================================================
# 0. 登入愛屋
# =========================================================================
def aiwu_login(driver):
    house_id = os.environ.get("AIWU_HOUSE_ID", "H229")
    member_id = os.environ.get("AIWU_MEMBER_ID", "sp290")
    member_pw = os.environ.get("AIWU_MEMBER_PW", "0000")

    driver.get(AIWU_LOGIN_URL)
    time.sleep(1.5)

    house_input = driver.find_element(By.ID, "HouseID")
    member_input = driver.find_element(By.ID, "MemberID")
    pw_input = driver.find_element(By.ID, "MemberPW")

    house_input.clear(); house_input.send_keys(house_id)
    member_input.clear(); member_input.send_keys(member_id)
    pw_input.clear(); pw_input.send_keys(member_pw)

    login_btn = driver.find_element(By.ID, "LinkButton1")
    login_btn.click()

    time.sleep(2)
    tlog(f"🟢 已嘗試登入愛屋（{house_id} / {member_id}）")

# =========================================================================
# 1. 登入 + 抓列表 + 存 TXT
# =========================================================================
def crawl_aiwu_and_save_txt(headless=True, click_interval=1.0, max_rounds=200):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=options)
    try:
        aiwu_login(driver)
        driver.get(AIWU_LIST_URL)
        time.sleep(1.5)
        tlog(f"📄 進入列表頁：{AIWU_LIST_URL}")

        catalog_links = set()
        last_count = 0
        same_count_rounds = 0

        for round_idx in range(1, max_rounds + 1):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            a_tags = driver.find_elements(By.TAG_NAME, "a")
            for a in a_tags:
                text = (a.text or "").strip()
                href = a.get_attribute("href") or ""
                if text == "型錄" and "Ecatalog.aspx" in href:
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = "https://es.houseol.com.tw" + href
                    catalog_links.add(href)

            now_count = len(catalog_links)
            tlog(f"🔁 第 {round_idx} 輪，目前抓到 {now_count} 筆型錄連結")

            if now_count == last_count:
                same_count_rounds += 1
            else:
                same_count_rounds = 0
            last_count = now_count

            # 點「查看更多」
            try:
                load_more = driver.find_element(By.CSS_SELECTOR, "a.load_more")
                if load_more.is_displayed():
                    tlog("👉 點擊『查看更多』")
                    load_more.click()
                    time.sleep(click_interval)
                else:
                    tlog("⚠️ load_more 不可見，準備結束")
                    break
            except Exception:
                tlog("⚠️ 找不到『查看更多』按鈕，準備結束")
                break

            if same_count_rounds >= 3:
                tlog("⚠️ 連續多輪沒有增加，停止")
                break

        links = sorted(catalog_links)
        tlog(f"🟢 共抓到 {len(links)} 筆型錄網址")

        date_str = datetime.now().strftime("%Y%m%d")
        txt_path = os.path.join(DATA_DIR, f"愛屋{date_str}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(links))

        unique_cnt, _ = count_unique_house_ids_from_txt(txt_path)
        tlog(f"📌 TXT 網址數：{len(links)}；唯一物件編號數（No 去重）：{unique_cnt}")

        return len(links), txt_path
    finally:
        driver.quit()

# =========================================================================
# 2. 單頁解析：extract_info_simple（失敗也先占位 No）
# =========================================================================
def extract_info_simple(url: str) -> dict:
    house_id = extract_house_id_from_url(url)
    result = {"網址": url}
    if house_id:
        result["物件編號"] = house_id

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 標題/區域
        title_el = soup.select_one(".title h3")
        area_el = soup.select_one("#VarArea .caption")
        result["房屋標題"] = title_el.get_text(strip=True) if title_el else ""
        result["區域"] = area_el.get_text(strip=True) if area_el else ""

        # 表格欄位
        for tr in soup.select(".t-tr"):
            ths = tr.select(".t-th")
            tds = tr.select(".t-td")
            for th, td in zip(ths, tds):
                label = (
                    th.get_text(strip=True)
                    .replace("：", "")
                    .replace("\xa0", "")
                    .replace("\u3000", "")
                    .strip()
                )
                p = td.select_one("p")
                value = (p.get_text(strip=True) if p else td.get_text(strip=True))
                value = value.replace("\xa0", "").replace("\u3000", "").strip()
                if label and (label not in result):
                    result[label] = value

        # 屋齡
        age_text = ""
        age_div = None
        for div in soup.select("div.title"):
            clean_title = div.get_text(strip=True).replace(" ", "").replace("\u3000", "")
            if "屋齡" in clean_title:
                age_div = div
                break
        if age_div:
            next_sib = age_div.find_next_sibling()
            if next_sib:
                for p in next_sib.find_all("p"):
                    txt = p.get_text(strip=True).replace("\u3000", "")
                    m = re.search(r"(\d+\.?\d*)\s*年", txt)
                    if m:
                        age_text = m.group(1) + "年"
                        break
                if not age_text:
                    age_text = next_sib.get_text(strip=True).replace("\u3000", "")
            else:
                age_text = age_div.parent.get_text(strip=True).replace("屋齡", "").replace("\u3000", "").strip()
        if age_text:
            result["屋齡"] = age_text

        # 環境特色
        features = []
        good_span = soup.select_one("#GoodSpan")
        if good_span:
            for s in good_span.select("div.points strong"):
                t = s.get_text(strip=True)
                if t:
                    features.append(t)
        if features:
            result["環境特色"] = "\n".join(features)

        # 地圖連結
        map_btn = soup.select_one("a#otherfunc1")
        if map_btn and map_btn.has_attr("onclick"):
            onclick_text = map_btn["onclick"]
            m = re.search(r"fancybox\('([^']+)'", onclick_text)
            if m:
                result["地圖連結"] = m.group(1)

        # 主圖 + 圖片列表（這裡先放主圖，後面 normalize 再展開）
        img = build_image_url(url)
        if img:
            result["image_url"] = img

        # 保險：如果頁面改版仍保留 No
        if not result.get("物件編號") and house_id:
            result["物件編號"] = house_id

        return result

    except Exception as e:
        result["錯誤"] = str(e)
        # 失敗也保住 No
        if house_id:
            result["物件編號"] = house_id
        return result

# =========================================================================
# batch_id / latest meta
# =========================================================================
def _make_batch_id(date_str: Optional[str] = None) -> str:
    date_str = date_str or datetime.now().strftime("%Y%m%d")
    # 以秒級時間避免同日重跑覆蓋
    return f"{date_str}_{datetime.now().strftime('%H%M%S')}"

def set_latest_batch_id(batch_id: str, source: str, url_count: int, unique_house_count: int, chunk_collection: str):
    payload = {
        "batch_id": batch_id,
        "source": source,
        "url_count": url_count,
        "unique_house_count": unique_house_count,
        "chunk_collection": chunk_collection,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    db.collection(AIWU_META_COLLECTION).document(AIWU_LATEST_DOC).set(payload, merge=True)

def get_latest_batch_id() -> Optional[str]:
    try:
        doc = db.collection(AIWU_META_COLLECTION).document(AIWU_LATEST_DOC).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        bid = (data.get("batch_id") or "").strip()
        return bid or None
    except Exception:
        return None

# =========================================================================
# 3. TXT → chunk Collection：完整型錄資料YYYYMMDD（不去重）
#    同時寫 aiwu_txt
# =========================================================================
def generate_aiwu_json_from_txt(txt_path: str, chunk_size: int = 300, batch_id: Optional[str] = None):
    log_lines = []

    def log(msg: str):
        tlog(msg)
        log_lines.append(msg)

    log(f"🟢 讀取 TXT：{txt_path}")

    with open(txt_path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        raise RuntimeError("TXT 檔裡沒有任何網址")

    total = len(urls)
    unique_cnt, unique_ids = count_unique_house_ids_from_txt(txt_path)
    log(f"🔢 TXT 網址數：{total}；唯一物件編號數（No 去重）：{unique_cnt}")

    all_rows = []
    for i, url in enumerate(urls, 1):
        if i == 1 or i % 25 == 0 or i == total:
            log(f"…進度 {i}/{total}")
        all_rows.append(extract_info_simple(url))

    date_str = datetime.now().strftime("%Y%m%d")
    batch_id = batch_id or _make_batch_id(date_str=date_str)

    txt_doc_id = f"愛屋{date_str}"
    # 存 aiwu_txt
    try:
        db.collection(AIWU_TXT_COLLECTION).document(txt_doc_id).set({
            "created_at": firestore.SERVER_TIMESTAMP,
            "batch_id": batch_id,
            "filename": os.path.basename(txt_path),
            "url_count": len(urls),
            "unique_house_count": unique_cnt,
            "unique_house_ids": list(sorted(unique_ids)),
            "urls": urls,
            "raw_txt": "\n".join(urls),
        }, merge=True)
        log(f"☁ 已儲存 TXT 至 {AIWU_TXT_COLLECTION}/{txt_doc_id}（batch_id={batch_id}）")
    except Exception as e:
        log(f"⚠ 寫入 {AIWU_TXT_COLLECTION}/{txt_doc_id} 失敗：{e}")

    # 建 chunk collection（同日重跑你可能想覆蓋：這裡保留「同名集合清空」行為）
    collection_name = f"{CHUNK_PREFIX}{date_str}"
    log(f"📚 寫入 Firestore：集合 {collection_name}（batch_id={batch_id}）")

    # 清掉同名舊集合（同日重跑）
    try:
        for doc in db.collection(collection_name).stream():
            db.collection(collection_name).document(doc.id).delete()
        log(f"🧹 已清空舊集合：{collection_name}")
    except Exception as e:
        log(f"⚠ 清空舊集合 {collection_name} 失敗：{e}")

    chunks = [all_rows[i:i + chunk_size] for i in range(0, len(all_rows), chunk_size)]
    for idx, chunk in enumerate(chunks, start=1):
        chunk_id = f"chunk_{idx:04d}"
        db.collection(collection_name).document(chunk_id).set({
            "batch_id": batch_id,
            "chunk_index": idx,
            "row_count": len(chunk),
            "rows": chunk,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        log(f"📄 已寫入 {collection_name}/{chunk_id} 共 {len(chunk)} 筆")

    # ✅ 寫 latest batch meta（讓前台不混舊）
    set_latest_batch_id(
        batch_id=batch_id,
        source="txt->chunk",
        url_count=len(urls),
        unique_house_count=unique_cnt,
        chunk_collection=collection_name,
    )

    log("✅ TXT → CHUNK 完成")
    return {
        "batch_id": batch_id,
        "doc_id": collection_name,
        "count": len(all_rows),                # 1078
        "unique_house_count": unique_cnt,      # 1057
        "chunks": len(chunks),
        "log": "\n".join(log_lines),
    }

# =========================================================================
# 4. row 正規化 + image_list
# =========================================================================
def normalize_row_for_aiwu_rows(raw: dict) -> dict:
    row = dict(raw or {})

    # 網址
    if not row.get("網址"):
        for k in ["EDM連結", "網址連結"]:
            if row.get(k):
                row["網址"] = row[k]
                break

    # 物件編號
    house_id = str(row.get("物件編號", "")).strip()
    if not house_id:
        house_id = extract_house_id_from_url(row.get("網址", "") or "")
    if house_id:
        row["物件編號"] = house_id

    # 主要圖片
    if not row.get("image_url"):
        if row.get("圖片連結"):
            imgs = [u.strip() for u in str(row["圖片連結"]).split(",") if u.strip()]
            if imgs:
                row["image_url"] = imgs[0]
        elif row.get("網址"):
            img = build_image_url(str(row["網址"]))
            if img:
                row["image_url"] = img

    return row

def _build_image_list(row: dict) -> List[str]:
    image_list = row.get("image_list")
    if isinstance(image_list, list) and image_list:
        return image_list

    imgs_field = row.get("圖片連結")
    if imgs_field:
        image_list = [u.strip() for u in str(imgs_field).split(",") if u.strip()]
        if image_list:
            return image_list

    image_url = row.get("image_url")
    if image_url:
        return expand_houseol_images(image_url)

    link = row.get("網址") or row.get("EDM連結")
    if link:
        img = build_image_url(str(link))
        if img:
            return expand_houseol_images(img)

    return []

def add_image_list_to_row(row: dict) -> dict:
    if isinstance(row.get("image_list"), list) and row["image_list"]:
        return row
    row["image_list"] = _build_image_list(row)
    return row

# =========================================================================
# 5. fingerprint：避免每次都重產 HTML（針對唯一 No）
# =========================================================================
def _fingerprint_row(row: dict) -> str:
    stable = {
        "房屋標題": row.get("房屋標題", ""),
        "區域": row.get("區域", ""),
        "委託總價": row.get("委託總價", row.get("總價", "")),
        "登記坪數": row.get("登記坪數", ""),
        "屋齡": row.get("屋齡", ""),
        "網址": row.get("網址", ""),
        "地圖連結": row.get("地圖連結", ""),
        "環境特色": row.get("環境特色", ""),
        "image_url": row.get("image_url", ""),
        "image_list": row.get("image_list", []),
    }
    s = json.dumps(stable, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(s.encode("utf-8")).hexdigest()

# =========================================================================
# 6. 產生單一物件頁 HTML → Storage + sedm_pages（唯一 No）
# =========================================================================
def generate_one_html_from_json(house_id: str, row: dict):
    image_list = _build_image_list(row)
    static_base = "https://ellenfindhome.com/static"

    with current_app.app_context():
        html = render_template(
            "sedm.html",
            image_list=image_list,
            title=row.get("房屋標題", ""),
            region=row.get("區域", ""),
            total_price=row.get("委託總價", row.get("總價", "")),
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
            layout=row.get("房/廳/衛", row.get("房廳衛", "")),
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

    blob_path = f"sedm_pages/{house_id}.html"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")
    blob.make_public()
    page_url = blob.public_url

    db.collection(SEDM_PAGES_COLLECTION).document(house_id).set(
        {
            "house_id": house_id,
            "page_url": page_url,
            "blob_path": blob_path,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return page_url

# =========================================================================
# 7. 讀取最新 chunk collection（完整型錄資料YYYYMMDD）
# =========================================================================
def _load_latest_from_chunk_collections() -> Tuple[Optional[str], List[dict], Optional[str]]:
    """
    回傳：
      latest_collection_name, all_rows(不去重), batch_id(若找得到)
    """
    latest_name = None
    for coll in db.collections():
        cid = coll.id
        if cid.startswith(CHUNK_PREFIX):
            if (latest_name is None) or (cid > latest_name):
                latest_name = cid

    if not latest_name:
        return None, [], None

    all_rows: List[dict] = []
    batch_id = None

    for doc in db.collection(latest_name).stream():
        d = doc.to_dict() or {}
        if not batch_id:
            batch_id = (d.get("batch_id") or "").strip() or None
        rows = d.get("rows") or []
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    all_rows.append(r)

    return latest_name, all_rows, batch_id

# =========================================================================
# 8-A. 同步 aiwu_items（不去重）：讓前台顯示 1078
#      每一筆 doc 都要有 batch_id，避免混舊
# =========================================================================
def sync_items_from_latest_chunk(purge_same_batch_first: bool = True) -> dict:
    """
    ✅ 前台用（不去重）
    - 從最新 chunk 讀出 all_rows（例如 1078）
    - 寫入 aiwu_items：每 row 一筆
    - doc_id：使用 md5(url) + idx 避免重複
    - 每筆寫 batch_id
    """
    src_id, rows, batch_id_from_chunk = _load_latest_from_chunk_collections()
    if not src_id or not rows:
        raise RuntimeError("找不到『完整型錄資料YYYYMMDD』，請先跑 generate_aiwu_json_from_txt")

    batch_id = batch_id_from_chunk or get_latest_batch_id() or _make_batch_id()
    tlog(f"🟢 sync_items：來源 {src_id} rows={len(rows)} batch_id={batch_id}")

    # 更新 latest meta（保險：確保前台永遠有最新 batch）
    # url_count/unique_house_count 這裡無法精準算 url_count（因為 rows 可能含錯誤），先填 rows count
    unique_ids = {extract_house_id_from_url((r.get("網址") or "")) for r in rows}
    unique_ids.discard("")
    set_latest_batch_id(
        batch_id=batch_id,
        source="chunk->items",
        url_count=len(rows),
        unique_house_count=len(unique_ids),
        chunk_collection=src_id,
    )

    # 同批次先清掉（避免重跑同批次造成重複）
    if purge_same_batch_first:
        try:
            q = db.collection(AIWU_ITEMS_COLLECTION).where("batch_id", "==", batch_id).stream()
            deleted = 0
            for d in q:
                d.reference.delete()
                deleted += 1
            if deleted:
                tlog(f"🧹 已清掉 aiwu_items 同 batch_id 舊資料：{deleted} 筆")
        except Exception as e:
            tlog("⚠ 清除同 batch_id 舊 aiwu_items 失敗：", e)

    written = 0
    failed = 0

    for idx, raw in enumerate(rows, start=1):
        try:
            row = normalize_row_for_aiwu_rows(raw)
            row = add_image_list_to_row(row)

            url = str(row.get("網址") or "").strip()
            hid = str(row.get("物件編號") or extract_house_id_from_url(url)).strip()
            if hid:
                row["物件編號"] = hid

            # doc id：用 url hash + idx，確保「同一 url」也能保留多筆（極少但保險）
            url_key = url or f"no_url_{idx}"
            md = hashlib.md5(url_key.encode("utf-8")).hexdigest()[:12]
            doc_id = f"{batch_id}_{idx:04d}_{md}"

            row["_idx"] = idx
            row["batch_id"] = batch_id
            row["source_chunk"] = src_id
            row["updated_at"] = firestore.SERVER_TIMESTAMP

            row = _sanitize_firestore_dict(row)
            db.collection(AIWU_ITEMS_COLLECTION).document(doc_id).set(row, merge=False)
            written += 1

            if idx == 1 or idx % 50 == 0 or idx == len(rows):
                tlog(f"…sync_items 進度 {idx}/{len(rows)}")

        except Exception as e:
            failed += 1
            if failed <= 5:
                tlog("❌ sync_items 寫入失敗 idx=", idx, "err=", e)

    tlog(f"✅ sync_items 完成：寫入 {written} 筆（預期 {len(rows)}），失敗 {failed} 筆")
    return {
        "source": src_id,
        "batch_id": batch_id,
        "expected": len(rows),
        "written": written,
        "failed": failed,
    }

# =========================================================================
# 8-B. 核心同步：唯一 No（aiwu_rows）+ sedm_pages（1057）
# =========================================================================
def sync_html_from_firestore_json(force_regen_html: bool = False):
    res = _load_latest_from_chunk_collections()

    # 兼容回傳 (src_id, data) 或 (src_id, data, extra...)
    if isinstance(res, tuple) and len(res) >= 2:
        src_id, data = res[0], res[1]
    else:
        # 保底：避免整個流程直接掛
        src_id, data = None, []

    print(f"🟢 使用來源：{src_id}，chunk rows 筆數：{len(data)}", flush=True)

    # ① 以「物件編號」去重 rows_by_id
    rows_by_id: Dict[str, dict] = {}
    failed_ids: List[str] = []

    total_data = len(data)
    print(f"🔄 normalize/去重開始 data={total_data}", flush=True)

    for i, item in enumerate(data, start=1):
        if i == 1 or i % 20 == 0 or i == total_data:
            print(f"⏳ 去重進度 {i}/{total_data}", flush=True)

        row = normalize_row_for_aiwu_rows(item)
        # ✅ 關鍵：先不要展開 image_list（會超慢）
        # row = add_image_list_to_row(row)

        hid = str(row.get("物件編號", "")).strip()
        if not hid:
            hid = extract_house_id_from_url(row.get("網址", "") or "")
            if hid:
                row["物件編號"] = hid
        if not hid:
            continue

        if row.get("錯誤"):
            failed_ids.append(hid)

        rows_by_id[hid] = row

    new_ids = set(rows_by_id.keys())
    print(f"📌 最新唯一物件編號數（去重後）：{len(new_ids)}", flush=True)
    if failed_ids:
        print(f"⚠ 單頁抓取失敗但仍占位（不會少筆）：{len(set(failed_ids))} 筆", flush=True)

    # ② 以 aiwu_rows 當舊資料基準
    print("📥 讀取 Firestore aiwu_rows 現有資料中...", flush=True)
    existing_rows = {d.id: (d.to_dict() or {}) for d in db.collection(AIWU_ROWS_COLLECTION).stream()}
    old_ids = set(existing_rows.keys())

    to_add = new_ids - old_ids
    to_delete = old_ids - new_ids
    to_check = (new_ids & old_ids)

    print(
        f"➕ 缺少需補：{len(to_add)}；🗑 下架需刪：{len(to_delete)}；🔎 需比對：{len(to_check)}",
        flush=True
    )

    added = updated = deleted = 0
    html_regen = 0

    # ====== 進度 log 設定 ======
    ids_sorted = sorted(new_ids)
    total_ids = len(ids_sorted)
    t_start = time.time()

    def _fmt_sec(sec: float) -> str:
        sec = max(0, int(sec))
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h{m}m{s}s"
        if m:
            return f"{m}m{s}s"
        return f"{s}s"

    def _progress_log(idx: int, hid: str, extra: str = ""):
        elapsed = time.time() - t_start
        rate = elapsed / max(1, idx)
        eta = rate * (total_ids - idx)
        msg = (
            f"⏳ 同步進度 {idx}/{total_ids} | "
            f"已耗時 {_fmt_sec(elapsed)} | ETA {_fmt_sec(eta)} | hid={hid}"
        )
        if extra:
            msg += f" | {extra}"
        print(msg, flush=True)

    print("🚀 開始逐筆同步 aiwu_rows / sedm_pages ...", flush=True)

    # ③ 寫入/更新 aiwu_rows（每筆都不中斷）
    for idx, hid in enumerate(ids_sorted, start=1):
        # 每 20 筆印一次，另外第一筆、最後一筆一定印
        if idx == 1 or idx == total_ids or idx % 20 == 0:
            _progress_log(idx, hid)

        row = rows_by_id[hid]
        row_fp = _fingerprint_row(row)

        old_doc = existing_rows.get(hid, {})
        old_fp = old_doc.get("_fp", "")

        changed = (row_fp != old_fp)
        is_missing = (hid in to_add)

        if is_missing or changed:
            save_row = dict(row)
            save_row["物件編號"] = hid
            save_row["detail_url"] = f"/house/{hid}"
            save_row["_fp"] = row_fp
            save_row["updated_at"] = firestore.SERVER_TIMESTAMP

            try:
                save_row = _sanitize_firestore_dict(save_row)
                db.collection(AIWU_ROWS_COLLECTION).document(hid).set(save_row, merge=True)
            except Exception as e:
                print(f"❌ 寫入 aiwu_rows 失敗 {hid}: {e}", flush=True)
                continue

            if is_missing:
                added += 1
            else:
                updated += 1

        # HTML 產生條件
        need_html = force_regen_html or is_missing or changed
        if not need_html:
            try:
                sp = db.collection(SEDM_PAGES_COLLECTION).document(hid).get()
                if not sp.exists:
                    need_html = True
            except Exception:
                need_html = True

        if need_html:
            try:
                # ✅ 只有要產 HTML 的才補 image_list
                row = add_image_list_to_row(row)
                generate_one_html_from_json(hid, row)

                html_regen += 1
            except Exception as e:
                print(f"⚠ 產生 HTML 失敗 {hid}: {e}", flush=True)

    # ④ 刪除下架（aiwu_rows + sedm_pages + storage）
    if to_delete:
        print(f"🗑 開始刪除下架物件：{len(to_delete)} 筆 ...", flush=True)

    for i, hid in enumerate(sorted(to_delete), start=1):
        # 刪除也每 20 筆顯示一次
        if i == 1 or i == len(to_delete) or i % 20 == 0:
            print(f"🗑 刪除進度 {i}/{len(to_delete)} hid={hid}", flush=True)

        try:
            db.collection(AIWU_ROWS_COLLECTION).document(hid).delete()
            db.collection(SEDM_PAGES_COLLECTION).document(hid).delete()
            try:
                bucket.blob(f"sedm_pages/{hid}.html").delete()
            except Exception:
                pass
            deleted += 1
        except Exception as e:
            print(f"⚠ 刪除 {hid} 失敗：{e}", flush=True)

    # ⑤ 最終一致性校驗
    print("🔎 最終一致性校驗中...", flush=True)
    final_ids = [d.id for d in db.collection(AIWU_ROWS_COLLECTION).stream()]
    final_count = len(final_ids)

    print(f"✅ 最終 aiwu_rows 筆數：{final_count}", flush=True)
    print(f"✅ 預期最新唯一物件數：{len(new_ids)}", flush=True)

    if final_count != len(new_ids):
        print("❌ 筆數不一致：代表同步過程中有寫入失敗或權限/連線問題。", flush=True)
        missing_now = sorted(list(new_ids - set(final_ids)))
        if missing_now:
            print(f"❌ 目前仍缺少 {len(missing_now)} 筆，前 30 筆：{missing_now[:30]}", flush=True)

    elapsed_total = time.time() - t_start
    print(
        f"🎉 同步完成 | added={added} updated={updated} deleted={deleted} html_regenerated={html_regen} "
        f"| total_time={_fmt_sec(elapsed_total)}",
        flush=True
    )

    return {
        "source": src_id,
        "expected": len(new_ids),
        "final": final_count,
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "html_regenerated": html_regen,
        "failed_page_count": len(set(failed_ids)),
    }

# =========================================================================
# 8-C. 一次做：先 items（1078）再 rows/html（1057）
# =========================================================================
def sync_items_and_rows_from_latest_chunk(force_regen_html: bool = False) -> dict:
    t0 = time.time()
    r_items = sync_items_from_latest_chunk()
    r_rows = sync_html_from_firestore_json(force_regen_html=force_regen_html)
    tlog("⏱ sync_items_and_rows total secs =", round(time.time() - t0, 2))
    return {"items": r_items, "rows": r_rows}

# =========================================================================
# 9. 一鍵 Pipeline（selenium 抓 -> txt -> chunk -> items -> rows/html）
# =========================================================================
def run_aiwu_pipeline(headless=True, force_regen_html: bool = False):
    url_count, txt_path = crawl_aiwu_and_save_txt(headless=headless)

    unique_cnt, _ = count_unique_house_ids_from_txt(txt_path)
    tlog(f"📌 TXT 網址數：{url_count}")
    tlog(f"📌 唯一物件編號數（No 去重）：{unique_cnt}")

    json_result = generate_aiwu_json_from_txt(txt_path)
    # ✅ 用最新 chunk 同步前台 items 以及唯一 rows/html
    sync_pack = sync_items_and_rows_from_latest_chunk(force_regen_html=force_regen_html)

    return {
        "url_count": url_count,
        "unique_house_count": unique_cnt,
        "chunk_rows_count": json_result["count"],               # 1078
        "items_written": sync_pack["items"]["written"],         # 1078
        "expected_final_unique": sync_pack["rows"]["expected"], # 1057
        "final_rows_unique": sync_pack["rows"]["final"],        # 1057
        "added_unique": sync_pack["rows"]["added"],
        "updated_unique": sync_pack["rows"]["updated"],
        "deleted_unique": sync_pack["rows"]["deleted"],
        "html_regenerated": sync_pack["rows"]["html_regenerated"],
        "failed_page_count": sync_pack["rows"]["failed_page_count"],
        "batch_id": json_result["batch_id"],
    }
