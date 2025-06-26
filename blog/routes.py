from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, current_app
)
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import json

blog_bp = Blueprint('blog', __name__, template_folder='templates')

posts = []
POSTS_FILE = 'posts.json'
FOLDERS_FILE = 'folders.json'


def login_required():
    if not session.get('logged_in'):
        flash("請先登入後台")
        return redirect(url_for('admin_login'))
    return None

def load_posts():
    global posts
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for post in data:
                post['created_at'] = datetime.fromisoformat(post['created_at'])
            posts = data


def save_posts():
    with open(POSTS_FILE, 'w', encoding='utf-8') as f:
        json.dump([
            {**post, 'created_at': post['created_at'].isoformat()} for post in posts
        ], f, ensure_ascii=False, indent=2)

load_posts()

@blog_bp.route('/admin/blog')
def admin_blog():
    login = login_required()
    if login:
        return login
    return render_template('admin/admin_blog.html', posts=posts)

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
        filename = ""

        if image and image.filename:
            filename = secure_filename(image.filename)
            upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            image.save(os.path.join(upload_folder, filename))

        post = {
            'id': len(posts) + 1,
            'title': title,
            'content': content,
            'image': filename,
            'created_at': datetime.now(),
            'folder': folder
        }
        posts.append(post)
        save_posts()
        flash("文章新增成功")
        return redirect(url_for('blog.admin_blog'))

    folders = load_folders()
    return render_template('admin/new_post.html', folders=folders)




@blog_bp.route('/admin/blog/edit/<int:post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    login = login_required()
    if login:
        return login

    post = next((p for p in posts if p['id'] == post_id), None)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.admin_blog'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        folder = request.form.get('folder', '未分類')  # ✅ 新增這行
        image = request.files.get('image')
        delete_image = request.form.get('delete_image')

        if title:
            post['title'] = title
        if content:
            post['content'] = content
        post['folder'] = folder  # ✅ 寫入新的分類

        if delete_image == 'on' and post['image']:
            upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
            image_path = os.path.join(upload_folder, post['image'])
            if os.path.exists(image_path):
                os.remove(image_path)
            post['image'] = ''

        if image and image.filename:
            filename = secure_filename(image.filename)
            upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            image.save(os.path.join(upload_folder, filename))
            post['image'] = filename

        save_posts()
        flash("文章已更新")
        return redirect(url_for('blog.admin_blog'))

    folders = load_folders()
    return render_template('admin/edit_post.html', post=post, folders=folders)


@blog_bp.route('/admin/blog/delete/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    login = login_required()
    if login:
        return login

    global posts
    posts = [p for p in posts if p['id'] != post_id]
    save_posts()
    flash("文章已刪除")
    return redirect(url_for('blog.admin_blog'))

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

# 🔹 前台單篇文章頁面
@blog_bp.route('/post/<int:post_id>')
def show_post(post_id):
    post = next((p for p in posts if p['id'] == post_id), None)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.index'))
    return render_template('blog/show_post.html', post=post)


@blog_bp.route('/post/<int:post_id>/detail')
def view_post(post_id):
    post = next((p for p in posts if p['id'] == post_id), None)
    if not post:
        flash("找不到文章")
        return redirect(url_for('blog.index'))
    return render_template('blog/post_detail.html', post=post)

@blog_bp.route('/blog')
def index():
    folder = request.args.get('folder', '')  # ?folder=分類名稱
    all_posts = posts  # 直接用全域 posts
    folders = load_folders()

    if folder:
        filtered_posts = [p for p in all_posts if p.get('folder') == folder]
    else:
        filtered_posts = all_posts

    return render_template(
        'blog/index.html',
        posts=filtered_posts,
        folders=folders,
        current_folder=folder
    )



FOLDERS_FILE = 'folders.json'

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
