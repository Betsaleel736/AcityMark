"""Run with:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Use a throwaway database and key for tests.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["SECRET_KEY"] = "test-secret"

try:
    import qrcode  # noqa: F401
except ImportError:  # lets the tests run where the qrcode package is not installed
    stub = types.ModuleType("qrcode")
    stub_image = types.ModuleType("qrcode.image")
    stub_svg = types.ModuleType("qrcode.image.svg")
    stub_svg.SvgPathImage = object

    class _FakeImage:
        def save(self, buf):
            buf.write(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    stub.make = lambda data, **kw: _FakeImage()
    stub.image = stub_image
    stub_image.svg = stub_svg
    sys.modules.update({"qrcode": stub, "qrcode.image": stub_image, "qrcode.image.svg": stub_svg})

import app as acity  # noqa: E402
from attendance import to_iso, utcnow  # noqa: E402
from db import get_db  # noqa: E402
from geo import haversine_m  # noqa: E402

ROOM = (5.6500, -0.1860)
NEAR = (5.6502, -0.1860)   # about 22 m north of the room
FAR = (5.6510, -0.1860)    # about 111 m north of the room


class HaversineTests(unittest.TestCase):
    def test_zero_distance(self):
        self.assertAlmostEqual(haversine_m(*ROOM, *ROOM), 0, places=6)

    def test_one_degree_latitude_is_about_111_km(self):
        self.assertAlmostEqual(haversine_m(0, 0, 1, 0), 111_195, delta=100)

    def test_known_short_distance(self):
        self.assertAlmostEqual(haversine_m(*ROOM, *NEAR), 22.2, delta=0.5)


class AppTests(unittest.TestCase):
    def setUp(self):
        acity.app.config["TESTING"] = True
        with acity.app.app_context():
            conn = get_db()
            for table in ("attendance", "attendance_sessions", "enrollments", "courses", "users"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence")  # restart ids at 1 for every test
            conn.commit()
        acity._failed_logins.clear()
        self.lect = self.client()
        self.stud = self.client()
        self.register(self.lect, "lecturer", "Dr Mensah", "mensah@acity.test", invite=True)
        self.register(self.stud, "student", "Ama Boateng", "ama@acity.test", student_id="ACU1001")
        self.login(self.lect, "mensah@acity.test")
        self.login(self.stud, "ama@acity.test")
        self.course_id = self.make_course(self.lect)
        self.enroll(self.lect, self.course_id, "ama@acity.test")
        self.token = self.start_session(self.lect, self.course_id)

    # ---- helpers ----
    def client(self):
        return acity.app.test_client()

    def csrf(self, client):
        client.get("/login")
        with client.session_transaction() as s:
            return s["csrf_token"]

    def post(self, client, url, data=None):
        data = dict(data or {})
        data["csrf_token"] = self.csrf(client)
        return client.post(url, data=data)

    def register(self, client, role, name, email, student_id="", invite=False):
        return self.post(client, "/register", {
            "role": role, "full_name": name, "email": email, "student_id": student_id,
            "password": "correct-horse", "confirm": "correct-horse",
            "invite_code": acity.app.config["LECTURER_INVITE_CODE"] if invite else ""})

    def login(self, client, email, password="correct-horse"):
        return self.post(client, "/login", {"email": email, "password": password})

    def make_course(self, client, radius=50):
        r = self.post(client, "/lecturer/courses", {"code": "cs 201", "title": "Data Structures"})
        cid = int(r.headers["Location"].rsplit("/", 1)[1])
        self.post(client, f"/lecturer/courses/{cid}/room", {
            "room_name": "Hall B", "latitude": ROOM[0], "longitude": ROOM[1], "radius_m": radius})
        return cid

    def enroll(self, client, cid, who):
        return self.post(client, f"/lecturer/courses/{cid}/enroll", {"students": who})

    def start_session(self, client, cid, minutes=10):
        r = self.post(client, f"/lecturer/courses/{cid}/sessions", {"minutes": minutes})
        sid = int(r.headers["Location"].rsplit("/", 1)[1])
        with acity.app.app_context():
            return get_db().execute("SELECT token FROM attendance_sessions WHERE id = ?", (sid,)).fetchone()["token"]

    def attend(self, client, token=None, loc=NEAR, accuracy=10.0, **extra):
        body = {"token": token or self.token, "latitude": loc[0], "longitude": loc[1], "accuracy": accuracy}
        body.update(extra)
        return client.post("/api/attendance", json=body, headers={"X-CSRF-Token": self.csrf(client)})

    # ---- authentication ----
    def test_passwords_are_hashed(self):
        with acity.app.app_context():
            row = get_db().execute("SELECT password_hash FROM users WHERE email = 'ama@acity.test'").fetchone()
        self.assertNotIn("correct-horse", row["password_hash"])
        self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_wrong_password_is_rejected(self):
        c = self.client()
        r = self.login(c, "ama@acity.test", "wrong-password")
        self.assertIn(b"incorrect", r.data)
        self.assertEqual(c.get("/student").status_code, 302)

    def test_sql_injection_in_login_does_nothing(self):
        c = self.client()
        r = self.login(c, "' OR 1=1 --", "x")
        self.assertIn(b"incorrect", r.data)

    def test_lecturer_cannot_register_without_invite_code(self):
        c = self.client()
        r = self.register(c, "lecturer", "Sneaky", "sneaky@acity.test", invite=False)
        self.assertIn(b"invite code", r.data)

    def test_login_locks_after_repeated_failures(self):
        c = self.client()
        for _ in range(5):
            self.login(c, "ama@acity.test", "nope")
        r = self.login(c, "ama@acity.test", "correct-horse")
        self.assertIn(b"Too many failed attempts", r.data)

    def test_logout_ends_session(self):
        self.post(self.stud, "/logout")
        self.assertEqual(self.stud.get("/student").status_code, 302)

    def test_post_without_csrf_token_is_refused(self):
        self.assertEqual(self.client().post("/login", data={"email": "a", "password": "b"}).status_code, 400)

    # ---- role-based access ----
    def test_student_cannot_open_lecturer_pages(self):
        self.assertEqual(self.stud.get("/lecturer").status_code, 403)
        self.assertEqual(self.stud.get(f"/lecturer/courses/{self.course_id}").status_code, 403)

    def test_lecturer_cannot_post_attendance(self):
        self.assertEqual(self.attend(self.lect).status_code, 403)

    def test_anonymous_attendance_is_401(self):
        c = self.client()
        r = c.post("/api/attendance", json={"token": self.token}, headers={"X-CSRF-Token": self.csrf(c)})
        self.assertEqual(r.status_code, 401)

    def test_other_lecturer_cannot_see_my_course_or_session(self):
        other = self.client()
        self.register(other, "lecturer", "Dr Other", "other@acity.test", invite=True)
        self.login(other, "other@acity.test")
        self.assertEqual(other.get(f"/lecturer/courses/{self.course_id}").status_code, 404)
        self.assertEqual(other.get("/lecturer/sessions/1/export.csv").status_code, 404)

    # ---- attendance rules ----
    def test_valid_attempt_is_approved(self):
        r = self.attend(self.stud)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_too_far_is_rejected(self):
        r = self.attend(self.stud, loc=FAR)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["code"], "TOO_FAR")

    def test_rejected_attempt_can_be_retried_from_the_room(self):
        self.attend(self.stud, loc=FAR)
        self.assertEqual(self.attend(self.stud, loc=NEAR).status_code, 200)

    def test_low_accuracy_is_rejected(self):
        r = self.attend(self.stud, accuracy=500)
        self.assertEqual(r.get_json()["code"], "LOW_ACCURACY")

    def test_missing_location_is_rejected(self):
        r = self.stud.post("/api/attendance", json={"token": self.token},
                           headers={"X-CSRF-Token": self.csrf(self.stud)})
        self.assertEqual(r.get_json()["code"], "LOCATION_MISSING")

    def test_garbage_location_is_rejected(self):
        for lat in ("5.65", 999, True, float("nan")):
            r = self.attend(self.stud, loc=(lat, -0.186))
            self.assertEqual(r.get_json()["code"], "LOCATION_INVALID", lat)

    def test_duplicate_is_rejected(self):
        self.attend(self.stud)
        r = self.attend(self.stud)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "ALREADY_MARKED")

    def test_expired_session_is_rejected(self):
        with acity.app.app_context():
            db = get_db()
            db.execute("UPDATE attendance_sessions SET expires_at = ?", ("2000-01-01T00:00:00Z",))
            db.commit()
        self.assertEqual(self.attend(self.stud).get_json()["code"], "EXPIRED")

    def test_closed_session_is_rejected(self):
        sid = 1
        self.post(self.lect, f"/lecturer/sessions/{sid}/close")
        self.assertEqual(self.attend(self.stud).get_json()["code"], "SESSION_CLOSED")

    def test_new_session_closes_the_old_qr(self):
        self.start_session(self.lect, self.course_id)
        self.assertEqual(self.attend(self.stud).get_json()["code"], "SESSION_CLOSED")

    def test_unknown_or_malformed_token_is_rejected(self):
        self.assertEqual(self.attend(self.stud, token="x" * 32).get_json()["code"], "INVALID_SESSION")
        self.assertEqual(self.attend(self.stud, token="short").get_json()["code"], "INVALID_SESSION")
        self.assertEqual(self.attend(self.stud, token="' OR 1=1 --").get_json()["code"], "INVALID_SESSION")

    def test_unenrolled_student_is_rejected(self):
        c = self.client()
        self.register(c, "student", "Kofi Asante", "kofi@acity.test", student_id="ACU2002")
        self.login(c, "kofi@acity.test")
        r = self.attend(c)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "NOT_ENROLLED")

    def test_student_cannot_claim_to_be_present_or_someone_else(self):
        # Extra fields such as student_id or status are simply ignored by the server.
        r = self.attend(self.stud, loc=FAR, student_id=999, status="approved", present=True)
        self.assertEqual(r.get_json()["code"], "TOO_FAR")
        with acity.app.app_context():
            n = get_db().execute("SELECT COUNT(*) FROM attendance WHERE status = 'approved'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_coordinates_are_not_stored(self):
        self.attend(self.stud)
        with acity.app.app_context():
            cols = [r["name"] for r in get_db().execute("PRAGMA table_info(attendance)")]
        self.assertNotIn("latitude", cols)
        self.assertNotIn("longitude", cols)

    def test_radius_is_configurable_per_course(self):
        self.post(self.lect, f"/lecturer/courses/{self.course_id}/room", {
            "room_name": "Hall B", "latitude": ROOM[0], "longitude": ROOM[1], "radius_m": 10})
        self.assertEqual(self.attend(self.stud, loc=NEAR).get_json()["code"], "TOO_FAR")

    # ---- dashboards, CSV, pages ----
    def test_session_csv_and_stats(self):
        self.attend(self.stud)
        r = self.lect.get("/lecturer/sessions/1/export.csv")
        text = r.data.decode("utf-8-sig")
        self.assertIn("ACU1001,Ama Boateng,ama@acity.test,Present", text)
        with acity.app.app_context():
            stats = acity.attendance_stats(get_db(), 1, self.course_id)
        self.assertEqual((stats["students"], stats["present"], stats["absent"], stats["rate"]), (1, 1, 0, 100))

    def test_absent_student_counted(self):
        r = self.lect.get("/lecturer/sessions/1/export.csv")
        self.assertIn("Absent", r.data.decode("utf-8-sig"))
        with acity.app.app_context():
            stats = acity.attendance_stats(get_db(), 1)
        self.assertEqual((stats["present"], stats["absent"], stats["rate"]), (0, 1, 0))

    def test_csv_blocks_formula_injection(self):
        self.assertEqual(acity.csv_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(acity.csv_cell("Ama"), "Ama")

    def test_course_csv(self):
        self.attend(self.stud)
        r = self.lect.get(f"/lecturer/courses/{self.course_id}/export.csv")
        self.assertIn("Present", r.data.decode("utf-8-sig"))

    def test_pages_render(self):
        self.attend(self.stud, loc=FAR)
        for client, urls in ((self.lect, ["/", "/lecturer", f"/lecturer/courses/{self.course_id}",
                                          "/lecturer/sessions/1", "/lecturer/sessions/1/live", "/lecturer/sessions/1/qr.svg"]),
                             (self.stud, ["/student", "/student/scan", "/student/history", f"/attend/{self.token}"])):
            for url in urls:
                self.assertEqual(client.get(url).status_code, 200, url)
        self.assertEqual(self.client().get("/login").status_code, 200)
        self.assertEqual(self.client().get("/register").status_code, 200)
        self.assertEqual(self.stud.get("/attend/" + "z" * 32).status_code, 404)

    def test_anonymous_attend_page_redirects_to_login_and_back(self):
        r = self.client().get(f"/attend/{self.token}")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login?next=/attend/", r.headers["Location"])

    def test_open_redirect_is_blocked(self):
        c = self.client()
        r = c.post("/login?next=//evil.example", data={"email": "ama@acity.test", "password": "correct-horse",
                                                       "csrf_token": self.csrf(c)})
        self.assertNotIn("evil.example", r.headers["Location"])

    def test_live_json_shape(self):
        self.attend(self.stud)
        data = self.lect.get("/lecturer/sessions/1/live").get_json()
        self.assertEqual(data["stats"]["present"], 1)
        self.assertEqual(data["status"], "active")
        self.assertIn("Ama Boateng", data["rows_html"])


if __name__ == "__main__":
    unittest.main()
