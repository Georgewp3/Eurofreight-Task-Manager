import csv
import os
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime
from datetime import datetime, timezone

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
LOCAL_WIN_DB = Path(
    r"C:\Users\giorg\OneDrive\Υπολογιστής\My Projects\Task Manager App - Python\TaskApp3\task_db.sqlite3"
).resolve()

DB_PATH = os.getenv("DB_PATH", str(LOCAL_WIN_DB))

# Ensure the folder exists locally
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

def _resolve_db_uri() -> str:
    # SQLAlchemy wants forward slashes even on Windows
    return f"sqlite:///{Path(DB_PATH).as_posix()}"

def create_app():
    app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-dev-key")
    db.init_app(app)
        # Cyprus time filter (works on Windows: uses timezone.utc)
    def cy_time(dt):
        if not dt:
            return ""
        cy = ZoneInfo("Europe/Nicosia")
        return (
            dt.replace(tzinfo=timezone.utc)   # assume stored in UTC
              .astimezone(cy)
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

        # ---------- Helpers ----------
    def admin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("is_admin") is True:
                return f(*args, **kwargs)
            return redirect(url_for("admin_login"))
        return wrapper

    # --- Merge a source SQLite DB into the current DB (by name/keys) ---
    def merge_sqlite_into_current(src_path: str):
        src = sqlite3.connect(src_path)
        src.row_factory = sqlite3.Row
        cur = src.cursor()

        # 1) USERS: dedupe by full_name
        src_users = cur.execute("""
            SELECT full_name, COALESCE(active, 1) AS active
            FROM users
        """).fetchall()
        added_users = 0
        for u in src_users:
            if not User.query.filter_by(full_name=u["full_name"]).first():
                db.session.add(User(full_name=u["full_name"], active=bool(u["active"])))
                added_users += 1
        db.session.commit()

        # 2) TASKS: join source tasks->users to get user_name; dedupe by (dest_user_id, title, project)
        src_tasks = cur.execute("""
            SELECT t.project, t.title, u.full_name AS user_name
            FROM tasks t
            JOIN users u ON u.id = t.user_id
        """).fetchall()
        added_tasks = 0
        for t in src_tasks:
            dest_user = User.query.filter_by(full_name=t["user_name"]).first()
            if not dest_user:
                continue
            exists = Task.query.filter_by(
                user_id=dest_user.id, title=t["title"], project=(t["project"] or "-")
            ).first()
            if not exists:
                db.session.add(Task(user=dest_user, project=(t["project"] or "-"), title=t["title"]))
                added_tasks += 1
        db.session.commit()

        # 3) LOGS: dedupe by (user_name, task_title, timestamp)
        src_logs = cur.execute("""
            SELECT user_name, project, task_title, status, comment, timestamp
            FROM log_entries
        """).fetchall()

        def _parse_ts(val):
            if isinstance(val, datetime):
                return val
            if not val:
                return datetime.utcnow()
            for fmt in ("%Y-%m-%d %H:%M:%S.%f",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(val, fmt)
                except Exception:
                    pass
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return datetime.utcnow()

        added_logs = 0
        for r in src_logs:
            ts = _parse_ts(r["timestamp"])
            exists = LogEntry.query.filter_by(
                user_name=r["user_name"],
                task_title=r["task_title"],
                timestamp=ts
            ).first()
            if not exists:
                db.session.add(LogEntry(
                    user_name=r["user_name"],
                    project=(r["project"] or "-"),
                    task_title=r["task_title"],
                    status=r["status"],
                    comment=(r["comment"] or ""),
                    timestamp=ts
                ))
                added_logs += 1
        db.session.commit()

        src.close()
        return added_users, added_tasks, added_logs

    @app.post("/admin/db/update")
    @admin_required
    def admin_update_db():
        f = request.files.get("dbfile")
        if not f or not f.filename:
            flash("No file selected.", "warning")
            return redirect(url_for("admin_data_bank"))

        name = f.filename.lower()
        if not (name.endswith(".sqlite3") or name.endswith(".sqlite") or name.endswith(".db")):
            flash("Please upload a .sqlite3/.sqlite/.db file.", "danger")
            return redirect(url_for("admin_data_bank"))

        # Save upload to /tmp
        tmp_src = os.path.join("/tmp", f"upload_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.sqlite3")
        try:
            f.save(tmp_src)
        except Exception as e:
            flash(f"Upload failed: {e}", "danger")
            return redirect(url_for("admin_data_bank"))

        # Count BEFORE
        before = {
            "users": db.session.query(User).count(),
            "tasks": db.session.query(Task).count(),
            "logs":  db.session.query(LogEntry).count(),
        }

        try:
            add_u, add_t, add_l = merge_sqlite_into_current(tmp_src)

            after = {
                "users": db.session.query(User).count(),
                "tasks": db.session.query(Task).count(),
                "logs":  db.session.query(LogEntry).count(),
            }

            flash(
                f"Update complete: +{add_u} users, +{add_t} tasks, +{add_l} logs "
                f"(totals now U:{after['users']} T:{after['tasks']} L:{after['logs']}).",
                "success"
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Update failed: {e}", "danger")
        finally:
            try:
                os.remove(tmp_src)
            except Exception:
                pass

        return redirect(url_for("admin_data_bank"))

       
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
                ts_local = row.timestamp.replace(tzinfo=timezone.utc).astimezone(cy)
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
