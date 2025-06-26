from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file,jsonify
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


app = Flask(__name__)
app.secret_key = "awsedfr123456"
app.config['UPLOAD_FOLDER'] = 'static/uploads'
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'data')
ALLOWED_EXTENSIONS = {'xlsx'}
SLIDE_FOLDER = os.path.join(app.static_folder, 'images', 'carousel')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif'}

# 部落格
posts = []
blog_bp.posts = posts
app.register_blueprint(blog_bp, url_prefix='/blog')

# 常數
ADMIN_PASSWORD = "0601"
DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, 'videos.csv')
CONTACT_FILE = 'contacts.json'

# 建立資料夾
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def format_layout(s):
    if not isinstance(s, str) or s.strip() == "":
        return "0"  # 空白字串顯示 0
    if "//" in s:
        return ""   # 中間連兩斜線顯示空字串

    parts = s.split('/')
    if len(parts) == 3:
        try:
            rooms = parts[0].strip()
            halls = parts[1].strip()
            baths = parts[2].strip()

            # 如果有空白，轉成0
            rooms = rooms if rooms else "0"
            halls = halls if halls else "0"
            baths = baths if baths else "0"

            return f"{rooms}房{halls}廳{baths}衛"
        except:
            return s
    return s


def load_and_format_data(filepath):
    df = pd.read_excel(filepath)

    # 格局格式化
    df["格局"] = df["房/廳/衛"].apply(format_layout)

    # 數值轉換（視需求調整欄名）
    df["屋齡"] = pd.to_numeric(df["屋齡"], errors='coerce')
    df["登記坪數"] = pd.to_numeric(df["登記坪數"], errors='coerce')
    df["土地登記"] = pd.to_numeric(df["土地登記"], errors='coerce')
    df["主建物坪"] = pd.to_numeric(df["主建物坪"], errors='coerce')
    df["委託總價"] = pd.to_numeric(df["委託總價"], errors='coerce')

    # 保留要用到的欄位並重命名方便前端
    data = df.rename(columns={
        "圖片": "image_url",
        "區域": "區域",
        "類型/現況": "房型",
        "委託總價": "委託總價",
        "房/廳/衛": "房廳衛",
        "屋齡": "屋齡",
        "登記坪數": "登記坪數",
        "土地登記": "土地坪數",
        "主建物坪": "主附坪",
        "地址": "物件連結",
        "連結": "網址"
    })

    # 轉成 dict list，方便傳給前端
    data_list = data.to_dict(orient="records")
    return data_list

# 圖片網址組合函數
def build_image_url(link):
    try:
        no_match = re.search(r'No=([A-Z0-9]+)', link)
        aid_match = re.search(r'AID=([A-Z0-9]+)', link)
        if no_match and aid_match:
            no = no_match.group(1)
            aid = aid_match.group(1)
            return f"https://hq.houseol.com.tw/images/pictures/{aid}{no}a.jpg"
    except:
        return None
    return None

# 讀取所有 Excel，合併 df_raw（請依你需求調整）
all_files = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(('.xls', '.xlsx'))]
dfs = []
for f in all_files:
    df = pd.read_excel(f)
    df["來源檔案"] = os.path.basename(f)
    dfs.append(df)
df_raw = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
df_raw["id"] = df_raw.index  # 確保有 id 欄位
if "強銷" not in df_raw.columns:
    df_raw["強銷"] = "否"
    

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

