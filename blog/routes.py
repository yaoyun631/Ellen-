from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, current_app
)
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import json
import time
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials, firestore

# =========================
#  Firebase / Firestore 初始化
# =========================

# 初始化 Firebase App（只初始化一次）
if not firebase_admin._apps:
    cred = None

    # ① 優先讀取環境變數（給 Render 用）
    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if cred_json:
        cred = credentials.Certificate(json.loads(cred_json))
    else:
        # ② 本機開發：讀取專案裡的 serviceAccountKey.json
        if os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")

    if not cred:
        raise RuntimeError("找不到 Firestore 憑證，請確認 FIREBASE_CREDENTIALS 或 serviceAccountKey.json")

    firebase_admin.initialize_app(cred)

# 全域 Firestore client
db = firestore.client()

# Firestore collection 名稱
POSTS_COLLECTION = "posts"

blog_bp = Blueprint('blog', __name__, template_folder='templates')

# 只剩 folders 仍用 json 存本機
FOLDERS_FILE = 'folders.json'


# =========================
#  權限檢查
# =========================

def login_required():
    if not session.get('logged_in'):
        flash("請先登入後台")
        return redirect(url_for('admin_login'))
    return None


# =========================
#  Firestore 文章存取工具
# =========================

def _doc_to_post(doc):
    """把 Firestore Document 轉成 dict，並補上 id 欄位"""
    data = doc.to_dict() or {}
    data["id"] = doc.id
    # 保險：確保 created_at 會有值
    if "created_at" not in data:
        data["created_at"] = datetime.now()
    return data


