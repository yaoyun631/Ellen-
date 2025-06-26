from flask import Blueprint

blog_bp = Blueprint("blog", __name__, template_folder="templates/blog")

from . import routes
