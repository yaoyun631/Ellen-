from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file, jsonify, send_from_directory,
    current_app, abort, Response
)
import pandas as pd
import os
import re
import math
import csv
from blog.routes import blog_bp
import random
import json
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from io import BytesIO
from werkzeug.utils import secure_filename
import sqlite3
import requests
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

from firebase_client import db, bucket
from firebase_admin import firestore   # 👈 加這一行



# 🔹 愛屋 pipeline（JSON 版）
from aiwu_pipeline import (
    crawl_aiwu_and_save_txt,
    generate_aiwu_json_from_txt,
    sync_html_from_firestore_json,
    build_image_url,
)

# ========= 一般工具 / 常數 =========

def save_contact(name, phone, message):
    # 如果資料夾不存在先建立
    os.makedirs("data", exist_ok=True)

    # 如果檔案不存在就建立空 list
    if not os.path.exists(CONTACT_FILE):
        with open(CONTACT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

    # 讀舊資料
    with open(CONTACT_FILE, "r", encoding="utf-8") as f:
        contacts = json.load(f)

    # 新增一筆
    contacts.append({
        "name": name,
        "phone": phone,
        "message": message,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending"
    })

    # 寫回去
    with open(CONTACT_FILE, "w", encoding="utf-8") as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)

def send_email(name, phone, message_text):
    FORM_ENDPOINT = "https://formspree.io/f/xjkzojnp"

    payload = {
        "姓名": name,
        "電話": phone,
        "內容": message_text,
        "_subject": f"網站新預約 / 諮詢 - {name}",
    }

    try:
        resp = requests.post(FORM_ENDPOINT, data=payload, timeout=10)
        print("Formspree 回應代碼:", resp.status_code)
        print("Formspree 回應內容:", resp.text[:200])
        return resp.status_code in (200, 201, 202)
    except Exception as e:
        print("❌ Formspree 寄信失敗：", repr(e))
        return False


def expand_houseol_images(image_url: str):
    """
    給一張愛屋 / houseol 類型的圖片網址，展開 a ~ t 這種尾碼的所有圖片。
    - 如果網址裡有像 `_a.` 或 `a.` 的 pattern，就依照 a~t 產生多張
    - 如果沒有符合 pattern，就只回傳原圖一張
    """
    if not image_url:
        return []

    url = str(image_url).strip()
    if not url:
        return []

    # 例： ..._a.jpg 或 ...a.jpg
    m = re.search(r'([a-t])(\.\w+)$', url)
    images = []

    if m:
        start_letter = m.group(1)   # a
        ext = m.group(2)            # .jpg / .png
        prefix = url[:m.start(1)]   # 去掉最後一個字母

        letters = "abcdefghijklmnopqrst"
        start_index = letters.index(start_letter)

        for ch in letters[start_index:]:
            candidate = f"{prefix}{ch}{ext}"
            images.append(candidate)
    else:
        # 找不到尾碼 pattern，就回傳原圖
        images.append(url)

    return images



# 建立（存在即可）租屋 DB
conn = sqlite3.connect("rent_data.db")
conn.close()

app = Flask(__name__)
app.secret_key = "awsedfr123456"

# 上傳 / 靜態 設定
app.config['UPLOAD_FOLDER'] = 'static/uploads'
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'data')
ALLOWED_EXTENSIONS_EXCEL = {'xls', 'xlsx'}
SLIDE_FOLDER = os.path.join(app.static_folder, 'images', 'carousel')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif'}
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['RENT_UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'data', 'rent')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 部落格
app.register_blueprint(blog_bp, url_prefix='/blog')

# 常數
ADMIN_PASSWORD = "0601"
DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, 'videos.csv')
CONTACT_FILE = 'contacts.json'

# 建立資料夾
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ========= 共用工具（售屋） =========

def simplify_address(address):
    if not isinstance(address, str):
        return ""
    match = re.search(r'^(.+?[段路街巷弄])', address)
    return match.group(1) if match else address

def format_layout(s):
    if not isinstance(s, str) or s.strip() == "":
        return "0"
    if "//" in s:
        return ""

    parts = s.split('/')
    if len(parts) == 3:
        try:
            rooms = parts[0].strip() or "0"
            halls = parts[1].strip() or "0"
            baths = parts[2].strip() or "0"
            return f"{rooms}房{halls}廳{baths}衛"
        except:
            return s
    return s

def clean_price(val):
    try:
        if pd.isna(val):
            return None
        s = str(val).replace(",", "").strip()
        if "萬" in s:
            s = s.replace("萬", "")
            num = float(s) * 10000
        else:
            num = float(s)
        return int(num)
    except:
        return None

def clean_float(val):
    try:
        if pd.isna(val):
            return None
        return float(str(val).replace(",", "").replace("萬", "").replace("坪", "").replace("年", "").strip())
    except:
        return None

def extract_area(addr):
    if not isinstance(addr, str):
        return None
    m = re.search(r"(\S+區)", addr)
    return m.group(1) if m else None

taichung_districts = [
  "中區", "東區", "南區", "西區", "北區", "北屯區", "西屯區", "南屯區", "太平區", "大里區", "霧峰區", "烏日區",
  "豐原區", "后里區", "石岡區", "東勢區", "和平區", "新社區", "潭子區", "大雅區", "神岡區",
  "大肚區", "沙鹿區", "龍井區", "梧棲區", "清水區", "大甲區", "外埔區", "大安區"
]

# ========= 從 Firestore 載入完整型錄資料（aiwu_rows） =========

def build_df_from_firestore():
    """
    從 Firestore 的 aiwu_rows 讀全部物件，建 df_raw
    """
    try:
        docs = db.collection("aiwu_rows").stream()
    except Exception as e:
        print("讀取 aiwu_rows 失敗：", e)
        return pd.DataFrame()

    rows = []
    for doc in docs:
        d = doc.to_dict() or {}
        # 保險：如果沒寫進去物件編號，就用 doc id
        if "物件編號" not in d or not d["物件編號"]:
            d["物件編號"] = doc.id
        rows.append(d)

    if not rows:
        print("aiwu_rows 集合是空的")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.columns = df.columns.str.strip()

    # 委託總價 / 坪數 / 屋齡 等做數字清洗
    for col in ["委託總價", "登記坪數", "建物面積", "主建物坪", "附屬建物", "公設建坪",
                "公設比", "每坪單價", "土地登記", "總基地坪", "屋齡", "每層戶數", "電梯總數"]:
        if col in df.columns:
            df[col] = df[col].apply(clean_float)

    # 房型：從「類型/現況」抽
    if "類型/現況" in df.columns:
        df["房型"] = df["類型/現況"].astype(str).str.extract(r"^(\S+)\s*/")[0]

    # 區域：抽出「〇〇區」
    if "區域" in df.columns:
        df["區域"] = df["區域"].map(extract_area)

    # 圖片網址：如果沒有 image_url，就用網址推
    if "image_url" not in df.columns and "網址" in df.columns:
        df["image_url"] = df["網址"].apply(build_image_url)

    # 強銷欄位
    if "強銷" not in df.columns:
        df["強銷"] = "否"
    else:
        df["強銷"] = df["強銷"].fillna("否")

    # 舊版 admin_featured 用的 id，可以保留
    df["id"] = df.index

    return df