def load_all_excels():
    global df_raw
    all_files = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(('.xls', '.xlsx'))]
    dfs = []
    for f in all_files:
        df = pd.read_excel(f)
        df.columns = df.columns.str.strip()  # 去除欄位名稱空白

        # 確保有強銷欄位，並填補缺失
        if "強銷" not in df.columns:
            df["強銷"] = "否"
        else:
            df["強銷"] = df["強銷"].fillna("否")

        # 標記來源檔案
        df["來源檔案"] = os.path.basename(f)
        dfs.append(df)

    if dfs:
        df_raw = pd.concat(dfs, ignore_index=True)
        df_raw.columns = df_raw.columns.str.strip()
    else:
        df_raw = pd.DataFrame()

    # 加 id 欄位
    df_raw["id"] = df_raw.index

    if not df_raw.empty:
        # 處理委託總價（移除「萬」並換成整數）
        if "委託總價" in df_raw.columns:
            df_raw["委託總價"] = df_raw["委託總價"].apply(clean_price)

        # 其他數字欄位處理
        float_cols = ["登記坪數", "建物面積", "主建物坪", "附屬建物", "公設建坪", "公設比",
                      "每坪單價", "土地登記", "總基地坪", "屋　　齡", "每層戶數", "電梯總數"]
        for col in float_cols:
            if col in df_raw.columns:
                df_raw[col] = df_raw[col].apply(clean_float)

        # 房型抽取（從「類型/現況」欄位）
        if "類型/現況" in df_raw.columns:
            df_raw["房型"] = df_raw["類型/現況"].astype(str).str.extract(r"^(\S+)\s*/")[0]

        # 區域抽取
        if "區域" in df_raw.columns:
            df_raw["區域"] = df_raw["區域"].map(extract_area)

        # 產生圖片網址
        if "網址" in df_raw.columns:
            df_raw["image_url"] = df_raw["網址"].apply(build_image_url)

    else:
        df_raw = pd.DataFrame()


# 你切換強銷時，需要找到對應檔案並存回去，示意
def save_df_to_excel(df, filename):
    df.to_excel(os.path.join(DATA_DIR, filename), index=False)

taichung_districts = [
    "中區", "東區", "南區", "西區", "北區", "西屯區", "南屯區", "北屯區",
    "豐原區", "石岡區", "東勢區", "和平區", "新社區", "潭子區", "大雅區",
    "神岡區", "大肚區", "沙鹿區", "龍井區", "梧棲區", "清水區", "大甲區",
    "外埔區", "大安區"
]

def extract_area(addr):
    if not isinstance(addr, str):
        return None
    m = re.search(r"(\S+區)", addr)
    return m.group(1) if m else None

def clean_float(val):
    try:
        return float(str(val).replace(",", "").replace("萬", "").replace("坪", "").replace("年", ""))
    except:
        return None

if not df_raw.empty:
    df_raw["區域"] = df_raw["區域"].map(extract_area)
    df_raw["房型"] = df_raw["類型/現況"].astype(str).str.extract(r"^(\S+)\s*/")[0]
    for col in ["委託總價", "登記坪數", "土地登記", "主建物坪", "屋齡"]:
        df_raw[col] = df_raw[col].apply(clean_float)
    df_raw["image_url"] = df_raw["網址"].apply(build_image_url)
else:
    df_raw = pd.DataFrame()

# ** 新增這段確保「強銷」欄位存在且填補缺失 **
if df_raw.empty:
    df_raw["強銷"] = pd.Series(dtype=str)
elif "強銷" not in df_raw.columns:
    df_raw["強銷"] = "否"
else:
    df_raw["強銷"] = df_raw["強銷"].fillna("否")

def read_videos():
    if not os.path.exists(CSV_FILE):
        return {}
    data = {}
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        reader = list(csv.reader(f))
        reader.reverse()
        for row in reader:
            if len(row) < 2:
                continue
            region, url = row
            data.setdefault(region, []).append(url)
    return data

def save_video(region, url):
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([region, url])

