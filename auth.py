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
import contextvars
import ipaddress
import logging
import os
import secrets
import time
from datetime import datetime, timezone

import bcrypt
from itsdangerous import BadData, URLSafeTimedSerializer

log = logging.getLogger("survey.auth")

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Two ways of recognising a researcher, and `local` is the default on purpose:
# an app that believes an identity header with nothing in front of it lets in
# anyone who sends that header.
#
#   local     password + TOTP against the users table, as it has always worked
#   gateway   an upstream SSO gate vouches for the caller via X-Borant-*
#
# What does NOT change in either mode: respondents. A questionnaire is answered
# by people who have no account here and must never be asked for one, so
# /s/{slug} and its submit route stay open on both sides of this switch.
#
# On the second factor: in `local` this app enforces its own TOTP, and the
# two-scope cookie above is how. In `gateway` that check moves to the gate,
# which is asked for `two_factor` on everything it guards — deliberately, so
# that turning the gate on does not quietly become a downgrade.
AUTH_MODE = os.getenv("AUTH_MODE", "local").strip().lower()

# In gateway mode identity headers are believed only from here — the reverse
# proxy, never the internet. Under Docker this is a bridge gateway and NOT
# 127.0.0.1; DEPLOY.md shows how to read the real value off a running container.
TRUSTED_PROXY = os.getenv("BORANT_TRUSTED_PROXY", "127.0.0.1")


def _parse_trusted(raw: str) -> list:
    nets = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning("BORANT_TRUSTED_PROXY: ignoring %r, not an address or CIDR", chunk)
    return nets


TRUSTED_PROXIES = _parse_trusted(TRUSTED_PROXY)


def gateway_mode() -> bool:
    return AUTH_MODE == "gateway"


def _from_trusted_proxy(request) -> bool:
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)

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


def user_from_gateway(request, db):
    """The researcher the gate vouched for, or None.

    Lookup is by `borant_sub` and never by email: surveys hang off `owner_id`,
    and an address that changes with an institution is the wrong thing to
    re-find someone by. An unknown subject gets a profile — they have a grant,
    so they may use the tool; what they get is an empty list of surveys, not
    somebody else's.
    """
    if not gateway_mode():
        return None
    sub = request.headers.get("x-borant-sub")
    if not sub:
        return None
    if not _from_trusted_proxy(request):
        log.warning("X-Borant-Sub from %s, outside BORANT_TRUSTED_PROXY (%s): ignored",
                    request.client.host if request.client else "?", TRUSTED_PROXY)
        return None

    row = db.execute("SELECT * FROM users WHERE borant_sub = ? AND is_active = 1",
                     (sub,)).fetchone()
    if row:
        return row

    email = (request.headers.get("x-borant-email", "") or f"{sub}@borant.invalid").strip().lower()
    name = request.headers.get("x-borant-name", "") or email
    # A local password nobody knows, rather than none: `AUTH_MODE=local` has to
    # stay a working way back, and a row with no password is not a way back.
    db.execute(
        "INSERT INTO users (email, name, hashed_password, is_admin, is_active, borant_sub) "
        "VALUES (?, ?, ?, 0, 1, ?)",
        (email, name, hash_password(secrets.token_urlsafe(32)), sub))
    db.commit()
    log.info("gateway: new profile for %s (%s)", email, sub)
    return db.execute("SELECT * FROM users WHERE borant_sub = ?", (sub,)).fetchone()


def current_user(request, db):
    """Fully authenticated active user, or None.

    In `local` that means the two-scope cookie has reached "full", i.e. the TOTP
    step is done. In `gateway` the gate has already applied whatever level its
    policy asks for — `two_factor` here — so a request that arrives at all has
    cleared the same bar, one storey up.
    """
    if gateway_mode():
        return user_from_gateway(request, db)
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


# --- MCP credentials ---
# A key is a credential of a *person*: it carries an identity, not a set of
# entitlements. Every model-facing call resolves to this user and then goes
# through the same ownership check the web app applies, so the MCP surface has
# exactly the reach of its owner and no more.

_caller = contextvars.ContextVar("survey_mcp_caller", default=None)


def new_api_key() -> str:
    return "svy_" + secrets.token_urlsafe(32)


def check_api_key(db, key: str):
    """The active user row behind this key, or None. Stamps last_used_at so a
    key still in use somewhere is visible in the admin list."""
    if not key:
        return None
    row = db.execute(
        """SELECT u.* FROM api_keys k JOIN users u ON u.id = k.user_id
           WHERE k.key = ? AND k.active = 1 AND u.is_active = 1""",
        (key,),
    ).fetchone()
    if not row:
        return None
    db.execute("UPDATE api_keys SET last_used_at = datetime('now') WHERE key = ?", (key,))
    db.commit()
    return row


def set_caller(user) -> None:
    _caller.set(user)


def current_caller():
    """The user behind the API key on this request.

    Raises rather than returning None: a tool that runs with no caller would run
    with no ownership check, and failing loudly is the only safe direction.
    """
    user = _caller.get()
    if user is None:
        raise PermissionError("No authenticated caller")
    return user