df_raw = build_df_from_firestore()

def reload_df_raw():
    global df_raw
    df_raw = build_df_from_firestore()
    print(f"🔄 df_raw 已重新載入，筆數：{len(df_raw)}")

# ========= Firestore 集合名稱 =========

VIDEOS_COLLECTION = "videos"     # IG 影片
RENT_COLLECTION = "rent_rows"    # 租屋查詢資料
featured_COLLECTION = "featured"
AIWU_COLLECTION = "aiwu_rows"
SEDM_PAGES_COLLECTION = "sedm_pages"

# ========= 影片列表 Firestore =========

def save_video(region: str, url: str):
    data = {
        "region": region,
        "url": url,
        "created_at": datetime.now(),
    }
    db.collection(VIDEOS_COLLECTION).add(data)


def read_videos():
    docs = (
        db.collection(VIDEOS_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )

    videos_by_region = {}
    for doc in docs:
        data = doc.to_dict() or {}
        region = data.get("region") or "未分類"
        url = data.get("url")
        if not url:
            continue

        videos_by_region.setdefault(region, []).append(url)

    return videos_by_region




def build_image_list_from_row(row: dict):
    imgs_field = row.get("圖片連結")
    if imgs_field:
        return [u.strip() for u in str(imgs_field).split(",") if u.strip()]

    image_url = row.get("image_url")
    if image_url:
        return expand_houseol_images(image_url)

    url = row.get("網址") or row.get("EDM連結")
    if url:
        img = build_image_url(str(url))
        if img:
            return expand_houseol_images(img)

    return []


def generate_all_sedm_pages_from_firestore():
    """
    版本 2：不再用 sale_data.db，
    改用 Firestore 的 aiwu_rows 當資料來源，
    直接把 HTML 上傳到 Firebase Storage。
    """
    print("=== 開始從 Firestore 產生 sedm 靜態頁面並上傳 Storage ===")

    docs = db.collection(AIWU_COLLECTION).stream()
    count = 0

    with app.app_context():
        for doc in docs:
            row = doc.to_dict() or {}
            house_id = str(row.get("物件編號") or doc.id)

            image_list = build_image_list_from_row(row)

            # 這裡把你原本需要的所有欄位都塞回去
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
                # 這個是剛剛 template 用來載入 logo / icon 的 base
                static_base="https://ellenfindhome.com/static"
                # 如果你現在還是用 Render 網址，就改成：
                # static_base="https://ellen-my-homie.onrender.com/static"
            )

            # ---- 上傳到 Storage ----
            blob_path = f"sedm_pages/{house_id}.html"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(html, content_type="text/html")
            blob.make_public()   # 讓任何人可以直接看

            page_url = blob.public_url

            # ---- 把對應的頁面資訊寫回 Firestore 的 sedm_pages ----
            db.collection(SEDM_PAGES_COLLECTION).document(house_id).set(
                {
                    "house_id": house_id,
                    "page_url": page_url,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )

            count += 1
            print(f"✅ {house_id}.html 已上傳：{page_url}")

    print(f"🎉 完成產出並上傳 {count} 個 sedm 靜態頁面")
    
    
# ========= 首頁（售屋列表） =========

@app.route("/", methods=["GET", "POST"])
def index():
    global df_raw

    if df_raw is None or df_raw.empty:
        # 如果 Firestore 沒資料，避免掛掉
        return render_template(
            "index.html",
            slide_images=[],
            taichung_districts=taichung_districts,
            selected_areas=[],
            房型選項=[],
            selected_types=[],
            room_min="",
            room_max="",
            price_min="",
            price_max="",
            building_min="",
            building_max="",
            keyword="",
            sort_by="屋齡",
            sort_order="asc",
            selected_has_elevator=False,
            selected_has_parking=False,
            data=[],
            total_records=0,
            page=1,
            total_pages=1,
            featured_data=[],
            age_min="",
            age_max=""
        )

    if request.method == "POST":
        selected_areas = request.form.getlist("areas")
        selected_types = request.form.getlist("types")
        room_min = request.form.get("room_min", "")
        room_max = request.form.get("room_max", "")
        price_min = request.form.get("price_min", "")
        price_max = request.form.get("price_max", "")
        building_min = request.form.get("building_min", "")
        building_max = request.form.get("building_max", "")
        keyword = request.form.get("keyword", "")
        sort_by = request.form.get("sort_by", "屋齡")
        sort_order = request.form.get("sort_order", "asc")
        selected_has_elevator = request.form.get("has_elevator") == "1"
        age_min = request.form.get("age_min", "") 
        age_max = request.form.get("age_max", "")
        selected_has_parking = request.form.get("has_parking") == "1"
        page = 1
    else:
        selected_areas = request.args.getlist("areas")
        selected_types = request.args.getlist("types")
        room_min = request.args.get("room_min", "")
        room_max = request.args.get("room_max", "")
        price_min = request.args.get("price_min", "")
        price_max = request.args.get("price_max", "")
        building_min = request.args.get("building_min", "")
        building_max = request.args.get("building_max", "")
        keyword = request.args.get("keyword", "")
        sort_by = request.args.get("sort_by", "屋齡")
        sort_order = request.args.get("sort_order", "asc")
        selected_has_elevator = request.args.get("has_elevator") == "1"
        age_min = request.args.get("age_min", "") 
        age_max = request.args.get("age_max", "")
        selected_has_parking = request.args.get("has_parking") == "1"
        page = int(request.args.get("page", 1))

    df = df_raw.copy()

    # 格局字串處理
    if "房/廳/衛" in df.columns:
        df["房/廳/衛"] = df["房/廳/衛"].apply(format_layout)
    else:
        df["房/廳/衛"] = ""

    def extract_room_num(s):
        if not isinstance(s, str):
            return None
        m = re.search(r"(\d+)房", s)
        return int(m.group(1)) if m else None

    df["房間數"] = df["房/廳/衛"].apply(extract_room_num)

    # 區域篩選
    if selected_areas and "全部" not in selected_areas:
        if "其他" in selected_areas:
            other_areas = df[~df["區域"].isin(taichung_districts)]["區域"].unique().tolist()
            filter_areas = [a for a in selected_areas if a not in ("全部", "其他")] + other_areas
            df = df[df["區域"].isin(filter_areas)]
        else:
            df = df[df["區域"].isin(selected_areas)]

    # 房型篩選
    if selected_types and "房型" in df.columns:
        df = df[df["房型"].isin(selected_types)]

    # 房間數篩選
    try:
        if room_min:
            df = df[df["房間數"] >= int(room_min)]
        if room_max:
            df = df[df["房間數"] <= int(room_max)]
    except:
        pass

    # 價格篩選（委託總價 單位：萬）
    try:
        if price_min:
            df = df[df["委託總價"] >= float(price_min)]
        if price_max:
            df = df[df["委託總價"] <= float(price_max)]
    except:
        pass

    # 主建物坪數篩選（主建物坪）
    if "主建物坪" in df.columns:
        try:
            if building_min:
                df = df[df["主建物坪"] >= float(building_min)]
            if building_max:
                df = df[df["主建物坪"] <= float(building_max)]
        except:
            pass

    # 有電梯
    if selected_has_elevator and "電梯總數" in df.columns:
        df = df[df["電梯總數"].notnull() & (df["電梯總數"].astype(str).str.strip() != "")]

    # 有車位（車位型式非空）
    if selected_has_parking and "車位型式" in df.columns:
        df = df[df["車位型式"].notnull() & (df["車位型式"].astype(str).str.strip() != "")]

    # 關鍵字搜尋
    if keyword:
        keyword_lower = keyword.strip().lower()
        search_cols = [
            "網址", "房屋標題", "區域", "委託總價",
            "鄰近市場", "鄰近學校", "生活圈",
            "社區/建物", "環境特色"
        ]
        df = df[df.apply(
            lambda row: any(
                keyword_lower in str(row[col]).lower()
                for col in search_cols if col in df.columns
            ),
            axis=1
        )]

    # 屋齡篩選（只做上限）
    try:
        if age_max and "屋齡" in df.columns:
            df = df[df["屋齡"] <= float(age_max)]
    except:
        pass

    # 排序（預設用屋齡）
    ascending = (sort_order == "asc")
    if sort_by in df.columns:
        df[sort_by] = pd.to_numeric(df[sort_by], errors='coerce')
        df = df.sort_values(by=sort_by, ascending=ascending)

    # 分頁
    per_page = 10
    total_records = len(df)
    total_pages = max(1, math.ceil(total_records / per_page))
    page = max(1, min(page, total_pages))
    page_data = (
        df.iloc[(page - 1) * per_page: page * per_page]
        .fillna("-")
        .to_dict(orient="records")
    )

    # ========= 這裡開始：從 Firestore 的 sedm_pages 拿卡片 HTML =========
    # 確保每筆都有物件編號（aiwu_rows 已經用 doc.id 補過一次了）
    for item in page_data:
        if not item.get("物件編號"):
            # df_raw 本來就有 id 欄位，保險一下
            if "id" in item and item["id"] != "-":
                item["物件編號"] = str(item["id"])

    # 逐筆去 sedm_pages 找對應的 html / card_html
    for item in page_data:
        house_id = str(item.get("物件編號") or "").strip()
        if not house_id:
            continue

        try:
            doc = db.collection(SEDM_PAGES_COLLECTION).document(house_id).get()
        except Exception as e:
            print(f"讀取 sedm_pages/{house_id} 失敗：", e)
            continue

        if not doc.exists:
            continue

        data = doc.to_dict() or {}
        # 如果你在 pipeline 有存「card_html」，就優先用；沒有就退回 html
        card_html = data.get("card_html") or data.get("html")
        if card_html:
            item["card_html"] = card_html

    房型選項 = sorted(df_raw["房型"].dropna().unique()) if ("房型" in df_raw.columns and not df_raw.empty) else []

    # 輪播圖片目前仍從本地資料夾讀
    slide_images = sorted([
        f for f in os.listdir(SLIDE_FOLDER)
        if f.lower().endswith(('.jpg', '.jpeg', '.png'))
    ]) if os.path.exists(SLIDE_FOLDER) else []

    # Ellen 強銷專區：從 featured_COLLECTION 取出強銷物件
    featured_ids = []
    try:
        docs = db.collection(featured_COLLECTION).stream()
        featured_ids = [str(doc.id) for doc in docs]
    except Exception as e:
        print("讀取 featured_COLLECTION 失敗：", e)
        featured_ids = []

    if featured_ids and not df_raw.empty:
        fdf = df_raw.copy()
        if "物件編號" not in fdf.columns:
            fdf["物件編號"] = fdf.index.astype(str)
        fdf["物件編號"] = fdf["物件編號"].astype(str)

        fdf = fdf[fdf["物件編號"].isin(featured_ids)]

        if "委託總價" in fdf.columns:
            fdf["委託總價"] = pd.to_numeric(fdf["委託總價"], errors="coerce")
            fdf = fdf.sort_values(by="委託總價", ascending=False)

        featured_data = fdf.head(8).fillna("-").to_dict(orient="records")
    else:
        featured_data = []

    return render_template(
        "index.html",
        slide_images=slide_images,
        taichung_districts=taichung_districts,
        selected_areas=selected_areas,
        房型選項=房型選項,
        selected_types=selected_types,
        room_min=room_min,
        room_max=room_max,
        price_min=price_min,
        price_max=price_max,
        building_min=building_min,
        building_max=building_max,
        keyword=keyword,
        sort_by=sort_by,
        sort_order=sort_order,
        selected_has_elevator=selected_has_elevator,
        selected_has_parking=selected_has_parking,
        data=page_data,
        total_records=total_records,
        page=page,
        total_pages=total_pages,
        featured_data=featured_data,
        age_min=age_min,
        age_max=age_max,
    )

# ========= SEO / 其他頁面 =========

@app.route('/googlec61da90b3857cf74.html')
def google_verify():
    return ('google-site-verification: googlec61da90b3857cf74.html')

@app.route("/sitemap.xml")
def sitemap():
    return send_from_directory("static", "sitemap.xml", mimetype="application/xml")

@app.route("/insights")
def insights():
    return render_template("insights.html")

@app.route('/loan', endpoint='loan')
def loan_page():
    return render_template("loan.html")

# ========= 租屋頁面 =========

@app.route('/rent')
def rent():
    selected_areas = request.args.getlist('areas')
    selected_styles = request.args.getlist('styles')
    selected_house_types = request.args.getlist('house_types')
    selected_house_forms = request.args.getlist('house_forms')
    selected_pets = request.args.getlist('pets')
    keyword = request.args.get('keyword', '').strip()
    room_min = request.args.get('room_min')
    room_max = request.args.get('room_max')
    price_min = request.args.get('price_min')
    price_max = request.args.get('price_max')
    sort_by = request.args.get('sort_by', '')

    selected_has_balcony = request.args.get('has_balcony') == '1'
    selected_has_parking = request.args.get('has_parking') == '1'
    selected_has_water_cooler = request.args.get('has_water_cooler') == '1'
    selected_has_wheelie_bin = request.args.get('has_wheelie_bin') == '1'
    selected_has_sink = request.args.get('has_sink') == '1'
    selected_has_bath_separate = request.args.get('has_bath_separate') == '1'
    selected_has_washer_indep = request.args.get('has_washer_indep') == '1'
    selected_has_short_term = request.args.get('has_short_term') == '1'
    selected_has_elevator = request.args.get('has_elevator') == '1'

    def to_int(val, default):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    room_min_val = to_int(room_min, 0)
    room_max_val = to_int(room_max, 99)
    price_min_val = to_int(price_min, 0)
    price_max_val = to_int(price_max, 9999999)

    conn = sqlite3.connect("rent_data.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM rent")
    rows = cur.fetchall()
    conn.close()

    data = []
    dist_set = set()

    for row in rows:
        dist_set.add(row['地區'])

        if selected_areas and row['地區'] not in selected_areas:
            continue
        if selected_house_forms and row['房屋形式'] not in selected_house_forms:
            continue
        if selected_house_types and row['房屋類型'] not in selected_house_types:
            continue
        if selected_pets and row['是否可寵物'] in ['不可寵']:
            continue
        if selected_has_balcony and row['陽台'] != '有':
            continue
        if selected_has_parking and '有' not in row['車位']:
            continue
        if selected_has_water_cooler and row['飲水機'] != '有':
            continue
        if selected_has_wheelie_bin and row['子母車'] != '有':
            continue
        if selected_has_sink and ('流理台' not in (row['特徵'] or '')):
            continue
        if selected_has_bath_separate and ('乾濕分離' not in (row['特徵'] or '')):
            continue
        if selected_has_washer_indep and ('獨洗' not in (row['特徵'] or '')):
            continue

        if keyword:
            if keyword not in (row['地址'] or '') and keyword not in (row['備註'] or ''):
                continue

        if selected_has_short_term and (not row['短租'] or '不可' in row['短租']):
            continue
        if selected_has_elevator and row['是否有電梯'] != '有':
            continue

        try:
            房數 = int(row['格局'].split('房')[0])
        except:
            房數 = 0

        try:
            租金 = int(row['租金'])
        except:
            租金 = 0

        if not (room_min_val <= 房數 <= room_max_val and price_min_val <= 租金 <= price_max_val):
            continue

        first_image = row['圖片連結'].split(',')[0] if row['圖片連結'] else ''

        data.append({
            'title': simplify_address(row['地址']),
            'district': row['地區'],
            'edm_link': row['EDM連結'],
            '類型': row['房屋類型'],
            '格局': row['格局'],
            '租金': 租金,
            '型式': row['房屋形式'],
            '是否可寵物': row['是否可寵物'],
            '設備': row['設備'],
            '圖片連結': first_image,
            '電費': row['電費'],
            '水費': row['水費'],
            '陽台': row['陽台'],
            '物件編號': row['物件編號']
        })

    if sort_by == 'price_asc':
        data.sort(key=lambda x: x['租金'])
    elif sort_by == 'price_desc':
        data.sort(key=lambda x: -x['租金'])
    elif sort_by == 'room_asc':
        data.sort(key=lambda x: int(x['格局'].split('房')[0]) if '房' in x['格局'] else 0)
    elif sort_by == 'room_desc':
        data.sort(key=lambda x: -int(x['格局'].split('房')[0]) if '房' in x['格局'] else 0)
    else:
        data.sort(key=lambda x: x['物件編號'], reverse=True)

    # 分頁
    page = request.args.get('page', 1, type=int)
    per_page = 9

    total_records = len(data)
    total_pages = max(1, math.ceil(total_records / per_page))

    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page
    page_data = data[start:end]

    return render_template(
        'rent.html',
        data=page_data,
        total_records=total_records,
        sort_by=sort_by,
        keyword=keyword,
        selected_areas=selected_areas,
        selected_styles=selected_styles,
        selected_house_forms=selected_house_forms,
        selected_house_types=selected_house_types,
        selected_pets=selected_pets,
        selected_has_electric=False,
        selected_has_water=False,
        selected_has_balcony=selected_has_balcony,
        selected_has_parking=selected_has_parking,
        selected_has_water_cooler=selected_has_water_cooler,
        selected_has_wheelie_bin=selected_has_wheelie_bin,
        selected_has_sink=selected_has_sink,
        selected_has_bath_separate=selected_has_bath_separate,
        selected_has_washer_indep=selected_has_washer_indep,
        room_min='' if room_min_val == 0 else room_min_val,
        room_max='' if room_max_val == 99 else room_max_val,
        price_min='' if price_min_val == 0 else price_min_val,
        price_max='' if price_max_val == 9999999 else price_max_val,
        taichung_districts=sorted(list(dist_set)),
        selected_has_short_term=selected_has_short_term,
        selected_has_elevator=selected_has_elevator,
        page=page,
        total_pages=total_pages,
    )


# =====================================================================
# 後台共用：登入檢查
# =====================================================================

def admin_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            flash("請先登入後台")
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)
    return wrapper


