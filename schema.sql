CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    student_id    TEXT UNIQUE,
    role          TEXT NOT NULL CHECK (role IN ('lecturer', 'student')),
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lecturer_id INTEGER NOT NULL REFERENCES users(id),
    code        TEXT NOT NULL,
    title       TEXT NOT NULL,
    room_name   TEXT,
    room_lat    REAL,
    room_lng    REAL,
    radius_m    REAL NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (lecturer_id, code)
);

CREATE TABLE IF NOT EXISTS enrollments (
    course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    enrolled_at TEXT NOT NULL,
    PRIMARY KEY (course_id, student_id)
);

CREATE TABLE IF NOT EXISTS attendance_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    token      TEXT NOT NULL UNIQUE,
    starts_at  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    closed_at  TEXT
);

-- One row per attempt. We store only the distance and the reported accuracy,
-- never the student's raw coordinates.
CREATE TABLE IF NOT EXISTS attendance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    student_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    reason       TEXT,
    distance_m   REAL,
    accuracy_m   REAL,
    submitted_at TEXT NOT NULL
);

-- The database itself refuses a second approved record for the same student and session.
CREATE UNIQUE INDEX IF NOT EXISTS one_approved_per_student_session
    ON attendance (session_id, student_id) WHERE status = 'approved';

CREATE INDEX IF NOT EXISTS attendance_by_student ON attendance (student_id, submitted_at);
