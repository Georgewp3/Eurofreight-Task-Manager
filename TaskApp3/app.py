import csv
import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from sqlalchemy import func, cast, Date
from sqlalchemy.exc import SQLAlchemyError
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, session, send_file, flash
)
from dotenv import load_dotenv

from models import db, User, Task, LogEntry, ScheduledTask, OvertimeEntry, ExportMarker, OvertimeTotal

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "332133")  
GEORGE_PASSWORD = os.getenv("GEORGE_PASSWORD", "040773")

def _resolve_db_uri() -> str:
    db_url = os.getenv("DATABASE_URL")

    # Prefer Postgres if DATABASE_URL is set (Render)
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        if "sslmode=" not in db_url and db_url.startswith("postgresql+psycopg://"):
            db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
        return db_url
    
    sqlite_path = Path(__file__).with_name("task_db.sqlite3")
    return f"sqlite:///{sqlite_path.as_posix()}"



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
    
    def george_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("is_george") is True:
                return f(*args, **kwargs)
            return redirect(url_for("admin_login"))  # send to the same login page
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
        dialect = (bind.dialect.name if bind else "postgresql")

        if dialect == "sqlite":
            # SQLite likes strftime for day-bucketing
            day_col = func.strftime('%Y-%m-%d', LogEntry.timestamp).label("day")
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
        
    @app.get("/overtime")
    def overtime_page():
        users = User.query.filter_by(active=True).order_by(User.full_name.asc()).all()
        return render_template("overtime.html", users=users)

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
    
    @app.post("/overtime/submit")
    def overtime_submit():
        user_id = int(request.form.get("user_id"))
        project_choice = (request.form.get("project") or "").strip()
        project_other = (request.form.get("project_other") or "").strip()
        overtime_date_str = (request.form.get("overtime_date") or "").strip()
        duration = (request.form.get("duration") or "").strip()
        allowed_durations = {f"{x/2:.1f}" for x in range(1, 21)}
        if duration not in allowed_durations:
            flash("Invalid duration selected.", "danger")
            return redirect(url_for("overtime_page"))
        
        duration = f"{float(duration):.1f}"
        
        user = User.query.get_or_404(user_id)
        
        if project_choice == "OTHER":
            project_final = project_other
        else:
            project_final = project_choice
            
        allowed = {"TEMU","ALPHAMEGA","PUBLIC","FROZEN","TRANSPORT","OTHER"}
        if project_choice not in allowed:
            flash("Invalid project choice.", "danger")
            return redirect(url_for("overtime_page"))
        
        if project_choice == "OTHER" and not project_other:
            flash("Please type the project name for OTHER.", "danger")
            return redirect(url_for("overtime_page"))
        
        if not overtime_date_str:
            flash("Please select an overtime date.", "danger")
            return redirect(url_for("overtime_page"))
        
        if not duration:
            flash("Please enter the duration.", "danger")
            return redirect(url_for("overtime_page"))
        
        try:
            overtime_date = datetime.strptime(overtime_date_str, "%Y-%m-%d").date()
        except Exception:
            flash("Invalid date.", "danger")
            return redirect(url_for("overtime_page"))
        
        entry = OvertimeEntry(
            user_name=user.full_name,
            project=project_final,
            overtime_date=overtime_date,
            duration=duration,
            timestamp=datetime.utcnow(),
        )
        db.session.add(entry)
        db.session.commit()
        
        recompute_overtime_totals()
        
        flash("Overtime entry submitted.", "success")
        return redirect(url_for("overtime_page"))
    
    @app.post("/admin/overtimes/clear")
    @admin_required
    def admin_clear_overtimes():
        count = OvertimeEntry.query.delete()
        db.session.commit()
        flash(f"Cleared {count} overtime rows.", "warning")
        return redirect(url_for("admin_data_bank"))
    
    @app.post("/admin/overtimes/delete/<int:oid>")
    @admin_required
    def admin_delete_overtime(oid):
        o = OvertimeEntry.query.get_or_404(oid)
        db.session.delete(o)
        db.session.commit()
        
        recompute_overtime_totals()
        
        flash("Overtime entry deleted.", "success")
        return redirect(url_for("admin_data_bank"))
    
    @app.post("/admin/overtimes/edit/<int:oid>")
    @admin_required
    def admin_edit_overtime(oid):
        o = OvertimeEntry.query.get_or_404(oid)
        
        project = (request.form.get("project") or "").strip()
        overtime_date_str = (request.form.get("overtime_date") or "").strip()
        duration = (request.form.get("duration") or "").strip()
        
        if not project:
            flash("Project cannot be empty.", "danger")
            return redirect(url_for("admin_data_bank"))
        
        try:
            overtime_date = datetime.strptime(overtime_date_str, "%Y-%m-%d").date()
        except Exception:
            flash("Invalid date format.", "danger")
            return redirect(url_for("admin_data_bank"))
        
        allowed = {f"{x/2:.1f}" for x in range(1, 21)}
        if duration not in allowed:
            flash("Invalid duration.", "danger")
            return redirect(url_for("admin_data_bank"))
        
        o.project = project
        o.overtime_date = overtime_date
        o.duration = f"{float(duration):.1f}"
        db.session.commit()
        
        recompute_overtime_totals()
        
        flash("Overtime entry updated.", "success")
        return redirect(url_for("admin_data_bank")) 
    
    @app.post("/admin/overtimes/totals/clear")
    @admin_required
    def admin_clear_overtime_totals():
        count = OvertimeTotal.query.delete()
        db.session.commit()
        flash(f"Cleared {count} overtime total rows.", "warning")
        return redirect(url_for("admin_data_bank"))
    
    def recompute_overtime_totals():
        
        OvertimeTotal.query.delete()
        
        totals = {}
        for o in OvertimeEntry.query.all():
            try:
                hrs = float(o.duration)
            except Exception:
                hrs = 0.0
            totals[o.user_name] = totals.get(o.user_name, 0.0) + hrs
            
        for name, total in totals.items():
            db.session.add(OvertimeTotal(user_name=name, total_hours=round(total, 1)))
            
        db.session.commit()




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
            timestamp=datetime.utcnow(),
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
    
    @app.post("/george/login")
    def george_login_post():
        if request.form.get("password") == GEORGE_PASSWORD:
            session["is_george"] = True
            return redirect(url_for("george_page"))
        flash("Wrong password.", "danger")
        return redirect(url_for("admin_login"))

    @app.get("/george")
    @george_required
    def george_page():
        return render_template("george.html")

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
        overtimes = OvertimeEntry.query.order_by(OvertimeEntry.timestamp.desc()).all()
        
        overtime_totals = OvertimeTotal.query.order_by(OvertimeTotal.user_name.asc()).all()
        
        marker = ExportMarker.query.get("overtime_totals")
        last_export_totals = marker.last_export_utc if marker else None
        
        return render_template(
            "data_bank.html",
            logs=logs,
            overtimes=overtimes,
            overtime_totals=overtime_totals,
            last_export_totals=last_export_totals
      )

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
    
    @app.get("/admin/overtimes/export")
    @admin_required
    def admin_export_overtimes_csv():
        filename = f"overtimes_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        rows = OvertimeEntry.query.order_by(OvertimeEntry.timestamp.asc()).all()
        cy = ZoneInfo("Europe/Nicosia")
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["User", "Project", "Overtime Date", "Duration", "Submitted At"])
            for r in rows:
                submitted_local = r.timestamp.replace(tzinfo=timezone.utc).astimezone(cy)
                writer.writerow([
                    r.user_name,
                    r.project,
                    r.overtime_date.strftime("%d/%m/%Y"),
                    r.duration,
                    submitted_local.strftime("%d/%m/%Y - %H:%M:%S"),
                ])
                
        return send_file(filepath, as_attachment=True, download_name=filename)
    
    @app.get("/admin/overtimes/totals/export")
    @admin_required
    def admin_export_overtime_totals_csv():
        filename = f"overtime_totals_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        rows = OvertimeEntry.query.all()
        
        totals_map = {}
        for o in rows:
            try:
                hrs = float(o.duration)
            except Exception:
                hrs = 0.0
            totals_map[o.user_name] = totals_map.get(o.user_name, 0.0) + hrs
            
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["User", "Total Overtime (hours)"])
            for name in sorted(totals_map.keys(), key=lambda x: x.lower()):
                writer.writerow([name, f"{totals_map[name]:.1f}"])
                
        marker = ExportMarker.query.get("overtime_totals")
        if not marker:
            marker = ExportMarker(key="overtime_totals")
            db.session.add(marker)
        marker.last_export_utc = datetime.utcnow()
        db.session.commit()
        
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