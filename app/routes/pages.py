from flask import Blueprint, render_template

from ..config import APP_NAME

pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    return render_template("index.html", app_name=APP_NAME)
