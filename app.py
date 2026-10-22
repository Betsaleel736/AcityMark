"""AcityMark: QR attendance with a location check. Run with:  python app.py"""
import argparse
import csv
import hmac
import io
import re
import secrets
import sqlite3
import time
from datetime import timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from attendance import REASON_LABELS, parse_iso, process_attendance, to_iso, utcnow
from config import Config
from db import close_db, get_db, init_db

app = Flask(__name__)
app.config.from_object(Config)
app.teardown_appcontext(close_db)
with app.app_context():
    init_db()

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9/-]{3,20}$")
COURSE_CODE_RE = re.compile(r"^[A-Za-z0-9 -]{2,20}$")
DUMMY_HASH = generate_password_hash("not-a-real-password")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def field(name, max_len=200):
    return (request.form.get(name) or "").strip()[:max_len]


def number(text, low, high):
    """Parse a float from a form field, or return None if invalid/out of range."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or not (low <= value <= high):  # NaN or out of range
        return None
    return value


def safe_next(target):
    """Only allow redirects to paths on this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


def session_status(row):
    if row["closed_at"]:
        return "closed"
    if utcnow() >= parse_iso(row["expires_at"]):
        return "expired"
    return "active"


def seconds_left(row):
    if session_status(row) != "active":
        return 0
    return max(0, int((parse_iso(row["expires_at"]) - utcnow()).total_seconds()))


def public_base():
    return (app.config["PUBLIC_BASE_URL"] or request.host_url).rstrip("/")


@app.template_filter("dt")
def dt_filter(value):
    """Render a UTC timestamp; app.js turns it into the viewer's local time."""
    if not value:
        return ""
    text = str(value).replace("T", " ").replace("Z", " UTC")
    return Markup('<time class="local-time" datetime="%s">%s</time>') % (escape(value), escape(text))


app.jinja_env.globals.update(
    session_status=session_status,
    reason_label=lambda code: REASON_LABELS.get(code, "Not approved"),
)


# --------------------------------------------------------------------------
# Authentication, CSRF and security headers
# --------------------------------------------------------------------------
@app.before_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT id, full_name, email, student_id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if g.user is None:
            session.clear()


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            abort(400)


@app.context_processor
def inject_csrf():
    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return session["csrf_token"]
    return {"csrf_token": csrf_token}


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), geolocation=(self)"
    return response


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            is_api = request.path.startswith("/api/")
            if g.user is None:
                if is_api:
                    return jsonify(ok=False, code="NOT_AUTHENTICATED",
                                   message="Please log in again."), 401
                return redirect(url_for("login", next=request.full_path.rstrip("?")))
            if role and g.user["role"] != role:
                if is_api:
                    return jsonify(ok=False, code="FORBIDDEN",
                                   message="Only student accounts can mark attendance."), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


ERROR_TEXT = {
    400: ("Request not accepted", "Your page expired or the request was incomplete. Go back, reload the page and try again."),
    403: ("Not allowed", "Your account does not have access to this page."),
    404: ("Page not found", "That page does not exist, or it belongs to another account."),
    405: ("Not allowed", "That action is not available here."),
}


@app.errorhandler(HTTPException)
def http_error(err):
    title, message = ERROR_TEXT.get(err.code, (err.name, "Something went wrong."))
    if request.path.startswith("/api/"):
        return jsonify(ok=False, code="ERROR", message=message), err.code
    return render_template("error.html", code=err.code, title=title, message=message), err.code


# Very small in-memory brake on password guessing (resets when the server restarts).
_failed_logins = {}


def too_many_attempts(key):
    now = time.time()
    recent = [t for t in _failed_logins.get(key, []) if now - t < 300]
    _failed_logins[key] = recent
    return len(recent) >= 5


