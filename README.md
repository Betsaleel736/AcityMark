# AcityMark

QR-code attendance for Academic City University. A scan alone never approves anyone:
the **server** checks the code, the clock, enrollment, and the distance to the lecture room.

Stack: Flask, SQLite, plain HTML/CSS/JavaScript, `qrcode` (Python), `html5-qrcode` (browser scanner), browser Geolocation API.

## Folder structure

```
acitymark/
  app.py              routes, login, lecturer and student pages, CSV export
  attendance.py       the verification rules (the only code that approves attendance)
  geo.py              Haversine distance
  config.py           settings (radius, accuracy limit, invite code, secret key)
  db.py, schema.sql   SQLite connection and tables
  seed.py             optional demo accounts
  requirements.txt
  templates/          HTML pages (Jinja)
  static/css, js, img styles, browser scripts, logo
  tests/              automated tests
```

## Install and run (Windows, VS Code)

Open the `acitymark` folder in VS Code, then open a terminal (Terminal > New Terminal):

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

- If `python` is not found, try `py` instead.
- If PowerShell blocks `activate`, run `Set-ExecutionPolicy -Scope Process RemoteSigned` and try again.
- The database file is created automatically in `instance/`. Delete it to start fresh.
- Optional demo data: `python seed.py` (prints the demo logins).

## First run, step by step

1. **Register a lecturer** at /register. The invite code is `ACU-LECTURER-2026` until you change `LECTURER_INVITE_CODE` in `config.py` (or set it as an environment variable).
2. **Register one or more students** (student ID required).
3. As lecturer: **Create a course**, then on its page **register the lecture room**. Stand in the room and press "Use my current location", or type the coordinates. Set the allowed distance (default 50 m).
4. **Enroll students** by email or student ID.
5. **Start a session**, choose how many minutes the QR stays valid, and show the QR code.
6. As student: **Scan QR** (or open the code with the phone camera), press "Share location and mark attendance", and read the result. History shows every attempt.

## Testing on a phone (important)

Phone browsers only give camera and location access on **HTTPS** pages (or `localhost`). Pick one:

**A. Tunnel (easiest).** Install ngrok or Cloudflare Tunnel and run `ngrok http 5000`. Open the `https://...` address it prints, on your computer **and** phones. The QR then contains that address automatically.

**B. Same Wi-Fi with a temporary certificate.**
```
pip install cryptography
python app.py --host 0.0.0.0 --https
```
Find your computer's IP with `ipconfig`, open `https://YOUR-IP:5000` on both devices, and accept the certificate warning. Allow Python through the Windows firewall if asked.

If the lecturer opens the app as `localhost`, the QR would point phones at `localhost`, which cannot work. The session page warns you; fix it by using option A or B, or by setting `PUBLIC_BASE_URL`.

## Settings (`config.py` or environment variables)

| Setting | Default | Meaning |
|---|---|---|
| `ALLOWED_RADIUS_M` | 50 | Starting radius for new courses (each course can change its own) |
| `MAX_ACCURACY_M` | 75 | Reject readings less accurate than this |
| `LECTURER_INVITE_CODE` | `ACU-LECTURER-2026` | Needed to register as a lecturer. Change it. |
| `PUBLIC_BASE_URL` | empty | Address phones use, if different from the lecturer's browser |
| `SECRET_KEY` | auto-created in `instance/secret.key` | Signs login cookies |
| `COOKIE_SECURE` | 0 | Set to 1 when serving over HTTPS only |
| `DEFAULT_SESSION_MINUTES`, `MAX_SESSION_MINUTES` | 10, 180 | Session length defaults |

PowerShell example: `$env:ALLOWED_RADIUS_M = "40"; python app.py`

## What the server checks (in `attendance.py`)

1. The student is logged in (identity comes from the login session, never from the request).
2. The token belongs to a real session.
3. The session is not closed and not expired (server clock).
4. The student is enrolled in the course.
5. Not already approved for this session (also enforced by a unique database index).
6. The location reading is a valid number set with acceptable accuracy.
7. The Haversine distance to the registered room is within the course radius.

Each failure returns a clear message. Location problems can be retried; closed, expired, or not-enrolled cannot.

## Security notes

- Passwords are hashed (Werkzeug scrypt). Every SQL query uses placeholders.
- Login uses a signed cookie holding only the user ID; the user is reloaded from the database on every request.
- CSRF token on every POST, including the attendance API. Failed-login lockout (5 tries / 5 min, in memory).
- Lecturer pages and exports check that the course or session belongs to the logged-in lecturer.
- CSV export neutralises cells that start with `=`, `+`, `-`, `@`.
- Only the distance and accuracy are stored. Raw student coordinates are not.
- Lecturer registration needs the invite code, otherwise anyone could make themselves a lecturer.

## Limits you should tell users about

- GPS can be inaccurate indoors and can be spoofed by someone determined. This is a first filter, not proof of presence. The lecturer list shows rejected attempts so odd cases can be reviewed.
- A QR code photographed and sent to a friend works for the friend only if they are also near the room and enrolled, but one person could mark a friend who is logged in on their own phone. Short session times reduce this.
- No manual override yet: a lecturer cannot mark a student present by hand.
- Camera scanning needs internet (the scanner library loads from a CDN) and HTTPS. Students can paste the link instead.
- SQLite and the built-in Flask server suit a class trial, not a whole university.

## Tests

```
python -m unittest discover -s tests -v
```
Covers the Haversine formula, each verification rule, login/roles, CSRF, CSV export and page rendering.
The tests do not drive a real camera or real GPS.

## Ideas for later

Manual present/absent override, password reset, rotating QR codes, per-course CSV by date range, deploying with a production server (Waitress/Gunicorn) behind HTTPS.