@app.route("/", methods=["GET", "POST"])
def index():
    # POST 請求時，從表單取得篩選條件
    if request.method == "POST":
        df_raw["房/廳/衛"] = df_raw["房/廳/衛"].apply(format_layout)

        selected_areas = request.form.getlist("areas")
        selected_types = request.form.getlist("types")
        room_min = request.form.get("room_min", "")
        room_max = request.form.get("room_max", "")
        price_min = request.form.get("price_min", "")
        price_max = request.form.get("price_max", "")
        keyword = request.form.get("keyword", "")
        sort_by = request.form.get("sort_by", "委託總價")
        sort_order = request.form.get("sort_order", "asc")
        page = 1
    else:
        # GET 請求時，從 URL query string 取得篩選條件
        selected_areas = request.args.getlist("areas")
        selected_types = request.args.getlist("types")
        room_min = request.args.get("room_min", "")
        room_max = request.args.get("room_max", "")
        price_min = request.args.get("price_min", "")
        price_max = request.args.get("price_max", "")
        keyword = request.args.get("keyword", "")
        sort_by = request.args.get("sort_by", "委託總價")
        sort_order = request.args.get("sort_order", "asc")
        page = int(request.args.get("page", 1))

    per_page = 10

    # 取得輪播圖片列表
    slide_images = sorted([
        f for f in os.listdir(SLIDE_FOLDER)
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))
    ])

    # 強銷物件篩選與整理
    df_raw["強銷"] = df_raw.get("強銷", "否").fillna("否")
    featured_df = df_raw[df_raw["強銷"] == "是"]
    featured_df["房廳衛"] = featured_df["房/廳/衛"].apply(format_layout)
    featured_data = featured_df.head(8).fillna("-").to_dict(orient="records")

    df = df_raw.copy()
    df["房/廳/衛"] = df["房/廳/衛"].apply(format_layout)
    df['房廳衛'] = df['房/廳/衛'].apply(format_layout)

    # 房間數從房/廳/衛欄位抽取（例：3房2廳2衛 -> 3）
    if "房/廳/衛" in df.columns:
        def extract_room_num(s):
            if not isinstance(s, str):
                return None
            m = re.search(r'(\d+)房', s)
            return int(m.group(1)) if m else None
        df["房間數"] = df["房/廳/衛"].apply(extract_room_num)
    else:
        df["房間數"] = None

    # 篩選區域
    if selected_areas and "全部" not in selected_areas:
        if "其他" in selected_areas:
            other_areas = df[~df["區域"].isin(taichung_districts)]["區域"].unique().tolist()
            filter_areas = [a for a in selected_areas if a not in ("全部", "其他")] + other_areas
            df = df[df["區域"].isin(filter_areas)]
        else:
            df = df[df["區域"].isin(selected_areas)]

    # 篩選房型
    if selected_types:
        df = df[df["房型"].isin(selected_types)]

    # 篩選房間數
    try:
        rmin = float(room_min) if room_min else None
        rmax = float(room_max) if room_max else None
        if rmin is not None:
            df = df[df["房間數"] >= rmin]
        if rmax is not None:
            df = df[df["房間數"] <= rmax]
    except:
        pass

    # 篩選價格
    try:
        pmin = float(price_min) if price_min else None
        pmax = float(price_max) if price_max else None
        if pmin is not None:
            df = df[df["委託總價"] >= pmin]
        if pmax is not None:
            df = df[df["委託總價"] <= pmax]
    except:
        pass

    # 關鍵字篩選，多欄位搜尋
    search_cols = [
        "網址", "房屋標題", "區域", "委託總價",
        "鄰近市場", "鄰近學校", "生活圈",
        "社區/建物", "環境特色"
    ]

    if keyword.strip():
        keyword_lower = keyword.strip().lower()

        def row_contains_keyword(row):
            for col in search_cols:
                if col in df.columns:
                    if keyword_lower in str(row[col]).lower():
                        return True
            return False

        df = df[df.apply(row_contains_keyword, axis=1)]

    total_records = len(df)

    # 排序
    ascending = sort_order == "asc"
    if sort_by in df.columns:
        if df[sort_by].dtype != 'O':  # 非字串欄位轉為數字
            df[sort_by] = pd.to_numeric(df[sort_by], errors='coerce')
        df = df.sort_values(by=sort_by, ascending=ascending)
    else:
        df["委託總價"] = pd.to_numeric(df["委託總價"], errors='coerce')
        df = df.sort_values(by="委託總價", ascending=ascending)

    # 分頁
    total_pages = math.ceil(total_records / per_page) if per_page else 1
    page = max(1, min(page, total_pages))
    page_data = df.iloc[(page - 1) * per_page: page * per_page].fillna("-").to_dict(orient="records")

    房型選項 = sorted(df_raw["房型"].dropna().unique()) if not df_raw.empty else []

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
        keyword=keyword,
        sort_by=sort_by,
        sort_order=sort_order,
        data=page_data,
        total_records=total_records,
        page=page,
        total_pages=total_pages,
        featured_data=featured_data
    )

    