# --------------------------------------------------------------------------
# Public pages: home, register, login, logout
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    errors, values = [], {}
    if request.method == "POST":
        values = {k: field(k, 120) for k in ("full_name", "email", "student_id", "role")}
        password = request.form.get("password", "")
        role = values["role"]
        email = values["email"].lower()
        if not 2 <= len(values["full_name"]) <= 100:
            errors.append("Enter your full name (2 to 100 characters).")
        if not EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        if role not in ("student", "lecturer"):
            errors.append("Choose whether you are a student or a lecturer.")
        if role == "student" and not STUDENT_ID_RE.match(values["student_id"]):
            errors.append("Enter your student ID (3 to 20 letters, numbers, / or -).")
        if role == "lecturer" and not hmac.compare_digest(
                field("invite_code").encode(), app.config["LECTURER_INVITE_CODE"].encode()):
            errors.append("The lecturer invite code is not correct.")
        if len(password) < 8:
            errors.append("Use a password of at least 8 characters.")
        if password != request.form.get("confirm", ""):
            errors.append("The two passwords do not match.")
        if not errors:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (full_name, email, student_id, role, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (values["full_name"], email,
                     values["student_id"] if role == "student" else None,
                     role, generate_password_hash(password), to_iso(utcnow())),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("An account with that email or student ID already exists.")
            else:
                flash("Account created. You can log in now.", "success")
                return redirect(url_for("login"))
    return render_template("register.html", errors=errors, values=values)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = field("email", 255).lower()
        password = request.form.get("password", "")
        key = (email, request.remote_addr)
        if too_many_attempts(key):
            error = "Too many failed attempts. Wait five minutes and try again."
        else:
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            # Always run one hash check so response time does not reveal whether the email exists.
            valid = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
            if user and valid:
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(16)
                return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
            _failed_logins.setdefault(key, []).append(time.time())
            error = "Email or password is incorrect."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required()
def dashboard():
    return redirect(url_for("lecturer_home" if g.user["role"] == "lecturer" else "student_home"))


# --------------------------------------------------------------------------
# Lecturer: statistics and ownership helpers
# --------------------------------------------------------------------------
def attendance_stats(db, lecturer_id, course_id=None):
    """Totals for a lecturer (optionally one course).
    A student is only counted as absent for sessions held after they were enrolled.
    The f-strings below only splice in fixed text; all values go through ? placeholders."""
    where, args = "c.lecturer_id = ?", [lecturer_id]
    if course_id is not None:
        where += " AND c.id = ?"
        args.append(course_id)
    students = db.execute(
        f"SELECT COUNT(DISTINCT e.student_id) FROM enrollments e JOIN courses c ON c.id = e.course_id WHERE {where}",
        args).fetchone()[0]
    slots = db.execute(
        f"SELECT COUNT(*) FROM enrollments e JOIN courses c ON c.id = e.course_id "
        f"JOIN attendance_sessions s ON s.course_id = c.id AND s.starts_at >= e.enrolled_at WHERE {where}",
        args).fetchone()[0]
    present = db.execute(
        f"SELECT COUNT(*) FROM attendance a JOIN attendance_sessions s ON s.id = a.session_id "
        f"JOIN courses c ON c.id = s.course_id "
        f"JOIN enrollments e ON e.course_id = c.id AND e.student_id = a.student_id "
        f"WHERE a.status = 'approved' AND {where}", args).fetchone()[0]
    return {
        "students": students,
        "present": present,
        "absent": max(slots - present, 0),
        "rate": round(100 * present / slots) if slots else 0,
    }


def owned_course(course_id):
    course = get_db().execute(
        "SELECT * FROM courses WHERE id = ? AND lecturer_id = ?", (course_id, g.user["id"])).fetchone()
    if course is None:
        abort(404)
    return course


def owned_session(session_id):
    row = get_db().execute(
        "SELECT s.*, c.code, c.title, c.room_name, c.radius_m, c.lecturer_id "
        "FROM attendance_sessions s JOIN courses c ON c.id = s.course_id "
        "WHERE s.id = ? AND c.lecturer_id = ?", (session_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


def roster_for(session_row):
    return get_db().execute(
        "SELECT u.id, u.full_name, u.email, u.student_id, "
        " (SELECT a.submitted_at FROM attendance a WHERE a.session_id = :sid AND a.student_id = u.id AND a.status = 'approved') AS present_at, "
        " (SELECT a.distance_m FROM attendance a WHERE a.session_id = :sid AND a.student_id = u.id AND a.status = 'approved') AS distance_m, "
        " (SELECT COUNT(*) FROM attendance a WHERE a.session_id = :sid AND a.student_id = u.id AND a.status = 'rejected') AS rejected_count, "
        " (SELECT a.reason FROM attendance a WHERE a.session_id = :sid AND a.student_id = u.id AND a.status = 'rejected' ORDER BY a.id DESC LIMIT 1) AS last_reject "
        "FROM enrollments e JOIN users u ON u.id = e.student_id WHERE e.course_id = :cid ORDER BY u.full_name",
        {"sid": session_row["id"], "cid": session_row["course_id"]}).fetchall()


def roster_stats(roster):
    present = sum(1 for r in roster if r["present_at"])
    total = len(roster)
    return {"students": total, "present": present, "absent": total - present,
            "rate": round(100 * present / total) if total else 0}


# --------------------------------------------------------------------------
# Lecturer pages
# --------------------------------------------------------------------------
@app.route("/lecturer")
@login_required("lecturer")
def lecturer_home():
    db = get_db()
    courses = db.execute(
        "SELECT c.*, "
        " (SELECT COUNT(*) FROM enrollments e WHERE e.course_id = c.id) AS students, "
        " (SELECT COUNT(*) FROM attendance_sessions s WHERE s.course_id = c.id) AS sessions, "
        " (SELECT s.id FROM attendance_sessions s WHERE s.course_id = c.id AND s.closed_at IS NULL "
        "    AND s.expires_at > ? ORDER BY s.id DESC LIMIT 1) AS active_session_id "
        "FROM courses c WHERE c.lecturer_id = ? ORDER BY c.code",
        (to_iso(utcnow()), g.user["id"])).fetchall()
    return render_template("lecturer_home.html", courses=courses,
                           stats=attendance_stats(db, g.user["id"]),
                           default_radius=app.config["ALLOWED_RADIUS_M"])


@app.post("/lecturer/courses")
@login_required("lecturer")
def course_create():
    code, title = field("code", 20).upper(), field("title", 120)
    if not COURSE_CODE_RE.match(code) or not 2 <= len(title) <= 120:
        flash("Enter a course code (for example CS 201) and a title.", "error")
        return redirect(url_for("lecturer_home"))
    try:
        db = get_db()
        cur = db.execute(
            "INSERT INTO courses (lecturer_id, code, title, radius_m, created_at) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], code, title, app.config["ALLOWED_RADIUS_M"], to_iso(utcnow())))
        db.commit()
    except sqlite3.IntegrityError:
        flash(f"You already have a course with the code {code}.", "error")
        return redirect(url_for("lecturer_home"))
    flash("Course created. Next, register the lecture room.", "success")
    return redirect(url_for("course_detail", course_id=cur.lastrowid))


@app.route("/lecturer/courses/<int:course_id>")
@login_required("lecturer")
def course_detail(course_id):
    db = get_db()
    course = owned_course(course_id)
    students = db.execute(
        "SELECT u.id, u.full_name, u.email, u.student_id FROM enrollments e "
        "JOIN users u ON u.id = e.student_id WHERE e.course_id = ? ORDER BY u.full_name", (course_id,)).fetchall()
    sessions = db.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.id AND a.status = 'approved') AS present "
        "FROM attendance_sessions s WHERE s.course_id = ? ORDER BY s.id DESC LIMIT 30", (course_id,)).fetchall()
    return render_template("course_detail.html", course=course, students=students, sessions=sessions,
                           stats=attendance_stats(db, g.user["id"], course_id),
                           default_minutes=app.config["DEFAULT_SESSION_MINUTES"],
                           max_minutes=app.config["MAX_SESSION_MINUTES"])


@app.post("/lecturer/courses/<int:course_id>/room")
@login_required("lecturer")
def course_room(course_id):
    owned_course(course_id)
    lat, lng = number(request.form.get("latitude"), -90, 90), number(request.form.get("longitude"), -180, 180)
    radius = number(request.form.get("radius_m"), 10, 500)
    room_name = field("room_name", 80)
    if lat is None or lng is None or radius is None or not room_name:
        flash("Enter a room name, a latitude (-90 to 90), a longitude (-180 to 180) and a radius of 10 to 500 metres.", "error")
    else:
        db = get_db()
        db.execute("UPDATE courses SET room_name = ?, room_lat = ?, room_lng = ?, radius_m = ? WHERE id = ?",
                   (room_name, lat, lng, radius, course_id))
        db.commit()
        flash("Lecture room saved.", "success")
    return redirect(url_for("course_detail", course_id=course_id))


@app.post("/lecturer/courses/<int:course_id>/enroll")
@login_required("lecturer")
def course_enroll(course_id):
    owned_course(course_id)
    identifiers = [x.lower() for x in re.split(r"[\s,;]+", request.form.get("students", "")) if x][:500]
    if not identifiers:
        flash("Enter at least one student email or student ID.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    db, added, existing, missing = get_db(), 0, 0, []
    for ident in dict.fromkeys(identifiers):
        student = db.execute(
            "SELECT id FROM users WHERE role = 'student' AND (lower(email) = ? OR lower(student_id) = ?)",
            (ident, ident)).fetchone()
        if student is None:
            missing.append(ident)
            continue
        cur = db.execute("INSERT OR IGNORE INTO enrollments (course_id, student_id, enrolled_at) VALUES (?, ?, ?)",
                         (course_id, student["id"], to_iso(utcnow())))
        added, existing = added + cur.rowcount, existing + (1 - cur.rowcount)
    db.commit()
    if added:
        flash(f"Enrolled {added} student{'s' if added != 1 else ''}.", "success")
    if existing:
        flash(f"{existing} already enrolled.", "warning")
    if missing:
        flash("No student account found for: " + ", ".join(missing[:10]) + (" and more" if len(missing) > 10 else "")
              + ". Students must register first.", "warning")
    return redirect(url_for("course_detail", course_id=course_id))


@app.post("/lecturer/courses/<int:course_id>/unenroll/<int:student_id>")
@login_required("lecturer")
def course_unenroll(course_id, student_id):
    owned_course(course_id)
    db = get_db()
    db.execute("DELETE FROM enrollments WHERE course_id = ? AND student_id = ?", (course_id, student_id))
    db.commit()
    flash("Student removed from the course.", "success")
    return redirect(url_for("course_detail", course_id=course_id))


@app.post("/lecturer/courses/<int:course_id>/sessions")
@login_required("lecturer")
def session_start(course_id):
    course = owned_course(course_id)
    if course["room_lat"] is None:
        flash("Register the lecture room before starting a session.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    minutes = number(request.form.get("minutes"), 1, app.config["MAX_SESSION_MINUTES"])
    if minutes is None:
        flash(f"Choose a duration between 1 and {app.config['MAX_SESSION_MINUTES']} minutes.", "error")
        return redirect(url_for("course_detail", course_id=course_id))
    now = utcnow()
    db = get_db()
    # One open session per course: close any earlier one so old QR codes stop working.
    db.execute("UPDATE attendance_sessions SET closed_at = ? WHERE course_id = ? AND closed_at IS NULL",
               (to_iso(now), course_id))
    cur = db.execute(
        "INSERT INTO attendance_sessions (course_id, token, starts_at, expires_at) VALUES (?, ?, ?, ?)",
        (course_id, secrets.token_urlsafe(24), to_iso(now),
         to_iso(now + timedelta(minutes=int(minutes)))))
    db.commit()
    return redirect(url_for("session_view", session_id=cur.lastrowid))


@app.route("/lecturer/sessions/<int:session_id>")
@login_required("lecturer")
def session_view(session_id):
    sess = owned_session(session_id)
    roster = roster_for(sess)
    base = public_base()
    return render_template(
        "session_view.html", s=sess, roster=roster, stats=roster_stats(roster),
        attend_url=f"{base}/attend/{sess['token']}",
        local_only=any(h in base for h in ("localhost", "127.0.0.1")),
        seconds_left=seconds_left(sess))


@app.route("/lecturer/sessions/<int:session_id>/live")
@login_required("lecturer")
def session_live(session_id):
    sess = owned_session(session_id)
    roster = roster_for(sess)
    return jsonify(stats=roster_stats(roster), status=session_status(sess), seconds_left=seconds_left(sess),
                   rows_html=render_template("_roster_rows.html", roster=roster))


@app.route("/lecturer/sessions/<int:session_id>/qr.svg")
@login_required("lecturer")
def session_qr(session_id):
    import qrcode
    import qrcode.image.svg
    sess = owned_session(session_id)
    image = qrcode.make(f"{public_base()}/attend/{sess['token']}",
                        image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    image.save(buf)
    response = Response(buf.getvalue(), mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.post("/lecturer/sessions/<int:session_id>/close")
@login_required("lecturer")
def session_close(session_id):
    sess = owned_session(session_id)
    db = get_db()
    db.execute("UPDATE attendance_sessions SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
               (to_iso(utcnow()), session_id))
    db.commit()
    flash(f"Session for {sess['code']} closed. The QR code no longer works.", "success")
    return redirect(url_for("session_view", session_id=session_id))


def csv_cell(value):
    """Stop spreadsheet apps from running text that starts with = + - @ as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_response(rows, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow([csv_cell(c) for c in row])
    response = Response("\ufeff" + buf.getvalue(), mimetype="text/csv")  # BOM so Excel reads UTF-8
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.route("/lecturer/sessions/<int:session_id>/export.csv")
@login_required("lecturer")
def session_export(session_id):
    sess = owned_session(session_id)
    rows = [["Student ID", "Full name", "Email", "Status", "Marked at (UTC)", "Distance from room (m)"]]
    for r in roster_for(sess):
        rows.append([r["student_id"] or "", r["full_name"], r["email"],
                     "Present" if r["present_at"] else "Absent",
                     r["present_at"] or "", "" if r["distance_m"] is None else r["distance_m"]])
    code = re.sub(r"[^A-Za-z0-9]+", "_", sess["code"])
    return csv_response(rows, f"attendance_{code}_{sess['starts_at'][:10]}_session{sess['id']}.csv")


@app.route("/lecturer/courses/<int:course_id>/export.csv")
@login_required("lecturer")
def course_export(course_id):
    course = owned_course(course_id)
    db = get_db()
    sessions = db.execute("SELECT id, starts_at FROM attendance_sessions WHERE course_id = ? ORDER BY id",
                          (course_id,)).fetchall()
    students = db.execute(
        "SELECT u.id, u.full_name, u.email, u.student_id FROM enrollments e "
        "JOIN users u ON u.id = e.student_id WHERE e.course_id = ? ORDER BY u.full_name", (course_id,)).fetchall()
    present = {(r["session_id"], r["student_id"]) for r in db.execute(
        "SELECT a.session_id, a.student_id FROM attendance a JOIN attendance_sessions s ON s.id = a.session_id "
        "WHERE s.course_id = ? AND a.status = 'approved'", (course_id,))}
    rows = [["Student ID", "Full name", "Email"] + [s["starts_at"].replace("T", " ").rstrip("Z") for s in sessions]
            + ["Sessions attended", "Sessions held", "Attendance rate (%)"]]
    for st in students:
        marks = [(s["id"], st["id"]) in present for s in sessions]
        attended = sum(marks)
        rows.append([st["student_id"] or "", st["full_name"], st["email"]]
                    + ["Present" if m else "Absent" for m in marks]
                    + [attended, len(sessions), round(100 * attended / len(sessions)) if sessions else 0])
    code = re.sub(r"[^A-Za-z0-9]+", "_", course["code"])
    return csv_response(rows, f"attendance_{code}_all_sessions.csv")


# --------------------------------------------------------------------------
# Student pages and the attendance API
# --------------------------------------------------------------------------
@app.route("/student")
@login_required("student")
def student_home():
    db = get_db()
    courses = db.execute(
        "SELECT c.id, c.code, c.title, u.full_name AS lecturer, "
        " (SELECT COUNT(*) FROM attendance_sessions s WHERE s.course_id = c.id AND s.starts_at >= e.enrolled_at) AS held, "
        " (SELECT COUNT(*) FROM attendance a JOIN attendance_sessions s ON s.id = a.session_id "
        "   WHERE s.course_id = c.id AND a.student_id = e.student_id AND a.status = 'approved') AS attended "
        "FROM enrollments e JOIN courses c ON c.id = e.course_id JOIN users u ON u.id = c.lecturer_id "
        "WHERE e.student_id = ? ORDER BY c.code", (g.user["id"],)).fetchall()
    held = sum(c["held"] for c in courses)
    attended = sum(min(c["attended"], c["held"]) for c in courses)
    stats = {"courses": len(courses), "present": attended, "absent": max(held - attended, 0),
             "rate": round(100 * attended / held) if held else 0}
    return render_template("student_home.html", courses=courses, stats=stats)


@app.route("/student/scan")
@login_required("student")
def student_scan():
    return render_template("scan.html")


@app.route("/attend/<token>")
@login_required("student")
def attend(token):
    sess = get_db().execute(
        "SELECT s.expires_at, s.closed_at, c.code, c.title, c.room_name FROM attendance_sessions s "
        "JOIN courses c ON c.id = s.course_id WHERE s.token = ?", (token,)).fetchone()
    if sess is None:
        return render_template("attend.html", sess=None, token=token, state="invalid"), 404
    return render_template("attend.html", sess=sess, token=token, state=session_status(sess))


@app.post("/api/attendance")
@login_required("student")
def api_attendance():
    # The student's identity comes from the login session, never from the request body.
    status, body = process_attendance(get_db(), g.user["id"], request.get_json(silent=True),
                                      app.config["MAX_ACCURACY_M"])
    return jsonify(body), status


@app.route("/student/history")
@login_required("student")
def student_history():
    records = get_db().execute(
        "SELECT a.status, a.reason, a.distance_m, a.submitted_at, c.code, c.title "
        "FROM attendance a JOIN attendance_sessions s ON s.id = a.session_id JOIN courses c ON c.id = s.course_id "
        "WHERE a.student_id = ? ORDER BY a.id DESC LIMIT 200", (g.user["id"],)).fetchall()
    return render_template("history.html", records=records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AcityMark")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to let phones on your Wi-Fi connect")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--https", action="store_true", help="temporary HTTPS certificate (pip install cryptography)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, ssl_context="adhoc" if args.https else None)