def get_all_posts():
    """取得所有文章（依 created_at 由新到舊排序）"""
    docs = (
        db.collection(POSTS_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )
    return [_doc_to_post(d) for d in docs]


def get_post(post_id: str):
    """取得單一文章"""
    ref = db.collection(POSTS_COLLECTION).document(post_id)
    doc = ref.get()
    if not doc.exists:
        return None
    return _doc_to_post(doc)


def create_post(title, content, image_filename, folder):
    """新增文章"""
    ref = db.collection(POSTS_COLLECTION).document()  # 自動產生 id
    now = datetime.now()
    data = {
        "title": title,
        "content": content,
        "image": image_filename or "",
        "folder": folder or "未分類",
        "created_at": now,
        "updated_at": now,
    }
    ref.set(data)
    return ref.id


def update_post(post_id, title=None, content=None, image_filename=None, folder=None, delete_image=False):
    """更新文章"""
    update_data = {"updated_at": datetime.now()}

    if title is not None:
        update_data["title"] = title
    if content is not None:
        update_data["content"] = content
    if folder is not None:
        update_data["folder"] = folder

    if delete_image:
        update_data["image"] = ""
    elif image_filename is not None:
        update_data["image"] = image_filename

    db.collection(POSTS_COLLECTION).document(post_id).update(update_data)


def delete_post_firestore(post_id):
    """刪除文章"""
    db.collection(POSTS_COLLECTION).document(post_id).delete()


# =========================
#  後台：文章列表
# =========================

@blog_bp.route('/admin/blog')
def admin_blog():
    login = login_required()
    if login:
        return login

    posts = get_all_posts()
    return render_template('admin/admin_blog.html', posts=posts)


# =========================
#  後台：新增文章
# =========================

@blog_bp.route('/admin/blog/new', methods=['GET', 'POST'])
def new_post():
    login = login_required()
    if login:
        return login

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        image = request.files.get('image')
        folder = request.form.get('folder', '未分類')

        if not title or not content:
            flash("標題與內容不能空白")
            folders = load_folders()
            return render_template('admin/new_post.html', folders=folders)

        # 處理圖片上傳
        filename = ""
        if image and image.filename:
            filename = secure_filename(image.filename)
            upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            image.save(os.path.join(upload_folder, filename))

        # 存到 Firestore
        create_post(title, content, filename, folder)

        flash("文章新增成功")
        return redirect(url_for('blog.admin_blog'))

    folders = load_folders()
    return render_template('admin/new_post.html', folders=folders)


# =========================
#  後台：編輯文章
# =========================

@blog_bp.route('/admin/blog/edit/<post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    """
    注意：這裡把 <int:post_id> 改成 <post_id>，
    因為 Firestore 的 doc id 是字串。
    """
    login = login_required()
    if login:
        return login

    post = get_post(post_id)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.admin_blog'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        folder = request.form.get('folder', '未分類')
        image = request.files.get('image')
        delete_image_flag = request.form.get('delete_image') == 'on'

        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
        os.makedirs(upload_folder, exist_ok=True)

        new_filename = None

        # 刪除舊圖片
        if delete_image_flag and post.get('image'):
            old_path = os.path.join(upload_folder, post['image'])
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
            new_filename = ""  # Firestore 裡會被設成空字串

        # 上傳新圖片
        if image and image.filename:
            filename = secure_filename(image.filename)
            image.save(os.path.join(upload_folder, filename))
            new_filename = filename

        update_post(
            post_id,
            title=title or post.get("title"),
            content=content or post.get("content"),
            folder=folder,
            image_filename=new_filename,
            delete_image=delete_image_flag
        )

        flash("文章已更新")
        return redirect(url_for('blog.admin_blog'))

    folders = load_folders()
    return render_template('admin/edit_post.html', post=post, folders=folders)


# =========================
#  後台：刪除文章
# =========================

@blog_bp.route('/admin/blog/delete/<post_id>', methods=['POST'])
def delete_post(post_id):
    login = login_required()
    if login:
        return login

    # 先抓出來刪圖片
    post = get_post(post_id)
    if post and post.get("image"):
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
        image_path = os.path.join(upload_folder, post['image'])
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception:
                pass

    delete_post_firestore(post_id)
    flash("文章已刪除")
    return redirect(url_for('blog.admin_blog'))


# =========================
#  CKEditor 圖片上傳（維持原本邏輯）
# =========================

@blog_bp.route('/admin/blog/upload-image', methods=['POST'])
def upload_image():
    if not session.get('logged_in'):
        return {'error': 'Unauthorized'}, 401

    image = request.files.get('upload')
    if not image:
        return {'error': 'No file'}, 400

    filename = secure_filename(image.filename)
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
    os.makedirs(upload_folder, exist_ok=True)
    image.save(os.path.join(upload_folder, filename))

    url = url_for('static', filename='uploads/' + filename)
    return {"uploaded": True, "url": url}


# =========================
#  前台：單篇文章頁面
# =========================

@blog_bp.route('/post/<post_id>')
def show_post(post_id):
    post = get_post(post_id)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.index'))
    return render_template('blog/show_post.html', post=post)


@blog_bp.route('/post/<post_id>/detail')
def view_post(post_id):
    post = get_post(post_id)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.index'))
    return render_template('blog/post_detail.html', post=post)


# =========================
#  前台：Blog 首頁（含 folder & profile）
# =========================

@blog_bp.route("/blog")
def index():
    folder = request.args.get("folder", "")  # ?folder=分類名稱
    all_posts = get_all_posts()
    folders = load_folders()

    # 依照選擇的分類過濾文章
    if folder:
        filtered_posts = [p for p in all_posts if p.get("folder") == folder]
    else:
        filtered_posts = all_posts

    # 🔹 讀取 profile.json
    base_dir = current_app.root_path
    data_dir = os.path.join(base_dir, "data")
    profile_path = os.path.join(data_dir, "profile.json")

    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
    else:
        # 沒有檔案時的預設值
        profile = {
            "avatar_filename": None,
            "avatar_pos_x": 0,
            "avatar_pos_y": 0,
            "avatar_zoom": 1.0,
            "title": "Ellen 的奶茶房產筆記本",
            "subtitle": "記錄海線房產、租屋大小事、投資理財心情、與每天的創業生活。",
            "tags": ["海線房仲", "房產知識", "投資理財", "加拿大打工渡假"],
            "about_title": "關於 Ellen",
            "about_text": "太平洋房屋｜海線房仲\n喜歡用故事、影片和文字，陪你一起找到適合的家。"
        }

    # 🔹 大頭貼路徑
    if profile.get("avatar_filename"):
        avatar_url_path = f"images/blog/{profile['avatar_filename']}"
    else:
        avatar_url_path = "images/blog/default_avatar.png"

    return render_template(
        "blog/index.html",
        posts=filtered_posts,
        folders=folders,
        current_folder=folder,
        profile=profile,
        avatar_url_path=avatar_url_path,
    )


# =========================
#  分類（仍用 JSON 本機）
# =========================

def load_folders():
    if os.path.exists(FOLDERS_FILE):
        with open(FOLDERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_folders(folders):
    with open(FOLDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(folders, f, ensure_ascii=False, indent=2)


@blog_bp.route('/admin/folders', methods=['GET'])
def folder_manager():
    login = login_required()
    if login:
        return login
    folders = load_folders()
    return render_template('admin/folder_manager.html', folders=folders)


@blog_bp.route('/admin/folders/add', methods=['POST'])
def add_folder():
    login = login_required()
    if login:
        return login
    folder = request.form.get('folder', '').strip()
    if folder:
        folders = load_folders()
        if folder not in folders:
            folders.append(folder)
            save_folders(folders)
    return redirect(url_for('blog.folder_manager'))


@blog_bp.route('/admin/folders/delete/<folder_name>', methods=['POST'])
def delete_folder(folder_name):
    login = login_required()
    if login:
        return login
    folders = load_folders()
    if folder_name in folders:
        folders.remove(folder_name)
        save_folders(folders)
    return redirect(url_for('blog.folder_manager'))


@blog_bp.route('/admin/folders/move-up/<folder_name>', methods=['POST'])
def move_folder_up(folder_name):
    login = login_required()
    if login:
        return login
    folders = load_folders()
    if folder_name in folders:
        index = folders.index(folder_name)
        if index > 0:
            folders[index], folders[index - 1] = folders[index - 1], folders[index]
            save_folders(folders)
    return redirect(url_for('blog.folder_manager'))


@blog_bp.route('/admin/folders/move-down/<folder_name>', methods=['POST'])
def move_folder_down(folder_name):
    login = login_required()
    if login:
        return login
    folders = load_folders()
    if folder_name in folders:
        index = folders.index(folder_name)
        if index < len(folders) - 1:
            folders[index], folders[index + 1] = folders[index + 1], folders[index]
            save_folders(folders)
    return redirect(url_for('blog.folder_manager'))


# =========================
#  Blog Profile（沿用原本邏輯）
# =========================

@blog_bp.route("/blog/profile", methods=["GET", "POST"])
def blog_profile():
    # === 準備路徑 ===
    base_dir = current_app.root_path               # 專案根目錄
    data_dir = os.path.join(base_dir, "data")      # 存 profile.json 的資料夾
    os.makedirs(data_dir, exist_ok=True)

    profile_path = os.path.join(data_dir, "profile.json")

    # 存大頭貼的資料夾：static/images/blog
    blog_img_dir = os.path.join(current_app.static_folder, "images", "blog")
    os.makedirs(blog_img_dir, exist_ok=True)

    # === 預設值（沒有 profile.json 時用這組） ===
    default_profile = {
        "avatar_filename": None,
        "avatar_pos_x": 0,
        "avatar_pos_y": 0,
        "avatar_zoom": 1.0,
        "title": "Ellen 的奶茶房產筆記本",
        "subtitle": "記錄海線房產、租屋大小事、投資理財心情、與每天的創業生活。",
        "tags": ["海線房仲", "房產知識", "投資理財", "加拿大打工渡假"],
        "about_title": "關於 Ellen",
        "about_text": "太平洋房屋｜海線房仲\n喜歡用故事、影片和文字，陪你一起找到適合的家。"
    }

    # === 先讀舊的 profile.json，如果沒有就用預設 ===
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except Exception:
            profile = default_profile.copy()
    else:
        profile = default_profile.copy()

    # === POST：使用者按下儲存 ===
    if request.method == "POST":
        # ---- 1) 處理大頭貼上傳 ----
        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            # 檔名：avatar_隨機ID.副檔名
            ext = os.path.splitext(secure_filename(avatar_file.filename))[1] or ".jpg"
            filename = f"avatar_{uuid4().hex}{ext}"
            save_path = os.path.join(blog_img_dir, filename)
            avatar_file.save(save_path)
            profile["avatar_filename"] = filename

        # ---- 2) 位置 / 縮放（從隱藏欄位來） ----
        def to_float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        pos_x = to_float(request.form.get("avatar_pos_x"), 0.0)
        pos_y = to_float(request.form.get("avatar_pos_y"), 0.0)
        zoom  = to_float(request.form.get("avatar_zoom"), 1.0)

        profile["avatar_pos_x"] = pos_x
        profile["avatar_pos_y"] = pos_y
        profile["avatar_zoom"]  = zoom

        # ---- 3) 標題 / 副標題 / 關於 ----
        title = (request.form.get("title") or "").strip()
        subtitle = (request.form.get("subtitle") or "").strip()
        about_title = (request.form.get("about_title") or "").strip()
        about_text = (request.form.get("about_text") or "").strip()

        profile["title"] = title or default_profile["title"]
        profile["subtitle"] = subtitle or default_profile["subtitle"]
        profile["about_title"] = about_title or default_profile["about_title"]
        profile["about_text"] = about_text or default_profile["about_text"]

        # ---- 4) 標籤：tags_raw -> list 存進 JSON ----
        tags_raw = (request.form.get("tags_raw") or "").strip()
        if tags_raw:
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            tags = []
        profile["tags"] = tags

        # 額外記錄更新時間（可選）
        profile["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # === 5) 寫回 profile.json（整包覆蓋） ===
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)

        flash("已更新部落格個人資料，首頁已套用最新設定。", "success")
        return redirect(url_for("blog.index"))

    # === GET：顯示編輯畫面 ===
    avatar_filename = profile.get("avatar_filename")
    if avatar_filename:
        avatar_url_path = f"images/blog/{avatar_filename}"
    else:
        avatar_url_path = "images/blog/default_avatar.png"

    if isinstance(profile.get("tags"), str):
        profile["tags"] = [t.strip() for t in profile["tags"].split(",") if t.strip()]

    return render_template(
        "admin/blog_profile.html",
        profile=profile,
        avatar_url_path=avatar_url_path,
    )
