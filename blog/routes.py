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

if not firebase_admin._apps:
    cred = None

    # ① Render / 伺服器環境：用環境變數 FIREBASE_CREDENTIALS
    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if cred_json:
        try:
            cred = credentials.Certificate(json.loads(cred_json))
        except Exception as e:
            print("載入 FIREBASE_CREDENTIALS 失敗：", e)
            cred = None

    # ② 本機開發：讀取 serviceAccountKey.json
    if cred is None and os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")

    if not cred:
        raise RuntimeError("找不到 Firestore 憑證，請確認 FIREBASE_CREDENTIALS 或 serviceAccountKey.json")

    firebase_admin.initialize_app(cred)

# 全域 Firestore client
db = firestore.client()

# Firestore collection 名稱
POSTS_COLLECTION = "posts"
PROFILE_COLLECTION = "blog_profile"
PROFILE_DOC_ID = "main"      # 固定只用一筆 profile

blog_bp = Blueprint('blog', __name__, template_folder='templates')

# 分類資料仍用 json 存本機
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
#  Firestore：文章工具
# =========================

def _doc_to_post(doc):
    """把 Firestore Document 轉成 dict，並補上 id 欄位"""
    data = doc.to_dict() or {}
    data["id"] = doc.id
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


def update_post(
    post_id,
    title=None,
    content=None,
    image_filename=None,
    folder=None,
    delete_image=False
):
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
#  Firestore：Profile / 關於我
# =========================

def get_profile_firestore():
    """讀取 Firestore 的 blog profile（若不存在回傳預設）"""
    doc_ref = db.collection(PROFILE_COLLECTION).document(PROFILE_DOC_ID)
    doc = doc_ref.get()

    default_profile = {
        "avatar_filename": None,
        "avatar_pos_x": 0,
        "avatar_pos_y": 0,
        "avatar_zoom": 1.0,
        "title": "Ellen 的房產筆記本",
        "subtitle": "記錄海線房產、租屋大小事、投資理財心情、與每天的創業生活。",
        "tags": ["海線房仲", "房產知識"],

        # 「關於我」區塊
        "about_title": "關於 Ellen",
        "about_text": "太平洋房屋｜海線房仲\n喜歡用故事、影片和文字，陪你一起找到適合的家。",

        "updated_at": datetime.now(),
    }

    if doc.exists:
        data = doc.to_dict() or {}
        for k, v in default_profile.items():
            data.setdefault(k, v)
        return data
    else:
        return default_profile


def save_profile_firestore(profile: dict):
    """儲存 blog profile 回 Firestore"""
    profile["updated_at"] = datetime.now()
    db.collection(PROFILE_COLLECTION).document(PROFILE_DOC_ID).set(profile)


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
            new_filename = ""  # Firestore 中設為空字串

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
#  CKEditor 內文圖片上傳
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
#  前台：單篇文章
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
#  前台：Blog 首頁（含 Profile / 關於我）
# =========================

@blog_bp.route("/blog")
def index():
    folder = request.args.get("folder", "")
    all_posts = get_all_posts()
    folders = load_folders()

    if folder:
        filtered_posts = [p for p in all_posts if p.get("folder") == folder]
    else:
        filtered_posts = all_posts

    # 🔹 從 Firestore 讀取個人資料 / 關於我
    profile = get_profile_firestore()

    avatar_filename = profile.get("avatar_filename")
    if avatar_filename:
        avatar_url_path = f"images/blog/{avatar_filename}"
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
#  後台：Blog Profile / 關於我（Firestore 版）
# =========================

@blog_bp.route("/blog/profile", methods=["GET", "POST"])
def blog_profile():
    login = login_required()
    if login:
        return login

    # 存大頭貼的資料夾：static/images/blog
    blog_img_dir = os.path.join(current_app.static_folder, "images", "blog")
    os.makedirs(blog_img_dir, exist_ok=True)

    # 讀取現有 profile（Firestore）
    profile = get_profile_firestore()

    if request.method == "POST":
        # 1) 大頭貼上傳
        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            ext = os.path.splitext(secure_filename(avatar_file.filename))[1] or ".jpg"
            filename = f"avatar_{uuid4().hex}{ext}"
            save_path = os.path.join(blog_img_dir, filename)
            avatar_file.save(save_path)
            profile["avatar_filename"] = filename

        # 2) 頭像位置 / 縮放
        def to_float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        profile["avatar_pos_x"] = to_float(request.form.get("avatar_pos_x"), 0.0)
        profile["avatar_pos_y"] = to_float(request.form.get("avatar_pos_y"), 0.0)
        profile["avatar_zoom"] = to_float(request.form.get("avatar_zoom"), 1.0)

        # 3) Profile 文字
        title = (request.form.get("title") or "").strip()
        subtitle = (request.form.get("subtitle") or "").strip()
        about_title = (request.form.get("about_title") or "").strip()
        about_text = (request.form.get("about_text") or "").strip()

        if title:
            profile["title"] = title
        if subtitle:
            profile["subtitle"] = subtitle
        if about_title:
            profile["about_title"] = about_title
        if about_text:
            profile["about_text"] = about_text

        # 4) 標籤 tags
        tags_raw = (request.form.get("tags_raw") or "").strip()
        if tags_raw:
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            tags = []
        profile["tags"] = tags

        # 寫回 Firestore
        save_profile_firestore(profile)

        flash("已更新部落格個人資料（包含關於我）", "success")
        return redirect(url_for("blog.index"))

    # GET：顯示編輯畫面
    avatar_filename = profile.get("avatar_filename")
    if avatar_filename:
        avatar_url_path = f"images/blog/{avatar_filename}"
    else:
        avatar_url_path = "images/blog/default_avatar.png"

    # 後台編輯頁如果要顯示 tags_raw（逗號字串）
    tags = profile.get("tags") or []
    if isinstance(tags, list):
        tags_raw = ", ".join(tags)
    else:
        tags_raw = str(tags)

    return render_template(
        "admin/blog_profile.html",
        profile=profile,
        avatar_url_path=avatar_url_path,
        tags_raw=tags_raw,
    )
