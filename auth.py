"""
Authentication for the Survey platform.

Lightweight, matching the app's existing style (raw sqlite3 + itsdangerous —
no SQLAlchemy, no JWT). Two-step login mirrors the Autocode pattern:

    password ok  → cookie scope "pending_2fa"  (10 min, cannot reach /admin)
    2FA passed   → cookie scope "full"         (7 days)

Passwords are bcrypt-hashed. The session cookie is a signed, timestamped
itsdangerous token carrying {uid, scope}; the max age enforced at load time
depends on the scope.
"""
import os
import time
from datetime import datetime, timezone

import bcrypt
from itsdangerous import BadData, URLSafeTimedSerializer

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="survey-session")

PENDING_MAX_AGE = 600          # 10 min — window between password and the 2FA step
FULL_MAX_AGE = 7 * 86400       # 7 days — full session lifetime


# ── Passwords ─────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


# ── Session token ─────────────────────────────────────────────────────────────

def make_token(uid: int, scope: str) -> str:
    return _serializer.dumps({"uid": uid, "scope": scope})


def _load(token: str | None) -> tuple[int, str] | None:
    """(uid, scope) if the token is valid and unexpired for its scope, else None."""
    if not token:
        return None
    try:
        data, ts = _serializer.loads(token, return_timestamp=True)
    except BadData:
        return None
    uid, scope = data.get("uid"), data.get("scope")
    if uid is None or scope not in ("pending_2fa", "full"):
        return None
    max_age = FULL_MAX_AGE if scope == "full" else PENDING_MAX_AGE
    if time.time() - ts.replace(tzinfo=timezone.utc).timestamp() > max_age:
        return None
    return uid, scope


def set_session(response, uid: int, scope: str):
    max_age = FULL_MAX_AGE if scope == "full" else PENDING_MAX_AGE
    response.set_cookie("session", make_token(uid, scope), httponly=True,
                        max_age=max_age, samesite="lax")


# ── User lookup helpers (take an open sqlite3 connection) ─────────────────────

def _user_by_id(db, uid: int):
    return db.execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)
    ).fetchone()


def current_user(request, db):
    """Fully authenticated (2FA-completed) active user, or None."""
    loaded = _load(request.cookies.get("session"))
    if not loaded:
        return None
    uid, scope = loaded
    if scope != "full":
        return None
    return _user_by_id(db, uid)


def pending_user(request, db):
    """User from a valid token of any scope (pending or full) — for the 2FA step."""
    loaded = _load(request.cookies.get("session"))
    if not loaded:
        return None
    uid, _scope = loaded
    return _user_by_id(db, uid)
