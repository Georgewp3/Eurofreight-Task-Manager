import csv
import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from sqlalchemy import func, cast, Date
from sqlalchemy.exc import SQLAlchemyError

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, session, send_file, flash
)
from dotenv import load_dotenv

from models import db, User, Task, LogEntry, ScheduledTask

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "332133")  # fixed as requested

def _resolve_db_uri() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set. Configure it in Render → Environment.")

    # Force psycopg3 driver for SQLAlchemy
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    # Ensure SSL for hosted DBs
    if "sslmode=" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
    return db_url



def create_app():
    app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_db_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-dev-key")
    db.init_app(app)

    # Cyprus time filter (uses timezone.utc; works fine on Windows/Linux)
    def cy_time(dt):
        if not dt:
            return ""
        cy = ZoneInfo("Europe/Nicosia")
        return dt.replace(tzinfo=timezone.utc).astimezone(cy).strftime("%d/%m/%Y - %H:%M:%S")
    app.jinja_env.filters["cy_time"] = cy_time

    with app.app_context():
        db.create_all()
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
    
    @app.get("/admin/schedules")
    @admin_required
    def admin_schedules():
        users = User.query.order_by(User.full_name.asc()).all()
        schedules = ScheduledTask.query.order_by(ScheduledTask.id.desc()).all()
        return render_template("schedules.html", users=users, schedules=schedules)
    
    @app.get("/admin/insights")
    @admin_required
    def admin_insights():
        return render_template("insights.html")
    
    @app.get("/admin/insights/data")
    @admin_required
    def admin_insights_data():
        now = datetime.utcnow()
        d30 = now - timedelta(days=29)
        d7  = now - timedelta(days=6)


        total = db.session.query(func.count(LogEntry.id)).scalar() or 0
        completed = db.session.query(func.count(LogEntry.id)).filter(
            LogEntry.status == "COMPLETED"
        ).scalar() or 0
        completion_rate = (completed / total * 100.0) if total else 0.0

        not_done_with_comment = db.session.query(func.count(LogEntry.id)).filter(
            LogEntry.status != "COMPLETED",
            func.length(func.coalesce(LogEntry.comment, "")) > 0
        ).scalar() or 0

        # --- Dialect-aware "day" bucketing ------------------------
        bind = db.session.get_bind()
        dialect = (bind.dialect.name if bind is not None else "postgresql")

        if dialect == "sqlite":
            # SQLite likes strftime for day-bucketing
            day_expr = func.strftime('%Y-%m-%d', LogEntry.timestamp).label("day")
        else:
            # Postgres (and others): cast timestamp to DATE
            day_col = cast(LogEntry.timestamp, Date).label("day")

        daily_rows = (
            db.session.query(day_col, func.count(LogEntry.id))
            .filter(LogEntry.timestamp.isnot(None), LogEntry.timestamp >= d30)
            .group_by(day_col)
            .order_by(day_col)
            .all()
        )

        daily_labels = [
            d if isinstance(d, str) else d.isoformat()
            for (d, _) in daily_rows
        ]
        daily_counts = [int(c) for (_, c) in daily_rows]

        # Per-user completions (last 30 days) 
        per_user_rows = (
            db.session.query(LogEntry.user_name, func.count(LogEntry.id))
            .filter(
                LogEntry.timestamp.isnot(None),
                LogEntry.timestamp >= d30,
                LogEntry.status == "COMPLETED",
            )
            .group_by(LogEntry.user_name)
            .order_by(func.count(LogEntry.id).desc())
            .limit(10)
            .all()
        )
        user_labels = [r[0] for r in per_user_rows]
        user_counts = [int(r[1]) for r in per_user_rows]
        
        # Per-project completions (last 30 days)
        proj_key = func.coalesce(LogEntry.project, "-")

        per_proj_rows = (
            db.session.query(proj_key.label("project"), func.count(LogEntry.id))
            .filter(
                LogEntry.timestamp.isnot(None),
                LogEntry.timestamp >= d30,
                LogEntry.status == "COMPLETED",
            )
            .group_by(proj_key)
            .order_by(func.count(LogEntry.id).desc())
            .limit(10)
            .all()
        )
        proj_labels = [r[0] for r in per_proj_rows]
        proj_counts = [int(r[1]) for r in per_proj_rows]


        # Last 7 days completion rate
        last7_total = (
            db.session.query(func.count(LogEntry.id))
            .filter(LogEntry.timestamp.isnot(None), LogEntry.timestamp >= d7)
            .scalar()
            or 0
        )

        last7_completed = (
             db.session.query(func.count(LogEntry.id))
             .filter(
                 LogEntry.timestamp.isnot(None),
                 LogEntry.timestamp >= d7,
                 LogEntry.status == "COMPLETED",
             )
             .scalar()
             or 0
        )

        last7_rate = (last7_completed / last7_total * 100.0) if last7_total else 0.0
       
        return jsonify({
            "kpis": {
                "total_submissions": total,
                "completed": completed,
                "completion_rate": round(completion_rate, 1),
                "not_done_with_comment": not_done_with_comment,
                "last7_rate": round(last7_rate, 1),
            },
            "daily": {"labels": daily_labels, "counts": daily_counts},
            "per_user": {"labels": user_labels, "counts": user_counts},
            "per_project": {"labels": proj_labels, "counts": proj_counts},
        })

    @app.post("/admin/schedules/add")
    @admin_required
    def admin_schedules_add():
        user_id = int(request.form.get("user_id"))
        title = (request.form.get("title") or "").strip()
        project = (request.form.get("project") or "-").strip() or "-"

        # checkboxes named wd_MON, wd_TUE, ... in the form
        chosen = [d for d in ["MON","TUE","WED","THU","FRI","SAT","SUN"] if request.form.get(f"wd_{d}")]
        wds = ",".join(chosen) if chosen else "MON"

        time_local = (request.form.get("time_local") or "09:00")[:5]
        tz = "Europe/Nicosia"

        if not title:
            flash("Title required.", "danger")
            return redirect(url_for("admin_schedules"))

        db.session.add(ScheduledTask(
            user_id=user_id, title=title, project=project,
            weekdays=wds, time_local=time_local, tz=tz, active=True
        ))
        db.session.commit()
        flash("Schedule added.", "success")
        return redirect(url_for("admin_schedules"))

    # Delete
    @app.post("/admin/schedules/delete/<int:sid>")
    @admin_required
    def admin_schedules_delete(sid):
        s = ScheduledTask.query.get_or_404(sid)
        db.session.delete(s)
        db.session.commit()
        flash("Schedule removed.", "success")
        return redirect(url_for("admin_schedules"))

    # Secure internal endpoint for the cron job to materialize today's tasks
    SCHEDULE_TOKEN = os.getenv("SCHEDULE_TOKEN")

    @app.post("/internal/run-schedules")
    def internal_run_schedules():
        token = request.headers.get("X-TaskApp-Token") or request.args.get("token")
        if not SCHEDULE_TOKEN or token != SCHEDULE_TOKEN:
            return ("Unauthorized", 401)

        tz = ZoneInfo("Europe/Nicosia")
        now_local = datetime.now(tz)
        today = now_local.date()
        wd = now_local.strftime("%a").upper()[:3]  # "MON", "TUE", ...

        created = 0
        schedules = ScheduledTask.query.filter_by(active=True).all()
        for s in schedules:
            allowed = set(x.strip().upper() for x in (s.weekdays or "").split(",") if x.strip())
            if wd not in allowed:
                continue

            try:
                hh, mm = (s.time_local or "00:00").split(":")
                run_time = datetime(today.year, today.month, today.day, int(hh), int(mm), tzinfo=tz)
            except Exception:
                run_time = datetime(today.year, today.month, today.day, 0, 0, tzinfo=tz)

            if now_local < run_time:
                continue

            if s.last_run_date == today:
                continue

            exists = Task.query.filter_by(user_id=s.user_id, title=s.title, project=s.project).first()
            if not exists:
                db.session.add(Task(user_id=s.user_id, project=s.project, title=s.title))
                created += 1

            s.last_run_date = today

        db.session.commit()
        return {"created": created, "date": str(today), "weekday": wd}, 200




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
            comment=comment,
            timestamp=datetime.now.utcnow(),
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
        q = (request.args.get("q") or "").strip()
        
        query = User.query
        if q:
            query = query.filter(User.full_name.ilike(f"%{q}%"))

        users = query.order_by(User.full_name.asc()).all()

        # Flatten for “Assign Tasks” card
        rows = []
        for u in users:
            rows.append({
                "id": u.id,
                "name": u.full_name,
                "active": u.active,
                "tasks": [{"id": t.id, "project": t.project, "title": t.title} for t in u.tasks],
            })
        return render_template("admin.html", stage="panel", users=rows, q=q)


        

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
        cy = ZoneInfo("Europe/Nicosia")  

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["User", "Project", "Task", "Status", "Timestamp", "Comment"])
            for row in logs:
                ts = row.timestamp or datetime.utcnow()
                ts_local = ts.replace(tzinfo=timezone.utc).astimezone(cy)
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