# =====================================================================
# 後台：試算機
# =====================================================================
@app.route('/admin/calculator', methods=['GET'])
@admin_login_required
def admin_calculator():
    return render_template('admin_calculator.html')


# =====================================================================
# 前台影片頁面
# =====================================================================
@app.route("/videos")
def videos():
    videos_grouped = read_videos()
    return render_template("videos.html", videos=videos_grouped)


# =====================================================================
# 後台登入 / 登出
# =====================================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash("密碼錯誤")
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin_login'))


# =====================================================================
# 後台主控台
# =====================================================================
@app.route('/admin')
@admin_login_required
def admin_dashboard():
    videos_grouped = read_videos()
    return render_template(
        'admin_dashboard.html',
        videos=videos_grouped,
        taichung_districts=taichung_districts,
    )


# =====================================================================
# 後台：新增影片
# =====================================================================
@app.route('/admin/add', methods=['GET', 'POST'])
@admin_login_required
def add_video():
    if request.method == 'POST':
        region = request.form['region'].strip()
        url = request.form['url'].strip()

        if region and url:
            save_video(region, url)
            flash("影片已新增")
        else:
            flash("請輸入完整地區與連結")

        return redirect(url_for('add_video'))

    videos_grouped = read_videos()
    return render_template(
        'admin_add_video.html',
        videos=videos_grouped,
        taichung_districts=taichung_districts,
    )


