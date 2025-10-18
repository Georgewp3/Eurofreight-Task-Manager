import csv
import os
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, session, send_file, flash
)
from dotenv import load_dotenv

from models import db, User, Task, LogEntry

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "332133")  # fixed as requested
DB_PATH = os.getenv("DB_PATH", "/var/data/task_db.sqlite3")

def _resolve_db_uri():
   #db_url = os.getenv("DATABASE_URL")
   #if db_url:

#    if db_url.startswith("postgres://"):
 #       db_url = db_url.replace("postgres://", "postgresql://", 1)

#        if "sslmode=" not in db_url:
 #           db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
  #      return db_url
    
    return f"sqlite:///{DB_PATH}"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def create_app():
    app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-dev-key")
    db.init_app(app)
    def cy_time(dt):
        if not dt:
            return ""
        return (
            dt.replace(tzinfo=ZoneInfo("UTC"))
              .astimezone(ZoneInfo("Europe/Nicosia"))
              .strftime("%d/%m/%Y - %H:%M:%S")
        )
    app.jinja_env.filters["cy_time"] = cy_time

    with app.app_context():
        db.create_all()
        # Seed a demo user if database is empty
        if not User.query.first():
            demo = User(full_name="Demo User")
            db.session.add(demo)
            db.session.add(Task(user=demo, project="General", title="Read onboarding doc"))
            db.session.commit()

    @app.get("/admin/db/download")
    @admin_required
    def admin_download_db():
        # Copy DB to a temp path to avoid file locking while the app is running
        tmp_name = f"task_db_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.sqlite3"
        tmp_path = os.path.join("/tmp", tmp_name)

        import shutil
        shutil.copyfile(DB_PATH, tmp_path)

        return send_file(tmp_path, as_attachment=True, download_name=tmp_name)

    # ---------- Helpers ----------
    def admin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("is_admin") is True:
                return f(*args, **kwargs)
            return redirect(url_for("admin_login"))
        return wrapper
    
   
    # ---------- Views ----------
    @app.get("/")
    def user_page():
        users = User.query.filter_by(active=True).order_by(User.full_name.asc()).all()
        return render_template("user.html", users=users)

    @app.post("/submit")
    def submit_entry():
        data = request.form
        user_id = int(data.get("user_id"))
        task_id = int(data.get("task_id"))
        status = data.get("status")  # COMPLETED or NOT_COMPLETED
        comment = (data.get("comment") or "").strip()

        user = User.query.get_or_404(user_id)
        task = Task.query.get_or_404(task_id)

        entry = LogEntry(
            user_name=user.full_name,
            project=task.project or "-",
            task_title=task.title,
            status=status,
            comment=comment
        )
        db.session.add(entry)
        db.session.commit()
        flash("Entry submitted.", "success")
        return redirect(url_for("user_page"))

    # --- APIs for dynamic UI ---
    @app.get("/api/users")
    def api_users():
        users = User.query.filter_by(active=True).order_by(User.full_name.asc()).all()
        return jsonify([{"id": u.id, "name": u.full_name} for u in users])

    @app.get("/api/user/<int:user_id>/tasks")
    def api_user_tasks(user_id: int):
        tasks = Task.query.filter_by(user_id=user_id).order_by(Task.created_at.desc()).all()
        return jsonify([
            {"id": t.id, "project": t.project or "-", "title": t.title}
            for t in tasks
        ])

    # ---------- Admin ----------
    @app.get("/admin/login")
    def admin_login():
        return render_template("admin.html", stage="login")

    @app.post("/admin/login")
    def admin_login_post():
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        flash("Wrong password.", "danger")
        return redirect(url_for("admin_login"))

    @app.get("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("user_page"))

    @app.get("/admin")
    @admin_required
    def admin_panel():
        users = User.query.order_by(User.full_name.asc()).all()
        # Flatten for “Assign Tasks” card
        rows = []
        for u in users:
            rows.append({
                "id": u.id, "name": u.full_name, "active": u.active,
                "tasks": [{"id": t.id, "project": t.project, "title": t.title} for t in u.tasks]
            })
        return render_template("admin.html", stage="panel", users=rows)

    @app.post("/admin/add-user")
    @admin_required
    def admin_add_user():
        name = (request.form.get("full_name") or "").strip()
        if not name:
            flash("Name required.", "danger")
        elif User.query.filter_by(full_name=name).first():
            flash("User already exists.", "warning")
        else:
            db.session.add(User(full_name=name))
            db.session.commit()
            flash("User added.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/admin/remove-user/<int:user_id>")
    @admin_required
    def admin_remove_user(user_id):
        user = User.query.get_or_404(user_id)
        db.session.delete(user)
        db.session.commit()
        flash("User & their tasks removed.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/admin/add-task/<int:user_id>")
    @admin_required
    def admin_add_task(user_id):
        user = User.query.get_or_404(user_id)
        project = (request.form.get("project") or "-").strip() or "-"
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Task title required.", "danger")
        else:
            db.session.add(Task(user=user, project=project, title=title))
            db.session.commit()
            flash("Task added.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/admin/remove-task/<int:task_id>")
    @admin_required
    def admin_remove_task(task_id):
        t = Task.query.get_or_404(task_id)
        db.session.delete(t)
        db.session.commit()
        flash("Task removed.", "success")
        return redirect(url_for("admin_panel"))

    # ---------- Data Bank ----------
    @app.get("/admin/data-bank")
    @admin_required
    def admin_data_bank():
        logs = LogEntry.query.order_by(LogEntry.timestamp.desc()).all()
        return render_template("data_bank.html", logs=logs)

    @app.get("/admin/data-bank/export")
    @admin_required
    def admin_export_csv():
        filename = f"data_bank_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(os.path.dirname(__file__), filename)

        logs = LogEntry.query.order_by(LogEntry.timestamp.asc()).all()
        cy = ZoneInfo("Europe/Nicosia")  # Cyprus time

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["User", "Project", "Task", "Status", "Timestamp", "Comment"])
            for row in logs:
                ts_local = row.timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(cy)
                writer.writerow([
                row.user_name,
                row.project,
                row.task_title,
                row.status,
                ts_local.strftime('%d/%m/%Y - %H:%M:%S'),
                row.comment or ""
                ])
        return send_file(filepath, as_attachment=True, download_name=filename)
    
    @app.post("/admin/data-bank/clear")
    @admin_required
    def admin_clear_bank():
        count = LogEntry.query.delete()
        db.session.commit()
        flash(f"Cleared {count} log rows.", "warning")
        return redirect(url_for("admin_data_bank"))

    return app

app = create_app()

if __name__ == "__main__":
    # Dev server
    app.run(host="0.0.0.0", port=5000, debug=True)
