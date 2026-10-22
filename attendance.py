"""Attendance verification.

This is the ONLY place that decides whether a student is present.
The browser sends a token and a location reading; everything else
(who the student is, whether the session is open, where the room is)
comes from the server.
"""
import math
import re
import sqlite3
from datetime import datetime, timezone

from geo import haversine_m

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MAX_LOGGED_REJECTIONS = 30  # per student per session, so the table cannot be spammed


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# code -> (HTTP status, message for the student, can the student try again?)
REASONS = {
    "BAD_REQUEST": (400, "The request was not understood. Reload the page and try again.", False),
    "INVALID_SESSION": (404, "This QR code is not a valid AcityMark attendance session.", False),
    "SESSION_CLOSED": (410, "The lecturer has closed this attendance session.", False),
    "EXPIRED": (410, "This QR code has expired. Ask your lecturer to start a new session.", False),
    "NOT_ENROLLED": (403, "You are not enrolled in this course. Contact your lecturer.", False),
    "ALREADY_MARKED": (409, "Your attendance for this session is already recorded.", False),
    "LOCATION_MISSING": (422, "No location was received. Allow location access, then try again.", True),
    "LOCATION_INVALID": (422, "The location reading was not valid. Try again.", True),
    "LOW_ACCURACY": (422, "Your location is too imprecise right now (about {accuracy} m). "
                          "Move near a window or outdoors, wait a few seconds, then try again.", True),
    "TOO_FAR": (422, "You appear to be about {distance} m from the lecture room. "
                     "The allowed distance is {radius} m.", True),
}

# Short labels shown in history and attendance lists.
REASON_LABELS = {
    "LOCATION_MISSING": "No location received",
    "LOCATION_INVALID": "Invalid location",
    "LOW_ACCURACY": "Location too imprecise",
    "TOO_FAR": "Too far from the room",
}


def _fail(code, **details):
    status, message, retry = REASONS[code]
    return status, {"ok": False, "code": code, "message": message.format(**details), "retry": retry}


def _number(value):
    """Accept real JSON numbers only (not strings, booleans, NaN or infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _log_rejection(db, session_id, student_id, code, distance=None, accuracy=None):
    count = db.execute(
        "SELECT COUNT(*) FROM attendance WHERE session_id = ? AND student_id = ? AND status = 'rejected'",
        (session_id, student_id),
    ).fetchone()[0]
    if count >= MAX_LOGGED_REJECTIONS:
        return
    db.execute(
        "INSERT INTO attendance (session_id, student_id, status, reason, distance_m, accuracy_m, submitted_at) "
        "VALUES (?, ?, 'rejected', ?, ?, ?, ?)",
        (session_id, student_id, code,
         None if distance is None else round(distance, 1),
         None if accuracy is None else round(accuracy, 1),
         to_iso(utcnow())),
    )
    db.commit()


def process_attendance(db, student_id, payload, max_accuracy_m):
    """Run every check. Returns (http_status, json_body). student_id comes from the login session."""
    if not isinstance(payload, dict):
        return _fail("BAD_REQUEST")

    # 2. The token must belong to a real session.
    token = payload.get("token")
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return _fail("INVALID_SESSION")
    sess = db.execute(
        "SELECT s.id, s.expires_at, s.closed_at, s.course_id, "
        "       c.code, c.room_lat, c.room_lng, c.radius_m "
        "FROM attendance_sessions s JOIN courses c ON c.id = s.course_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if sess is None or sess["room_lat"] is None or sess["room_lng"] is None:
        return _fail("INVALID_SESSION")

    # 3. Not closed, not expired (the clock is the server's clock).
    if sess["closed_at"]:
        return _fail("SESSION_CLOSED")
    if utcnow() >= parse_iso(sess["expires_at"]):
        return _fail("EXPIRED")

    # 4. The student must be enrolled in this course.
    enrolled = db.execute(
        "SELECT 1 FROM enrollments WHERE course_id = ? AND student_id = ?",
        (sess["course_id"], student_id),
    ).fetchone()
    if enrolled is None:
        return _fail("NOT_ENROLLED")

    # 7. No second approval (checked early so a student who is already present
    #    is told so even if they have since walked away).
    already = db.execute(
        "SELECT 1 FROM attendance WHERE session_id = ? AND student_id = ? AND status = 'approved'",
        (sess["id"], student_id),
    ).fetchone()
    if already is not None:
        return _fail("ALREADY_MARKED")

    # 5. A valid location reading.
    lat, lng, accuracy = (_number(payload.get(k)) for k in ("latitude", "longitude", "accuracy"))
    if payload.get("latitude") is None or payload.get("longitude") is None:
        return _fail("LOCATION_MISSING")
    if lat is None or lng is None or accuracy is None or accuracy < 0 \
            or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        _log_rejection(db, sess["id"], student_id, "LOCATION_INVALID")
        return _fail("LOCATION_INVALID")
    if accuracy > max_accuracy_m:
        _log_rejection(db, sess["id"], student_id, "LOW_ACCURACY", accuracy=accuracy)
        return _fail("LOW_ACCURACY", accuracy=round(accuracy))

    # 6. Within the allowed radius (Haversine, computed here on the server).
    distance = haversine_m(lat, lng, sess["room_lat"], sess["room_lng"])
    if distance > sess["radius_m"]:
        _log_rejection(db, sess["id"], student_id, "TOO_FAR", distance, accuracy)
        return _fail("TOO_FAR", distance=round(distance), radius=round(sess["radius_m"]))

    # All checks passed: record it. The unique index stops races and double taps.
    try:
        db.execute(
            "INSERT INTO attendance (session_id, student_id, status, reason, distance_m, accuracy_m, submitted_at) "
            "VALUES (?, ?, 'approved', NULL, ?, ?, ?)",
            (sess["id"], student_id, round(distance, 1), round(accuracy, 1), to_iso(utcnow())),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return _fail("ALREADY_MARKED")

    return 200, {
        "ok": True,
        "code": "APPROVED",
        "message": f"Attendance recorded for {sess['code']}.",
        "distance_m": round(distance),
    }