@app.route("/insights")
def insights():
    return render_template("insights.html")

@app.route("/report")
def report():
    return render_template("report.html")

@app.route("/videos")
def videos():
    videos = read_videos()
    return render_template("videos.html", videos=videos)

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

@app.route('/admin')
def admin_dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))
    videos = read_videos()
    return render_template('admin_dashboard.html', videos=videos)

@app.route('/admin/add', methods=['GET', 'POST'])
def add_video():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        region = request.form['region']
        url = request.form['url']
        save_video(region, url)
        return redirect(url_for('add_video'))
    videos = read_videos()
    return render_template('admin_add_video.html', videos=videos, taichung_districts=taichung_districts)

@app.route('/admin/delete', methods=['POST'])
def delete_video():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))
    region = request.form.get('region')
    url = request.form.get('url')
    videos = read_videos()
    if region in videos and url in videos[region]:
        videos[region].remove(url)
        if not videos[region]:
            del videos[region]
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            for r, urls in videos.items():
                for u in urls:
                    writer.writerow([r, u])
        flash("刪除成功")
    else:
        flash("找不到該影片")
    return redirect(url_for('admin_dashboard'))

# 預約表單
def load_contacts():
    if not os.path.exists(CONTACT_FILE):
        return []
    with open(CONTACT_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_contacts(contacts):
    with open(CONTACT_FILE, 'w', encoding='utf-8') as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    error_message = None
    success_message = None

    if request.method == 'POST':
        # 驗證碼檢查
        user_captcha = request.form.get('captcha_input', '')
        if user_captcha != str(session.get('captcha')):
            error_message = "驗證碼錯誤，請重新輸入"
        else:
            contacts = load_contacts()
            new_contact = {
                "name": request.form.get('name', ''),
                "phone": request.form.get('phone', ''),
                "message": request.form.get('message', ''),
                "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "status": "pending"  # 預設狀態
            }
            contacts.append(new_contact)
            save_contacts(contacts)
            success_message = "預約成功，感謝您的聯繫！"

    # 產生新驗證碼
    captcha = random.randint(1000, 9999)
    session['captcha'] = captcha

    return render_template(
        'contact.html',
        captcha=captcha,
        error_message=error_message,
        success_message=success_message,
        form_data=request.form if request.method == 'POST' else None
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

# 讀取訊息
def load_contacts():
    if not os.path.exists(CONTACT_FILE):
        return []
    with open(CONTACT_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# 寫入訊息
def save_contacts(contacts):
    with open(CONTACT_FILE, 'w', encoding='utf-8') as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)

# 後台查看所有預約訊息（需登入）
@app.route('/admin/contacts')
def admin_contacts():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    contacts = load_contacts()
    contacts.reverse()  # 最新留言排前面

    # 分頁設定
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



# 查看單一留言詳細內容
@app.route('/admin/contacts/<int:index>')
def admin_contact_detail(index):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    page = request.args.get('page', 1, type=int)

    contacts = load_contacts()
    contacts.reverse()  # 先倒序，跟列表順序一致

    if index < 0 or index >= len(contacts):
        flash("留言不存在")
        return redirect(url_for('admin_contacts', page=page))

    contact = contacts[index]
    return render_template('admin_contact_detail.html', contact=contact, index=index, page=page)


# 刪除留言
@app.route('/admin/contacts/<int:index>/delete', methods=['POST'])
def admin_contact_delete(index):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    page = request.args.get('page', 1, type=int)

    contacts = load_contacts()
    contacts.reverse()  # 反轉，跟列表同樣順序

    if index < 0 or index >= len(contacts):
        flash("留言不存在")
        return redirect(url_for('admin_contacts', page=page))

    contacts.pop(index)
    contacts.reverse()  # 存檔前再反轉回原順序
    save_contacts(contacts)
    flash("留言已刪除")
    return redirect(url_for('admin_contacts', page=page))

# 更新狀態（切換 已聯繫 / 待聯繫）
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
    flash(f"狀態已更新為 {'已聯繫' if contacts[index]['status']=='contacted' else '待聯繫'}")
    return redirect(url_for('admin_contacts', page=page))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/admin/excel', methods=['GET', 'POST'])
def admin_excel():
    excel_files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.xlsx')]

    if request.method == 'POST':
        if 'upload' in request.form:
            uploaded_file = request.files.get('file')
            if uploaded_file and allowed_file(uploaded_file.filename):
                filename = secure_filename(uploaded_file.filename)
                uploaded_file.save(os.path.join(UPLOAD_FOLDER, filename))
                flash(f'{filename} 上傳成功')
            else:
                flash('只允許上傳 .xlsx 檔案')
            return redirect(url_for('admin_excel'))

        elif 'delete' in request.form:
            filename = request.form.get('filename')
            filepath = os.path.join(SLIDE_FOLDER, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                flash(f'{filename} 已刪除')
            else:
                flash(f'{filename} 不存在')
            return redirect(url_for('admin_slide'))

    return render_template('admin_excel.html', excel_files=excel_files)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/admin/slide', methods=['GET', 'POST'])
def admin_slide():
    slide_images = sorted([
        f for f in os.listdir(SLIDE_FOLDER)
        if allowed_file(f)
    ])

    if request.method == 'POST':
        if 'upload' in request.form:
            uploaded_file = request.files.get('file')
            if uploaded_file and allowed_file(uploaded_file.filename):
                filename = secure_filename(uploaded_file.filename)
                uploaded_path = os.path.join(SLIDE_FOLDER, filename)
                uploaded_file.save(uploaded_path)
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


@app.route('/admin/featured')
def admin_featured():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    keyword = request.args.get('keyword', '').strip()
    only_featured = request.args.get('only_featured') == '1'

    df = df_raw.copy()
    df["房/廳/衛"] = df["房/廳/衛"].apply(format_layout)
    df['房廳衛'] = df['房/廳/衛'].apply(format_layout)

    search_cols = [
        "房屋標題", "區域", "委託總價",
        "鄰近市場", "鄰近學校", "生活圈",
        "社區/建物", "環境特色"
    ]

    # 關鍵字篩選
    if keyword:
        keyword_lower = keyword.lower()

        def row_contains_keyword(row):
            for col in search_cols:
                if col in df.columns:
                    if keyword_lower in str(row[col]).lower():
                        return True
            return False

        df = df[df.apply(row_contains_keyword, axis=1)]

    # 強銷篩選
    if "強銷" not in df.columns:
        df["強銷"] = "否"
    df["強銷"] = df["強銷"].fillna("否")

    if only_featured:
        df = df[df["強銷"] == "是"]

    # 確保有 ID 欄
    if "id" not in df.columns:
        df["id"] = df.index

    data = df.fillna("-").to_dict(orient='records')

    return render_template("admin_featured.html", data=data, keyword=keyword, only_featured=only_featured)


@app.route('/admin/toggle_featured/<int:item_id>', methods=['POST'])
def toggle_featured(item_id):
    global df_raw
    # 找出該筆資料
    match = df_raw[df_raw["id"] == item_id]
    if match.empty:
        return jsonify({"status": "error", "message": "物件不存在"}), 404
    current = match.iloc[0]["強銷"]
    new_value = "否" if current == "是" else "是"
    df_raw.loc[df_raw["id"] == item_id, "強銷"] = new_value
    source_file = match.iloc[0]["來源檔案"]
    try:
        # 只寫回這個檔案，不是整個 df_raw
        df_to_save = df_raw[df_raw["來源檔案"] == source_file].copy()
        df_to_save.to_excel(os.path.join(DATA_DIR, source_file), index=False)
    except Exception as e:
        return jsonify({"status": "error", "message": f"儲存失敗: {str(e)}"})
    # 重新讀取最新資料避免重複累積
    load_all_excels()
    return jsonify({"status": "success", "new_value": new_value})


@app.route("/admin/featured/<int:item_id>")
def admin_featured_detail(item_id):
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    if item_id < 0 or item_id >= len(df_raw):
        flash("物件不存在")
        return redirect(url_for('admin_featured'))

    item = df_raw.iloc[item_id].fillna("-").to_dict()
    return render_template("admin_featured_detail.html", item=item)


if __name__ == "__main__":
    app.run(debug=True)

app.run(host="0.0.0.0", port=5000, debug=True)