# =====================================================================
# 後台：刪除影片
# =====================================================================
@app.route('/admin/delete', methods=['POST'])
@admin_login_required
def delete_video():
    region = request.form.get('region')
    url = request.form.get('url')

    if not region or not url:
        flash("缺少必要參數")
        return redirect(url_for('admin_dashboard'))

    try:
        query = (
            db.collection(VIDEOS_COLLECTION)
            .where("region", "==", region)
            .where("url", "==", url)
        ).stream()

        deleted_count = 0
        for doc in query:
            doc.reference.delete()
            deleted_count += 1

        if deleted_count > 0:
            flash(f"已刪除 {deleted_count} 筆影片")
        else:
            flash("找不到該影片")

    except Exception as e:
        print("刪除影片發生錯誤：", e)
        flash("刪除失敗，請稍後再試")

    return redirect(url_for('admin_dashboard'))


# =====================================================================
# 後台：物件編號查詢（租屋 Firestore）
# =====================================================================
@app.route("/admin/query", methods=["GET", "POST"])
@admin_login_required
def admin_query():
    error = None
    result = None

    if request.method == "POST":
        house_id = (request.form.get("house_id") or "").strip()

        if not house_id:
            error = "請輸入物件編號"
        else:
            try:
                doc = db.collection(RENT_COLLECTION).document(house_id).get()
                if not doc.exists:
                    error = f"找不到物件編號：{house_id}"
                else:
                    data = doc.to_dict() or {}
                    result = {
                        "地址": data.get("地址", ""),
                        "備註": data.get("備註", ""),
                        "帶看方式": data.get("帶看方式", ""),
                        "租金": data.get("租金", ""),
                        "格局": data.get("格局", ""),
                        "房屋類型": data.get("房屋類型", ""),
                    }
            except Exception as e:
                error = f"查詢時發生錯誤：{e}"

    return render_template("admin_query.html", error=error, result=result)


