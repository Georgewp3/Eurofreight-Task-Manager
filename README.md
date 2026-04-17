Task Management System

A small Flask application for assigning tasks to users, collecting daily status updates, scheduling recurring tasks, and exporting an audit trail. Timestamps are stored in UTC and displayed in Europe/Nicosia.
Available at: https://test-3-1-rosb.onrender.com/

All the functions are available through the password-protected Admin tab. To view the whole app contact me: giorgospapageorgiou043@gmail.com

Tech stack

Backend: Python 3.13, Flask 3, SQLAlchemy ORM

Database (prod): PostgreSQL (psycopg driver). Local dev can use SQLite.

Templates/UI: Jinja2, vanilla JS + fetch, Chart.js (insights), custom CSS

Scheduling: Render Cron hitting a token-protected endpoint

Time/Locale: zoneinfo for Europe/Nicosia, CSV/date formatting server-side

Data model (simplified)

User: id, full_name, active

Task: id, user_id → User, project, title, created_at

LogEntry: id, user_name, project, task_title, status (COMPLETED / NOT_COMPLETED), comment, timestamp (UTC)

ScheduledTask: id, user_id → User, title, project, weekdays ("MON,TUE,…"), time_local ("HH:MM"), tz, active, last_run_date

Notes:

LogEntry is denormalized on purpose (user_name, task_title stored as strings) to simplify exports and preserve history even if users or tasks are later modified or deleted.

All timestamps persist in UTC; rendering and CSV export convert to Europe/Nicosia using zoneinfo.

Features by role
User (Employee)

User dashboard (/):

Select your name to load only your assigned tasks.

Mark a task as Done or Not done.

Optionally add a comment when not done (reason/notes).

UI auto-refreshes assigned tasks while a user is selected.

Admin

Admin login (/admin/login):

Single admin password gate (see ENV config below).

Admin panel (/admin):

User management: add user, remove user (cascades tasks).

Task management: add per-user tasks (title + optional project), remove task.

Search-as-you-type filter to quickly find users.

Data Bank (/admin/data-bank):

View the full submission log with status and comments.

Export CSV (Excel-ready) with timestamps formatted as DD/MM/YYYY - HH:MM:SS in Europe/Nicosia.

Clear the data bank (hard reset) via a confirm-protected action.

Scheduling (/admin/schedules):

Create recurring tasks by selecting user, project, title.

Choose weekdays (MON–SUN) and a local time (Europe/Nicosia).

See existing schedules, last run date, and delete schedules.

Background job (Render Cron) materializes scheduled tasks into each user’s task list at/after the specified local time; guarded to avoid duplicates (tracks last_run_date per schedule).

Insights (/admin/insights):

KPIs: Total submissions, Completed, Completion rate, “Not Done with comment”, Last 7-day completion rate.

Charts (Chart.js):

Activity (last 30 days)

Top users by completions (last 30 days)

Top projects by completions (last 30 days)

Security & configuration

Environment variables (Render → Environment; .env for local dev):

ADMIN_PASSWORD — password for /admin/login.

SECRET_KEY — Flask session signing key.

DATABASE_URL — SQLAlchemy connection string.

Postgres example (Render): postgresql+psycopg://USER:PASS@HOST:PORT/DBNAME?sslmode=require

Local SQLite example: sqlite:///task_db.sqlite3

SCHEDULE_TOKEN — secret token required by the scheduler endpoint.

The scheduler endpoint (POST /internal/run-schedules) requires header:

X-TaskApp-Token: <SCHEDULE_TOKEN>

Key routes (summary)

User UI

GET / — user dashboard

POST /submit — submit entry (Done/Not done + optional comment)

Admin UI

GET /admin/login / POST /admin/login — admin auth

GET /admin — admin panel (users/tasks & search)

POST /admin/add-user — add user

POST /admin/remove-user/<user_id> — remove user (+tasks)

POST /admin/add-task/<user_id> — add task to user

POST /admin/remove-task/<task_id> — remove task

Data Bank

GET /admin/data-bank — view logs

GET /admin/data-bank/export — CSV export

POST /admin/data-bank/clear — clear all logs

Scheduling

GET /admin/schedules — create/manage schedules

POST /admin/schedules/add — add schedule

POST /admin/schedules/delete/<sid> — delete schedule

POST /internal/run-schedules — cron target (requires X-TaskApp-Token)

Insights

GET /admin/insights — charts & KPIs page

GET /admin/insights/data — JSON data for the insights charts

Deployment notes

Production DB: Use PostgreSQL (Render Postgres). The app auto-detects the dialect from DATABASE_URL.

Cron: Add a Render Cron Job to POST /internal/run-schedules every minute (or at your preferred cadence) with the header X-TaskApp-Token: <SCHEDULE_TOKEN>.

Time zone: All displayed times and CSV export use Europe/Nicosia; storage remains UTC.

CSV export format

Columns: User, Project, Task, Status, Timestamp, Comment

Timestamp is formatted DD/MM/YYYY - HH:MM:SS in Europe/Nicosia.

Suitable for Excel and BI tools.
