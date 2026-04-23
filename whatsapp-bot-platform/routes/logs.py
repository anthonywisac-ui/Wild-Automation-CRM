from flask import Blueprint, render_template

logs_bp = Blueprint("logs", __name__)

@logs_bp.route("/logs")
def view_logs():
    try:
        with open("logs/ai.log", "r", encoding="utf-8") as f:
            logs = f.readlines()
    except:
        logs = ["No logs yet"]

    return render_template("logs.html", logs=logs[::-1])