def build_sedm_context(row, house_id: str):
    """
    把 aiwu_rows 裡的一筆 row，整理成 sedm.html 需要的變數
    """

    def first_nonempty(*keys):
        for k in keys:
            v = row.get(k)
            if v not in (None, "", "-"):
                return str(v)
        return ""

    # 標題
    title = first_nonempty("房屋標題")
    if not title:
        title = f"物件編號 {house_id}"

    # 總價字串
    total_price = first_nonempty("委託總價", "總價", "總價(萬)")
    if total_price and "萬" not in total_price:
        total_price = f"{total_price}萬"

    # ========= 圖片列表 =========
    image_list = []
    imgs_field = row.get("圖片連結")
    if imgs_field:
        # 如果 Firestore 已經有「圖片連結」欄位，優先用它
        image_list = [u.strip() for u in str(imgs_field).split(",") if u.strip()]

    if not image_list:
        # 沒有「圖片連結」，改用 image_url 展開 a～t
        image_url = row.get("image_url")
        if image_url:
            image_list = expand_houseol_images(image_url)
        else:
            # 再退一步：從 網址 / EDM 連結 推出一張 a.jpg，再展開
            url = row.get("網址") or row.get("EDM連結")
            if url:
                img = build_image_url(str(url))
                if img:
                    image_list = expand_houseol_images(img)

    # 以下照你現在版本：把其他欄位塞進 ctx
    ctx = {
        "title": title,
        "total_price": total_price,
        "image_list": image_list,

        # 上半部
        "layout": first_nonempty("房/廳/衛", "格局"),
        "age": first_nonempty("屋齡", "屋齡(年)", "屋齡年數"),
        "reg_area": first_nonempty("登記坪數", "建物面積", "建坪"),
        "floor_info": first_nonempty("樓別/樓高"),
        "direction": first_nonempty("物件座向"),
        "community": first_nonempty("社區/建物"),

        # 🔶 房屋資料
        "building_type": first_nonempty("類型/現況"),
        "public_ratio": first_nonempty("公設比"),
        "usage_zone": first_nonempty("使用分區"),
        "parking_type": first_nonempty("車位型式"),
        "parking_num": first_nonempty("車位/編號"),
        "status_type": first_nonempty("現況類別/謄本用途"),

        # 🔶 坪數說明
        "building_area": first_nonempty("建物面積", "建坪", "登記坪數"),
        "main_area": first_nonempty("主建物坪"),
        "sub_area": first_nonempty("附屬建物"),
        "public_area": first_nonempty("公設建坪"),
        "land_status": first_nonempty("土地登記"),
        "base_area": first_nonempty("總基地坪"),

        # 🔶 生活圈與周邊
        "circle": first_nonempty("生活圈"),
        "near_school": first_nonempty("鄰近學校"),
        "near_park": first_nonempty("鄰近公園"),
        "near_market": first_nonempty("鄰近市場"),

        # 🌿 環境特色
        "feature": first_nonempty("環境特色"),
        "featured": first_nonempty("環境特色"),

        "house_id": house_id,
        "house": row,
    }

    return ctx



# ========= featured 共用工具 =========

def get_all_featured():
    docs = db.collection(featured_COLLECTION).order_by("sort_order").stream()
    featured_list = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        featured_list.append(d)
    return featured_list


def get_featured_by_id(featured_id: str):
    doc_ref = db.collection(featured_COLLECTION).document(featured_id)
    doc = doc_ref.get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    d["id"] = doc.id
    return d


def update_featured(featured_id: str, data: dict):
    doc_ref = db.collection(featured_COLLECTION).document(featured_id)
    data["updated_at"] = datetime.utcnow()
    doc_ref.update(data)


def create_featured(data: dict):
    col_ref = db.collection(featured_COLLECTION)
    now = datetime.utcnow()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    data.setdefault("sort_order", 9999)
    doc_ref = col_ref.document()
    doc_ref.set(data)
    return doc_ref.id


def delete_featured(featured_id: str):
    db.collection(featured_COLLECTION).document(featured_id).delete()


@app.route("/admin/featured")
@admin_login_required
def admin_featured():
    global df_raw

    if df_raw is None or df_raw.empty:
        return render_template(
            "admin_featured.html",
            items=[],
            page=1,
            total_pages=1,
            per_page=12,
            total=0,
            show_only=False,
            keyword="",
        )

    page = request.args.get("page", 1, type=int)
    per_page = 12
    show_only = request.args.get("only", "0") == "1"
    keyword = request.args.get("keyword", "").strip()

    featured_ids = set()
    try:
        docs = db.collection(featured_COLLECTION).stream()
        for doc in docs:
            featured_ids.add(str(doc.id))
    except Exception as e:
        print("讀取 featured_COLLECTION 失敗：", e)

    df = df_raw.copy()

    if "物件編號" not in df.columns:
        df["物件編號"] = df.index.astype(str)

    df["物件編號"] = df["物件編號"].astype(str)
    df["is_featured"] = df["物件編號"].isin(featured_ids)

    if keyword:
        kw = keyword.lower()
        search_cols = [
            "物件編號", "房屋標題", "區域",
            "社區/建物", "地址", "生活圈"
        ]
        df = df[df.apply(
            lambda row: any(
                kw in str(row.get(col, "")).lower() for col in search_cols
            ),
            axis=1
        )]

    if show_only:
        df = df[df["is_featured"]]

    sort_col = "委託總價" if "委託總價" in df.columns else "物件編號"
    df[sort_col] = pd.to_numeric(df[sort_col], errors="coerce")
    df = df.sort_values(by=sort_col, ascending=False)

    total = len(df)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    end = start + per_page
    page_df = df.iloc[start:end].copy()

    if "image_url" not in page_df.columns:
        page_df["image_url"] = ""

    items = []
    for _, row in page_df.iterrows():
        img = row.get("image_url", "") or ""
        if (not img) and ("網址" in row and row["網址"]):
            img = build_image_url(str(row["網址"]))

        items.append({
            "id": str(row.get("物件編號", "")),
            "title": str(row.get("房屋標題", "")),
            "region": str(row.get("區域", "")),
            "price": row.get("委託總價", ""),
            "building_ping": row.get("主建物坪", ""),
            "age": row.get("屋齡", ""),
            "image_url": img or "",
            "is_featured": bool(row.get("is_featured", False)),
        })

    return render_template(
        "admin_featured.html",
        items=items,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        total=total,
        show_only=show_only,
        keyword=keyword,
    )


