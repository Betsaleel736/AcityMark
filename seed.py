"""Optional: fill the database with demo accounts so you can try AcityMark straight away.

    python seed.py

Everything here is DEMO DATA. The room coordinates are a placeholder in Accra:
replace them with your real lecture room on the course page before testing.
Do not use these passwords anywhere real.
"""
from werkzeug.security import generate_password_hash

from app import app
from attendance import to_iso, utcnow
from db import get_db

PASSWORD = "password123"

with app.app_context():
    db = get_db()
    now = to_iso(utcnow())
    if db.execute("SELECT 1 FROM users WHERE email = 'lecturer@demo.test'").fetchone():
        print("Demo data already exists.")
        raise SystemExit
    pw = generate_password_hash(PASSWORD)
    cur = db.execute("INSERT INTO users (full_name, email, student_id, role, password_hash, created_at) "
                     "VALUES ('Dr Demo Lecturer', 'lecturer@demo.test', NULL, 'lecturer', ?, ?)", (pw, now))
    lecturer_id = cur.lastrowid
    student_ids = []
    for i, name in enumerate(["Ama Demo", "Kofi Demo", "Esi Demo"], start=1):
        cur = db.execute("INSERT INTO users (full_name, email, student_id, role, password_hash, created_at) "
                         "VALUES (?, ?, ?, 'student', ?, ?)", (name, f"student{i}@demo.test", f"DEMO{i:03d}", pw, now))
        student_ids.append(cur.lastrowid)
    cur = db.execute("INSERT INTO courses (lecturer_id, code, title, room_name, room_lat, room_lng, radius_m, created_at) "
                     "VALUES (?, 'CS 201', 'Data Structures', 'Demo Hall (placeholder location)', 5.6037, -0.1870, ?, ?)",
                     (lecturer_id, app.config["ALLOWED_RADIUS_M"], now))
    for sid in student_ids:
        db.execute("INSERT INTO enrollments (course_id, student_id, enrolled_at) VALUES (?, ?, ?)", (cur.lastrowid, sid, now))
    db.commit()
    print("Created demo data.")
    print("  Lecturer: lecturer@demo.test /", PASSWORD)
    print("  Students: student1@demo.test, student2@demo.test, student3@demo.test /", PASSWORD)
