"""All settings live here. Change a value here, or set an environment variable."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)


def _load_secret_key():
    """Use SECRET_KEY if set; otherwise create one once and keep it in instance/secret.key."""
    from_env = os.environ.get("SECRET_KEY")
    if from_env:
        return from_env
    key_file = INSTANCE_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


class Config:
    SECRET_KEY = _load_secret_key()
    DATABASE = os.environ.get("DATABASE_PATH", str(INSTANCE_DIR / "acitymark.db"))

    # --- Attendance rules (easy to change) ---------------------------------
    # Default distance (metres) a student may be from the registered room.
    # Each course can override this on its page.
    ALLOWED_RADIUS_M = float(os.environ.get("ALLOWED_RADIUS_M", 50))
    # Reject readings whose reported accuracy is worse than this (metres).
    MAX_ACCURACY_M = float(os.environ.get("MAX_ACCURACY_M", 75))
    DEFAULT_SESSION_MINUTES = 10
    MAX_SESSION_MINUTES = 180

    # Anyone can register as a student. Lecturers must know this code.
    # CHANGE IT before real use (or set the LECTURER_INVITE_CODE variable).
    LECTURER_INVITE_CODE = os.environ.get("LECTURER_INVITE_CODE", "ACU-LECTURER-2026")

    # The address phones use to reach this server, e.g. https://abc123.ngrok-free.app
    # Leave empty to use whatever address the lecturer's browser is using.
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

    # --- Cookies -----------------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