@app.route("/admin/toggle_featured/<item_id>", methods=["POST"])
@admin_login_required
def toggle_featured(item_id):
    ref = db.collection(featured_COLLECTION).document(item_id)
    doc = ref.get()

    if doc.exists:
        ref.delete()
        return {"status": "success", "featured": False}
    else:
        ref.set({"created_at": firestore.SERVER_TIMESTAMP})
        return {"status": "success", "featured": True}


@app.route("/admin/featured/<featured_id>", methods=["GET", "POST"])
@admin_login_required
def admin_featured_detail(featured_id):
    featured = get_featured_by_id(featured_id)
    if not featured:
        flash("找不到這筆強銷資料", "error")
        return redirect(url_for("admin_featured"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subtitle = request.form.get("subtitle", "").strip()
        region = request.form.get("region", "").strip()
        price = request.form.get("price", "").strip()
        image_url = request.form.get("image_url", "").strip()
        property_id = request.form.get("property_id", "").strip()
        sort_order = request.form.get("sort_order", "").strip()
        is_active = request.form.get("is_active") == "on"

        try:
            sort_order = int(sort_order) if sort_order else 9999
        except ValueError:
            sort_order = 9999

        try:
            price = float(price) if price else 0
        except ValueError:
            price = 0

        data = {
            "title": title,
            "subtitle": subtitle,
            "region": region,
            "price": price,
            "image_url": image_url,
            "property_id": property_id,
            "sort_order": sort_order,
            "is_active": is_active,
        }

        update_featured(featured_id, data)
        flash("已更新強銷資料", "success")
        return redirect(url_for("admin_featured_detail", featured_id=featured_id))

    return render_template("admin_featured_detail.html", featured=featured)


@app.route("/admin/featured/<featured_id>/delete", methods=["POST"])
@admin_login_required
def admin_featured_delete(featured_id):
    delete_featured(featured_id)
    flash("已刪除強銷資料", "success")
    return redirect(url_for("admin_featured"))


# ========= 預約表單 =========

def load_contacts():
    if not os.path.exists(CONTACT_FILE):
        return []
    with open(CONTACT_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_contacts(contacts):
    with open(CONTACT_FILE, 'w', encoding='utf-8') as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)

@app.route("/contact", methods=["GET", "POST"])
def contact():
    form_data = None
    success_message = None
    error_message = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not phone or not message:
            error_message = "請把姓名、電話和想諮詢內容都填寫完整喔！"
            form_data = request.form
        else:
            save_contact(name, phone, message)
            ok = send_email(name, phone, message)

            if ok:
                success_message = "已收到你的預約 / 諮詢，我會盡快與你聯繫 🙌"
                form_data = None
            else:
                error_message = "資料已送出，但寄信失敗（伺服器端有詳細錯誤訊息）。"
                form_data = request.form

    return render_template(
        "contact.html",
        form_data=form_data,
        success_message=success_message,
        error_message=error_message,
    )

@app.route('/captcha_image')
def captcha_image():
    captcha_text = str(random.randint(1000, 9999))
    session['captcha'] = captcha_text

    width, height = 100, 40
    image = Image.new('RGB', (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except:
        font = ImageFont.load_default()

    draw.text((10, 5), captcha_text, font=font, fill=(0, 0, 0))

    for _ in range(5):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line(((x1, y1), (x2, y2)), fill=(150, 150, 150), width=1)

    image = image.filter(ImageFilter.GaussianBlur(1))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png')

@app.route('/admin/contacts')
def admin_contacts():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    contacts = load_contacts()
    contacts.reverse()

    page = request.args.get('page', 1, type=int)
    per_page = 20
    total = len(contacts)
    total_pages = (total + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    contacts_page = contacts[start:end]
    unread_count = sum(1 for c in contacts if c.get('status') == 'pending')

    return render_template(
        'admin_contacts.html',
        contacts=contacts_page,
        unread_count=unread_count,
        page=page,
        total_pages=total_pages
    )

@app.route('/admin/contacts/<int:index>')
def admin_contact_detail(index):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    page = request.args.get('page', 1, type=int)
    contacts = load_contacts()
    contacts.reverse()

    if index < 0 or index >= len(contacts):
        flash("留言不存在")
        return redirect(url_for('admin_contacts', page=page))

    contact_data = contacts[index]
    return render_template('admin_contact_detail.html', contact=contact_data, index=index, page=page)

@app.route('/admin/contacts/<int:index>/delete', methods=['POST'])
def admin_contact_delete(index):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    page = request.args.get('page', 1, type=int)
    contacts = load_contacts()
    contacts.reverse()

    if index < 0 or index >= len(contacts):
        flash("留言不存在")
        return redirect(url_for('admin_contacts', page=page))

    contacts.pop(index)
    contacts.reverse()
    save_contacts(contacts)
    flash("留言已刪除")
    return redirect(url_for('admin_contacts', page=page))

@app.route('/admin/contacts/<int:index>/toggle_status', methods=['POST'])
def admin_contact_toggle_status(index):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    page = request.args.get('page', 1, type=int)
    contacts = load_contacts()
    contacts.reverse()

    if index < 0 or index >= len(contacts):
        flash("留言不存在")
        return redirect(url_for('admin_contacts', page=page))

    current_status = contacts[index].get('status', 'pending')
    contacts[index]['status'] = 'contacted' if current_status == 'pending' else 'pending'

    contacts.reverse()
    save_contacts(contacts)
    flash("狀態已更新")
    return redirect(url_for('admin_contacts', page=page))


# ========= Excel / 輪播圖上傳 =========

def allowed_file_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_file_excel(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_EXCEL

@app.route('/admin/excel', methods=['GET', 'POST'])
@admin_login_required
def admin_excel():
    excel_files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.xlsx')]

    if request.method == 'POST':
        if 'upload' in request.form:
            uploaded_file = request.files.get('file')
            if uploaded_file and allowed_file_excel(uploaded_file.filename):
                filename = secure_filename(uploaded_file.filename)
                uploaded_file.save(os.path.join(UPLOAD_FOLDER, filename))
                flash(f'{filename} 上傳成功')
            else:
                flash('只允許上傳 .xlsx 檔案')
            return redirect(url_for('admin_excel'))

    return render_template('admin_excel.html', excel_files=excel_files)

@app.route('/admin/slide', methods=['GET', 'POST'])
@admin_login_required
def admin_slide():
    slide_images = sorted([
        f for f in os.listdir(SLIDE_FOLDER)
        if allowed_file_image(f)
    ]) if os.path.exists(SLIDE_FOLDER) else []

    if request.method == 'POST':
        if 'upload' in request.form:
            uploaded_file = request.files.get('file')
            if uploaded_file and allowed_file_image(uploaded_file.filename):
                filename = secure_filename(uploaded_file.filename)

                # 1) 照舊存本機（備份）
                os.makedirs(SLIDE_FOLDER, exist_ok=True)
                uploaded_path = os.path.join(SLIDE_FOLDER, filename)
                uploaded_file.save(uploaded_path)

                # 2) 再上傳到 Storage：static/images/carousel/檔名
                try:
                    blob_path = f"static/images/carousel/{filename}"
                    blob = bucket.blob(blob_path)
                    # 重新開一次檔案上傳（避免用掉剛剛的 stream）
                    with open(uploaded_path, "rb") as f:
                        blob.upload_from_file(f)
                    # 如果你要公開給前台直接用 URL，開這行
                    # blob.make_public()
                    print(f"✅ slide 圖片已上傳到 Storage：{blob_path}")
                except Exception as e:
                    print(f"⚠ slide 圖片上傳 Storage 失敗：{e}")

                flash(f'{filename} 上傳成功')
            else:
                flash('僅限上傳圖片檔 (jpg, png, gif)')
            return redirect(url_for('admin_slide'))

        elif 'delete' in request.form:
            filename = request.form.get('filename')
            filepath = os.path.join(SLIDE_FOLDER, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                flash(f'{filename} 已刪除')
            else:
                flash(f'{filename} 不存在')
            return redirect(url_for('admin_slide'))

    return render_template('admin_slide.html', slide_images=slide_images)


# ========= 租屋 Excel 上傳 =========

@app.route('/admin/rent_upload', methods=['GET', 'POST'])
@admin_login_required
def admin_rent_upload():
    if request.method == 'POST':
        if 'excel_file' not in request.files:
            flash('沒有上傳檔案', 'danger')
            return redirect(request.url)

        file = request.files['excel_file']
        if file.filename == '':
            flash('請選擇檔案', 'warning')
            return redirect(request.url)

        if file and allowed_file_excel(file.filename):
            filename = os.path.basename(file.filename).replace('/', '_').replace('\\', '_')

            # 1) 本機備份
            os.makedirs(app.config['RENT_UPLOAD_FOLDER'], exist_ok=True)
            save_path = os.path.join(app.config['RENT_UPLOAD_FOLDER'], filename)
            file.save(save_path)

            # 2) 上傳到 Storage：data/rent/檔名
            try:
                blob_path = f"data/rent/{filename}"
                blob = bucket.blob(blob_path)
                with open(save_path, "rb") as f:
                    blob.upload_from_file(f)
                print(f"✅ 租屋 Excel 已上傳到 Storage：{blob_path}")
            except Exception as e:
                print(f"⚠ 租屋 Excel 上傳 Storage 失敗：{e}")

            flash(f'檔案「{filename}」上傳成功！', 'success')
            return redirect(url_for('admin_rent_upload'))
        else:
            flash('請上傳 xls 或 xlsx 格式的檔案', 'danger')
            return redirect(request.url)

    files = [f for f in os.listdir(app.config['RENT_UPLOAD_FOLDER']) if allowed_file_excel(f)]
    return render_template('admin_rent_upload.html', files=files)

@app.route('/admin/rent_delete/<filename>', methods=['POST'])
@admin_login_required
def admin_rent_delete(filename):
    if not allowed_file_excel(filename):
        flash('檔案格式不允許刪除', 'danger')
        return redirect(url_for('admin_rent_upload'))

    file_path = os.path.join(app.config['RENT_UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash(f'檔案「{filename}」已刪除', 'success')
    else:
        flash('檔案不存在', 'warning')

    return redirect(url_for('admin_rent_upload'))


# ========= AIWU Pipeline 三顆按鈕 =========

@app.route("/admin/aiwu_update")
@admin_login_required
def admin_aiwu_update():
    # 用 URL ?action=xxx 來決定要跑哪一段
    # action = search_aiwu / generate_html / pipeline(預設)
    action = request.args.get("action", "pipeline")

    try:
        # 1️⃣ 只抓愛屋型錄網址 + 產生 JSON（寫進 Firestore: aiwu_json）
        if action == "search_aiwu":
            url_count, txt_path = crawl_aiwu_and_save_txt(headless=True)
            json_result = generate_aiwu_json_from_txt(txt_path)

            flash(
                f"【愛屋抓取完成】型錄網址 {url_count} 筆，"
                f"已產生 JSON：{json_result['doc_id']}（共 {json_result['count']} 筆資料）",
                "success"
            )

        # 2️⃣ 只用 Firestore 最新 JSON 生成 / 更新 HTML + sedm_pages / aiwu_rows
        elif action == "generate_html":
            sync_result = sync_html_from_firestore_json()
            reload_df_raw()  # 重新載入前台用 df_raw（售屋列表）

            flash(
                "【HTML 同步完成】"
                f"新增 {sync_result['added']} 筆，"
                f"更新 {sync_result['updated']} 筆，"
                f"刪除 {sync_result['deleted']} 筆",
                "success"
            )

        # 3️⃣ 一鍵 pipeline：抓網址 + 產 JSON + 同步 HTML
        else:
            # (1) 抓型錄網址 (TXT)
            url_count, txt_path = crawl_aiwu_and_save_txt(headless=True)
            # (2) TXT → JSON → Firestore aiwu_json / chunk
            json_result = generate_aiwu_json_from_txt(txt_path)
            # (3) 用 Firestore 最新 JSON 同步 sedm_pages / aiwu_rows
            sync_result = sync_html_from_firestore_json()
            reload_df_raw()

            flash(
                "【一鍵更新完成】"
                f"型錄網址 {url_count} 筆，"
                f"JSON 資料 {json_result['count']} 筆，"
                f"新增 HTML {sync_result['added']} 筆，"
                f"更新 {sync_result['updated']} 筆，"
                f"刪除 {sync_result['deleted']} 筆",
                "success"
            )

    except Exception as e:
        flash(f"更新愛屋資料失敗：{e}", "danger")

    return redirect(url_for("admin_dashboard"))


# ================================
# 手動上傳愛屋 TXT → 自動產生 JSON
# ================================
@app.route("/admin/aiwu_manual", methods=["GET", "POST"])
@admin_login_required
def admin_aiwu_manual():
    log_text = None

    if request.method == "POST":
        action = request.form.get("action")

        # B. 按下「使用最新 chunk 重新產生 aiwu_rows / sedm_pages」
        if action == "sync_chunks":
            try:
                result = sync_html_from_firestore_json()
                reload_df_raw()
                log_lines = [
                    "使用最新 chunk 同步完成：",
                    f"新增 {result['added']} 筆",
                    f"更新 {result['updated']} 筆",
                    f"刪除 {result['deleted']} 筆",
                ]
                log_text = "\n".join(log_lines)
                flash("已使用最新 chunk 重新產生 aiwu_rows / sedm_pages", "success")
            except Exception as e:
                log_text = f"❌ 同步失敗：{e}"
                flash(f"同步失敗：{e}", "danger")

        # A. 上傳 TXT（沒有 action 或 action 為空）
        else:
            file = request.files.get("txt_file")
            if not file or file.filename == "":
                flash("請先選擇一個 TXT 檔案再上傳。", "warning")
            else:
                filename = secure_filename(file.filename)
                save_path = os.path.join(DATA_DIR, filename)
                file.save(save_path)

                try:
                    # 1) TXT → chunk (完整型錄資料YYYYMMDD / chunk_000x)
                    info = generate_aiwu_json_from_txt(save_path)
                    # 2) 用最新 chunk 同步 aiwu_rows / sedm_pages
                    sync_result = sync_html_from_firestore_json()
                    reload_df_raw()

                    log_lines = [
                        f"TXT 檔案：{filename}",
                        f"總筆數：{info['count']}，chunk 數：{info['chunks']}",
                        "--- 同步結果 ---",
                        f"新增 {sync_result['added']} 筆",
                        f"更新 {sync_result['updated']} 筆",
                        f"刪除 {sync_result['deleted']} 筆",
                    ]
                    log_text = "\n".join(log_lines)

                    flash(
                        "TXT 解析 + chunk 寫入 + 同步 aiwu_rows / sedm_pages 完成。",
                        "success"
                    )
                except Exception as e:
                    log_text = f"❌ 上傳 TXT 或同步過程失敗：{e}"
                    flash(f"處理 TXT 檔案失敗：{e}", "danger")

    # GET 或 POST 處理完回傳畫面
    return render_template("admin_aiwu_manual.html", log_text=log_text)

# =====================================================================
# 後台：獨立 Selenium 抓愛屋 + 產 JSON / chunk
# =====================================================================
@app.route("/admin/aiwu_selenium", methods=["GET", "POST"])
@admin_login_required
def admin_aiwu_selenium():
    """
    這個頁面只做兩件事（按按鈕才會跑）：
    1. 用 Selenium 登入 + 抓所有「型錄」網址，存成 data/愛屋YYYYMMDD.txt
    2. 讀剛剛那個 txt，跑 generate_aiwu_json_from_txt → 寫進 Firestore（aiwu_json 或 chunk）
    不會動到 sedm_pages / aiwu_rows（不會刪資料），只負責「準備資料」。
    """
    log_lines = []

    if request.method == "POST":
        try:
            log_lines.append("開始執行 Selenium 抓取流程 ...")

            # 1️⃣ Selenium 抓型錄網址 → 存 TXT
            url_count, txt_path = crawl_aiwu_and_save_txt(headless=False)
            # 如果你想讓它背景跑不要跳瀏覽器，可以改 headless=True

            log_lines.append(f"✅ 抓到型錄網址 {url_count} 筆")
            log_lines.append(f"✅ TXT 已儲存：{txt_path}")

            # 2️⃣ TXT → JSON / chunk → Firestore
            json_result = generate_aiwu_json_from_txt(txt_path)
            log_lines.append(
                f"✅ 產生完整型錄 JSON / chunk：{json_result['doc_id']}，"
                f"共 {json_result['count']} 筆資料"
            )

        except Exception as e:
            log_lines.append(f"❌ 執行失敗：{e}")

    # GET 或 POST 都會 render 同一頁，只是 POST 多帶 log
    return render_template(
        "admin_aiwu_selenium.html",
        log_text="\n".join(log_lines) if log_lines else None
    )

# ========= 前台：單一售屋頁（從 Firestore aiwu_rows 讀，Jinja 渲染） =========

@app.route("/house/<house_id>")
def house_page(house_id):
    doc = db.collection("sedm_pages").document(house_id).get()
    if doc.exists:
        data = doc.to_dict() or {}
        page_url = data.get("page_url")
        if page_url:
            return redirect(page_url)

    # 沒有 page_url 時，退回舊的 Jinja 渲染
    doc = db.collection(AIWU_COLLECTION).document(house_id).get()
    if not doc.exists:
        abort(404)

    row = doc.to_dict() or {}
    if not row.get("物件編號"):
        row["物件編號"] = house_id

    ctx = build_sedm_context(row, house_id)
    return render_template("sedm.html", **ctx)


# =========================================================================
# 6-bis. 從「完整型錄資料YYYYMMDD / chunk_000x」讀出最新資料（工具，暫時未在路由中用）
# =========================================================================

def _load_latest_from_chunk_collections():
    """
    掃描 Firestore 所有 collection，找出名稱以「完整型錄資料」開頭的，
    取「名字最大的那一個」（通常就是日期最大），然後把底下的
    chunk_0001 / chunk_0002 ... 的 rows[] 全部串起來。
    回傳：(collection_name, all_rows)
    """
    latest_name = None

    # 1) 找出最新的「完整型錄資料YYYYMMDD」
    for coll in db.collections():
        coll_id = coll.id
        if coll_id.startswith("完整型錄資料"):
            if (latest_name is None) or (coll_id > latest_name):
                latest_name = coll_id

    if not latest_name:
        print("⚠ 找不到任何『完整型錄資料YYYYMMDD』的集合")
        return None, None

    print(f"📚 使用 chunk 版完整型錄：{latest_name}")

    # 2) 把該集合底下所有 chunk_xxxx 的 rows 全部串起來
    all_rows = []
    for doc in db.collection(latest_name).stream():
        doc_id = doc.id
        data = doc.to_dict() or {}

        rows = data.get("rows") or []
        if not isinstance(rows, list):
            print(f"⚠ {latest_name}/{doc_id} 的 rows 不是 list，略過")
            continue

        # 防呆：只接受「每一筆都是 dict」
        clean_rows = []
        for r in rows:
            if isinstance(r, dict):
                clean_rows.append(r)
            else:
                print(f"⚠ {latest_name}/{doc_id} 中出現不是 dict 的 row，略過：{type(r)}")

        all_rows.extend(clean_rows)

    print(f"📌 總共從 {latest_name} 讀出 {len(all_rows)} 筆 rows")
    return latest_name, all_rows


@app.route('/sale_map')
def sale_map():
    return render_template('sale_map.html')


# ========= 主程式 =========

if __name__ == "__main__":
    app.run(debug=True)
