from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# --- Tables ---

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True)
    tasks = db.relationship("Task", backref="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.full_name}>"

class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    project = db.Column(db.String(120), default="-")
    title = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Task {self.title} for {self.user_id}>"

class LogEntry(db.Model):
    """
    Data Bank rows — a submission from a user.
    """
    __tablename__ = "log_entries"
    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(120), nullable=False)    # denormalized for simple CSV
    project = db.Column(db.String(120), default="-")
    task_title = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False)        # COMPLETED or NOT_COMPLETED
    comment = db.Column(db.Text, default="")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def as_csv_row(self):
        return [self.user_name, self.project, self.task_title, self.status, self.timestamp.isoformat(), self.comment or ""]
