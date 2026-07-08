from datetime import datetime

# models.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# --- Users who receive tasks ---
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    login_password_hash = db.Column(db.String(255), nullable=True)

    # backref "user" on Task
    tasks = db.relationship("Task", backref="user", cascade="all, delete-orphan")


# --- Assignable tasks (show up for a user until completed) ---
class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    project = db.Column(db.String(120), default="-", nullable=False)
    title = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)


# --- Submitted entries (audit/data bank) ---
class LogEntry(db.Model):
    __tablename__ = "log_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(200), nullable=False)
    project = db.Column(db.String(120), default="-", nullable=False)
    task_title = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(40), nullable=False)  # "COMPLETED" or "NOT_COMPLETED"
    comment = db.Column(db.Text, default="", nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    # Used by older CSV export code; safe to keep
    def as_csv_row(self):
        return [
            self.user_name,
            self.project or "-",
            self.task_title,
            self.status,
            self.timestamp,   # format in the route if needed
            self.comment or "",
        ]


# --- Recurring schedules (admin-configured, materialized by cron) ---
class ScheduledTask(db.Model):
    __tablename__ = "scheduled_tasks"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User")

    # What to create
    project = db.Column(db.String(120), default="-", nullable=False)
    title = db.Column(db.String(255), nullable=False)

    # When to create it
    weekdays = db.Column(db.String(50), nullable=False)     # e.g., "MON,FRI"
    time_local = db.Column(db.String(5), default="09:00", nullable=False)  # "HH:MM"
    tz = db.Column(db.String(64), default="Europe/Nicosia", nullable=False)

    # Prevent duplicate creation per calendar day
    last_run_date = db.Column(db.Date, nullable=True)

    active = db.Column(db.Boolean, default=True, nullable=False)
    
class OvertimeEntry(db.Model):
    __tablename__ = "overtime_entries"

    id = db.Column(db.Integer, primary_key=True)

    # store name (like LogEntry does) so history stays correct even if user is removed/renamed
    user_name = db.Column(db.String(255), nullable=False)

    # final project string (either one of the dropdown values or the custom OTHER text)
    project = db.Column(db.String(255), nullable=False)

    # date the overtime happened (user picks)
    overtime_date = db.Column(db.Date, nullable=False)

    # duration as text (flexible, user enters whatever e.g. "3h", "2:30", "150 min")
    duration = db.Column(db.String(100), nullable=False)

    # when the submission was made (UTC)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def as_csv_row(self):
        return [
            self.user_name,
            self.project,
            self.overtime_date.isoformat(),
            self.duration,
            self.timestamp.isoformat(sep=" "),
        ]
        
class ExportMarker(db.Model):
    __tablename__ = "export_markers"
    key = db.Column(db.String(50), primary_key=True)   # "overtime_totals"
    last_export_utc = db.Column(db.DateTime, nullable=True)
    
class OvertimeTotal(db.Model):
    __tablename__ = "overtime_totals"

    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(255), nullable=False, unique=True)
    total_hours = db.Column(db.Float, nullable=False, default=0.0)


class ContractModel(db.Model):
    __tablename__ = "contract_models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    days = db.relationship(
        "ContractModelDay",
        backref="contract_model",
        cascade="all, delete-orphan",
        order_by="ContractModelDay.weekday",
    )
    assignments = db.relationship(
        "UserContractAssignment",
        backref="contract_model",
        cascade="all, delete-orphan",
    )


class ContractModelDay(db.Model):
    __tablename__ = "contract_model_days"
    __table_args__ = (
        db.UniqueConstraint("contract_model_id", "weekday", name="uq_contract_day"),
    )

    id = db.Column(db.Integer, primary_key=True)
    contract_model_id = db.Column(db.Integer, db.ForeignKey("contract_models.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # Monday = 0, Sunday = 6
    start_time = db.Column(db.String(5), nullable=True)
    end_time = db.Column(db.String(5), nullable=True)
    break_minutes = db.Column(db.Integer, default=0, nullable=False)
    expected_minutes = db.Column(db.Integer, default=0, nullable=False)
    is_flat_pay = db.Column(db.Boolean, default=False, nullable=False)


class UserContractAssignment(db.Model):
    __tablename__ = "user_contract_assignments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    contract_model_id = db.Column(db.Integer, db.ForeignKey("contract_models.id"), nullable=False)
    hourly_rate = db.Column(db.Float, default=0.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)

    user = db.relationship("User", backref="contract_assignments")


class ClockRecord(db.Model):
    __tablename__ = "clock_records"
    __table_args__ = (
        db.UniqueConstraint("user_id", "work_date", name="uq_clock_user_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    clock_in = db.Column(db.String(5), nullable=True)
    clock_out = db.Column(db.String(5), nullable=True)
    source = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", backref="clock_records")


class ClockExtraInstruction(db.Model):
    __tablename__ = "clock_extra_instructions"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    work_date = db.Column(db.Date, nullable=False)
    scope_type = db.Column(db.String(40), nullable=False)  # all, contract_model, user
    contract_model_id = db.Column(db.Integer, db.ForeignKey("contract_models.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user_ids = db.Column(db.Text, nullable=True)  # comma-separated ids for multi-user special cases
    extra_rate_per_hour = db.Column(db.Float, default=0.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    contract_model = db.relationship("ContractModel")
    user = db.relationship("User")
