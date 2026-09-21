import contextlib
import csv
import io
import itertools
import json
import logging
import os
import random
import re
import secrets
import shutil
import sqlite3
import urllib.parse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import time

import aggregate
import auth
import crypto
import explore
import report as report_model
import results as results_view
import review_export
import totp

DB_PATH = os.getenv("DB_PATH", "/data/survey.db")
UPLOADS_PATH = os.getenv("UPLOADS_PATH", "/data/uploads")

# The application log is the only durable trace this app keeps of an
# administrative act; there is no audit table and nothing in the export records
# that the instrument moved. Replacing a schema over collected answers is
# written here because it is the one change a backup does not undo.
log = logging.getLogger("survey.admin")

# Bootstrap admin — created once on first run, then owns any pre-existing surveys.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@survey.local").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --- panel recruitment ---
# Panel providers (Bilendi, Dynata, Cint, Toluna...) hand each respondent a
# single-use token in the entry URL and expect us to bounce them back to one of
# three return URLs with that token attached. Without the bounce the provider
# cannot credit the respondent, so a panel field is unusable without this.
PANEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
PANEL_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
PANEL_OUTCOMES = ("complete", "screenout", "quotafull")

# An assignment issued but not yet submitted still counts toward balancing for
# this long, which keeps concurrent starts from piling onto the same arm without
# letting abandoned sessions skew the arms permanently.
PENDING_ASSIGNMENT_MINUTES = 60

# mcp_app reaches back here for get_db and the ownership check, but only at call
# time, so importing it before `app` exists resolves the cycle cleanly.
from mcp_app import mcp  # noqa: E402


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOADS_PATH, exist_ok=True)
    init_db()
    app.mount("/uploads", StaticFiles(directory=UPLOADS_PATH), name="uploads")
    # Chart.js, vendored at one version rather than pulled from a CDN: the
    # results page can end up projected in a lecture hall or inside somebody
    # else's iframe, and that is exactly where a network dependency fails.
    # Behind the gate, /static/* has to be public for the same reason /uploads/*
    # does — a respondent has no account here.
    app.mount("/static", StaticFiles(directory="static"), name="static")
    # The MCP session manager has to be running for the mounted transport to
    # answer at all; without this every call to /mcp fails with a 500 that says
    # nothing about why.
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# The MCP transport checks Host headers against DNS rebinding, so the public
# domain has to be allowed or every proxied request is refused.
def _allowed_hosts() -> list:
    from urllib.parse import urlparse
    hosts = ["localhost:8000", "127.0.0.1:8000", "localhost", "127.0.0.1"]
    public = urlparse(os.environ.get("PUBLIC_URL", "")).netloc
    if public:
        hosts.append(public)
    return hosts


from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

app.mount("/mcp", mcp.streamable_http_app(
    streamable_http_path="/", json_response=True, stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts(),
        allowed_origins=[os.environ.get("PUBLIC_URL", "http://localhost:8000")])))


@app.middleware("http")
async def mcp_key_gate(request: Request, call_next):
    """
    Resolve the MCP caller, or refuse.

    Two ways in, one table. The header is the normal path; /mcp/k/{key} carries
    the same key as a path segment for clients that cannot set headers, and is
    stripped before the mounted app sees it, so the MCP layer stays unaware of
    how the caller authenticated.

    Note this sits in front of /mcp only. The researcher UI keeps its own login,
    and /s/{slug} stays open to respondents, who have no account here.
    """
    path = request.url.path
    if not path.startswith("/mcp"):
        return await call_next(request)

    if path.startswith("/mcp/k/"):
        key, _, rest = path[len("/mcp/k/"):].partition("/")
        request.scope["path"] = "/mcp/" + rest
        request.scope["raw_path"] = request.scope["path"].encode()
    else:
        key = request.headers.get("X-API-Key", "")

    db = get_db()
    try:
        user = auth.check_api_key(db, key)
    finally:
        db.close()
    auth.set_caller(user)
    if not user:
        return JSONResponse({"error": "missing or invalid API key"}, status_code=401)
    return await call_next(request)


# --- database ---

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            email                 TEXT UNIQUE NOT NULL,
            name                  TEXT NOT NULL,
            hashed_password       TEXT NOT NULL,
            totp_secret_encrypted TEXT,
            totp_enabled          INTEGER NOT NULL DEFAULT 0,
            backup_codes_json     TEXT,
            is_admin              INTEGER NOT NULL DEFAULT 0,
            is_active             INTEGER NOT NULL DEFAULT 1,
            created_at            TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS surveys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT UNIQUE NOT NULL,
            title       TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS responses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id     INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
            response_json TEXT NOT NULL,
            submitted_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    # per-user ownership: additive migration (older DBs predate this column)
    survey_cols = {r[1] for r in db.execute("PRAGMA table_info(surveys)").fetchall()}
    if "owner_id" not in survey_cols:
        db.execute("ALTER TABLE surveys ADD COLUMN owner_id INTEGER REFERENCES users(id)")

    # The immutable subject an upstream SSO gate knows this person by, when
    # there is one. Null until map_borant.py links them, and never the email:
    # an address changes with an institution, and this is what has to survive
    # that change and keep someone attached to the surveys they own.
    user_cols = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
    if "borant_sub" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN borant_sub TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_borant_sub "
                   "ON users(borant_sub)")

    # migrate single-pool schema to multi-pool if needed
    has_rand_pools = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='rand_pools'"
    ).fetchone()
    if not has_rand_pools:
        db.executescript("DROP TABLE IF EXISTS assignment_counts; DROP TABLE IF EXISTS randomization;")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rand_pools (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id      INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
            pool_name      TEXT NOT NULL DEFAULT 'Pool',
            pool_order     INTEGER NOT NULL DEFAULT 0,
            pool_pages     TEXT NOT NULL DEFAULT '[]',
            show_count     INTEGER NOT NULL DEFAULT 1,
            condition_var  TEXT NULL,
            condition_map  TEXT NULL,
            page_order     TEXT NULL
        );
        -- legacy aggregate counter, superseded by `assignments`. Kept so that
        -- an existing deployment can be backfilled from it exactly once.
        CREATE TABLE IF NOT EXISTS assignment_counts (
            pool_id       INTEGER NOT NULL REFERENCES rand_pools(id) ON DELETE CASCADE,
            condition_key TEXT NOT NULL,
            count         INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pool_id, condition_key)
        );
    """)
    # migrate existing rand_pools tables that predate condition columns
    cols = {r[1] for r in db.execute("PRAGMA table_info(rand_pools)").fetchall()}
    if "condition_var" not in cols:
        db.execute("ALTER TABLE rand_pools ADD COLUMN condition_var TEXT NULL")
    if "condition_map" not in cols:
        db.execute("ALTER TABLE rand_pools ADD COLUMN condition_map TEXT NULL")
    if "page_order" not in cols:
        db.execute("ALTER TABLE rand_pools ADD COLUMN page_order TEXT NULL")

    # panel recruitment config, per survey (additive)
    for col in ("panel_token_param", "panel_complete_url",
                "panel_screenout_url", "panel_quotafull_url"):
        if col not in survey_cols:
            db.execute(f"ALTER TABLE surveys ADD COLUMN {col} TEXT NULL")

    # panel token on each response, unique per survey so a token cannot be
    # spent twice. SQLite treats NULLs as distinct, so non-panel surveys are
    # unaffected by the index.
    resp_cols = {r[1] for r in db.execute("PRAGMA table_info(responses)").fetchall()}
    if "panel_token" not in resp_cols:
        db.execute("ALTER TABLE responses ADD COLUMN panel_token TEXT NULL")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_responses_panel_token
                  ON responses (survey_id, panel_token)""")

    # Per-assignment ledger. Replaces the aggregate assignment_counts, which
    # incremented on page load and so drifted with every abandonment and reload.
    fresh_assignments = not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='assignments'"
    ).fetchone()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS assignments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_id       INTEGER NOT NULL REFERENCES rand_pools(id) ON DELETE CASCADE,
            condition_key TEXT NOT NULL,
            issued_at     TEXT NOT NULL DEFAULT (datetime('now')),
            completed     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_assignments_pool
            ON assignments (pool_id, completed, issued_at);
    """)
    if fresh_assignments:
        _backfill_assignments(db)

    # The results report: an ordered document of blocks, one per survey. A
    # structure and not a copy of anything, so it lives beside the schema in
    # the same additive style as condition_map and page_order. Null until
    # somebody opens the editor, which reads as an empty report.
    if "report_json" not in survey_cols:
        db.execute("ALTER TABLE surveys ADD COLUMN report_json TEXT NULL")

    # A read-only handle on one's own response, minted at submit and good for
    # nothing but marking that person's answers on the results page. Emphatically
    # not the panel token: that one identifies a person to a provider, and would
    # be a reconciliation identifier travelling in a URL people paste around.
    if "view_token" not in resp_cols:
        db.execute("ALTER TABLE responses ADD COLUMN view_token TEXT NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_responses_view_token "
               "ON responses (survey_id, view_token)")

    # MCP credential, one per user and held on the user row.
    #
    # A key is a credential of a person, never of the installation: every
    # model-facing call resolves to the user holding it and then goes through
    # the same _owned_survey() the web app uses, so a key reaches exactly what
    # its owner reaches. Keeping it here rather than in a table of its own makes
    # that identity structural — there is nowhere for a key without an owner to
    # exist — at the cost of per-client revocation: regenerating invalidates
    # whatever else was using the old one.
    for col, decl in (("mcp_key", "TEXT"),
                      ("mcp_key_created_at", "TEXT"),
                      ("mcp_key_last_used_at", "TEXT")):
        if col not in user_cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {decl} NULL")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_mcp_key ON users (mcp_key)")
    db.commit()

    _bootstrap_admin(db)
    db.close()


def _backfill_assignments(db):
    """Seed the per-assignment ledger from the old aggregate counters so that
    balancing continues from where it left off rather than restarting.

    The old counters cannot distinguish a completed response from an abandoned
    page load, so every historical count is carried over as completed. That
    slightly overstates history, and it is the closest reconstruction available.
    """
    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='assignment_counts'"
    ).fetchone():
        return
    for row in db.execute("SELECT pool_id, condition_key, count FROM assignment_counts"):
        if row["count"] > 0:
            db.executemany(
                "INSERT INTO assignments (pool_id, condition_key, completed) VALUES (?, ?, 1)",
                [(row["pool_id"], row["condition_key"])] * row["count"],
            )


def _bootstrap_admin(db):
    """Create the bootstrap admin on first run and hand it any orphan surveys.
    The admin still has to enrol in 2FA on first login (totp_enabled = 0)."""
    admin = db.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (email, name, hashed_password, is_admin) VALUES (?, ?, ?, 1)",
            (ADMIN_EMAIL, "Admin", auth.hash_password(ADMIN_PASSWORD)),
        )
        db.commit()
        admin = db.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    # assign surveys that predate multi-user to the admin
    db.execute("UPDATE surveys SET owner_id = ? WHERE owner_id IS NULL", (admin["id"],))
    db.commit()


# --- auth helpers ---

def _response_count(db, survey_id: int) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM responses WHERE survey_id = ?", (survey_id,)
    ).fetchone()[0]


def _owned_survey(db, slug: str, user):
    """The survey row if `user` may manage it (owner or admin), else None."""
    row = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    if user["is_admin"] or row["owner_id"] == user["id"]:
        return row
    return None


# --- auth routes ---

@app.get("/login")
async def login_page(error: int = 0):
    # the login form lives on the combined landing page
    return RedirectResponse("/?error=1" if error else "/", status_code=302)


@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    # In gateway mode the app switches its own login off rather than trusting
    # the proxy to hide it: two sets of credentials for one tool is exactly what
    # the SSO is there to remove.
    if auth.gateway_mode():
        return RedirectResponse("/", status_code=302)
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", (email.strip().lower(),)
    ).fetchone()
    db.close()
    if not user or not auth.verify_password(password, user["hashed_password"]):
        return RedirectResponse("/?error=1", status_code=302)
    # password ok → pending session; full access only after the 2FA step
    response = RedirectResponse("/2fa", status_code=302)
    auth.set_session(response, user["id"], "pending_2fa")
    return response


@app.get("/register")
async def register_page():
    return RedirectResponse("/?tab=register", status_code=302)


@app.post("/register")
async def register(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    # Open registration is a `local` affordance. Behind the gate, who gets an
    # account is the gate's decision and arriving here would make a second one.
    if auth.gateway_mode():
        return RedirectResponse("/", status_code=302)
    email = email.strip().lower()
    name = name.strip()
    if not EMAIL_RE.match(email):
        return RedirectResponse("/?tab=register&reg_error=email", status_code=302)
    if len(password) < 8:
        return RedirectResponse("/?tab=register&reg_error=pwd", status_code=302)
    if not name:
        return RedirectResponse("/?tab=register&reg_error=name", status_code=302)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (email, name, hashed_password) VALUES (?, ?, ?)",
            (email, name, auth.hash_password(password)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        return RedirectResponse("/?tab=register&reg_error=taken", status_code=302)
    user_id = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    db.close()
    # accounts are active immediately, but 2FA enrolment is mandatory before use
    response = RedirectResponse("/2fa", status_code=302)
    auth.set_session(response, user_id, "pending_2fa")
    return response


BORANT_LOGOUT_URL = os.getenv("BORANT_LOGOUT_URL", "https://id.borant.eu/logout")


@app.get("/logout")
async def logout():
    # In gateway mode dropping the local cookie is not signing out: the gate
    # still holds the session, and the next click walks straight back in.
    target = BORANT_LOGOUT_URL if auth.gateway_mode() else "/login"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie("session")
    return response


# --- two-factor (TOTP, mandatory) ---

@app.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request):
    db = get_db()
    if auth.current_user(request, db):   # already fully authenticated
        db.close()
        return RedirectResponse("/admin", status_code=302)
    user = auth.pending_user(request, db)
    db.close()
    if not user:                         # no pending session → back to login
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "twofa.html", {
        "enrolled": bool(user["totp_enabled"]),
        "email": user["email"],
    })


@app.post("/api/2fa/setup")
async def api_2fa_setup(request: Request):
    """Generate a secret + QR for enrolment (does not enable 2FA until confirmed)."""
    db = get_db()
    user = auth.pending_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "session expired"}, status_code=401)
    secret = totp.generate_secret()
    db.execute("UPDATE users SET totp_secret_encrypted = ? WHERE id = ?",
               (crypto.encrypt(secret), user["id"]))
    db.commit()
    db.close()
    uri = totp.provisioning_uri(secret, user["email"])
    return JSONResponse({"secret": secret, "uri": uri, "qr": totp.qr_data_uri(uri)})


@app.post("/api/2fa/confirm")
async def api_2fa_confirm(request: Request):
    db = get_db()
    user = auth.pending_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "session expired"}, status_code=401)
    if not user["totp_secret_encrypted"]:
        db.close()
        return JSONResponse({"error": "start the setup first"}, status_code=400)
    body = await request.json()
    if not totp.verify(crypto.decrypt(user["totp_secret_encrypted"]), body.get("code", "")):
        db.close()
        return JSONResponse({"error": "Invalid code — check your authenticator app"}, status_code=400)
    plain, hashes = totp.generate_backup_codes()
    db.execute("UPDATE users SET totp_enabled = 1, backup_codes_json = ? WHERE id = ?",
               (json.dumps(hashes), user["id"]))
    db.commit()
    db.close()
    response = JSONResponse({"ok": True, "backup_codes": plain})
    auth.set_session(response, user["id"], "full")
    return response


@app.post("/api/2fa/verify")
async def api_2fa_verify(request: Request):
    db = get_db()
    user = auth.pending_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "session expired"}, status_code=401)
    if not (user["totp_enabled"] and user["totp_secret_encrypted"]):
        db.close()
        return JSONResponse({"error": "2FA is not configured"}, status_code=400)
    body = await request.json()
    code = body.get("code", "")
    ok = totp.verify(crypto.decrypt(user["totp_secret_encrypted"]), code)
    if not ok:  # fall back to a one-time backup code
        remaining = totp.consume_backup_code(code, json.loads(user["backup_codes_json"] or "[]"))
        if remaining is not None:
            db.execute("UPDATE users SET backup_codes_json = ? WHERE id = ?",
                       (json.dumps(remaining), user["id"]))
            db.commit()
            ok = True
    db.close()
    if not ok:
        return JSONResponse({"error": "Invalid code"}, status_code=400)
    response = JSONResponse({"ok": True})
    auth.set_session(response, user["id"], "full")
    return response


# --- randomization ---

def _pool_counts(db, pool_id: int) -> tuple[dict, dict]:
    """(completed, pending) counts per condition for one pool.

    Balancing is driven by completed responses, with assignments issued in the
    last PENDING_ASSIGNMENT_MINUTES counted too. Without the pending term a burst
    of simultaneous starts would all see the same counts and land on the same
    arm; with it, an abandoned session stops distorting the arms once it ages out.
    """
    completed, pending = {}, {}
    rows = db.execute(
        f"""SELECT condition_key,
                   SUM(completed) AS done,
                   SUM(CASE WHEN completed = 0
                             AND issued_at > datetime('now', '-{PENDING_ASSIGNMENT_MINUTES} minutes')
                            THEN 1 ELSE 0 END) AS live
            FROM assignments WHERE pool_id = ? GROUP BY condition_key""",
        (pool_id,),
    ).fetchall()
    for r in rows:
        completed[r["condition_key"]] = r["done"] or 0
        pending[r["condition_key"]] = r["live"] or 0
    return completed, pending


def _assign_condition(db, pool_id: int, pool: list, show_count: int) -> tuple[list, int]:
    """Pick the least-used combination and record a pending assignment.

    Returns (pages, assignment_id). The assignment is marked completed only when
    the response is submitted, so page loads that go nowhere do not consume an arm.
    """
    all_conditions = [
        ",".join(sorted(combo))
        for combo in itertools.combinations(pool, show_count)
    ]
    completed, pending = _pool_counts(db, pool_id)
    load = {c: completed.get(c, 0) + pending.get(c, 0) for c in all_conditions}
    min_count = min(load.values(), default=0)
    chosen = random.choice([c for c in all_conditions if load[c] == min_count])
    cur = db.execute(
        "INSERT INTO assignments (pool_id, condition_key) VALUES (?, ?)",
        (pool_id, chosen),
    )
    assignment_id = cur.lastrowid
    # housekeeping: pending rows older than a week can never complete
    db.execute(
        "DELETE FROM assignments WHERE pool_id = ? AND completed = 0 "
        "AND issued_at < datetime('now', '-7 days')",
        (pool_id,),
    )
    db.commit()
    return chosen.split(","), assignment_id


def _complete_assignments(db, survey_id: int, ids) -> None:
    """Mark the pending assignments for a submitted response as completed.

    Ids come from the client, so the update is scoped to pools belonging to this
    survey: an arbitrary id from elsewhere cannot be flipped.
    """
    clean = [int(i) for i in ids if str(i).isdigit()][:10]
    if not clean:
        return
    placeholders = ",".join("?" * len(clean))
    db.execute(
        f"""UPDATE assignments SET completed = 1
            WHERE id IN ({placeholders})
              AND pool_id IN (SELECT id FROM rand_pools WHERE survey_id = ?)""",
        (*clean, survey_id),
    )


# --- panel recruitment helpers ---

def _panel_config(row) -> dict | None:
    """Panel settings for a survey row, or None when panel mode is off.
    Panel mode is on as soon as a token parameter is configured."""
    try:
        param = row["panel_token_param"]
    except (IndexError, KeyError):
        return None
    if not param:
        return None
    return {
        "param": param,
        "complete": row["panel_complete_url"] or "",
        "screenout": row["panel_screenout_url"] or "",
        "quotafull": row["panel_quotafull_url"] or "",
    }


def _read_panel_token(query_params, param: str) -> str | None:
    """The token from the entry URL, matched case-insensitively because
    providers disagree on capitalisation (RID, rid, Rid all appear in the wild).
    Anything outside the safe charset is treated as absent."""
    wanted = param.lower()
    for key, value in query_params.items():
        if key.lower() == wanted:
            value = (value or "").strip()
            return value if PANEL_TOKEN_RE.match(value) else None
    return None


def _panel_redirect(config: dict, outcome: str, token: str | None) -> str | None:
    """Return URL for an outcome, with the token substituted or appended.

    A `{token}` placeholder anywhere in the configured URL is replaced in place,
    which covers providers whose return URL carries the id mid-path. Otherwise
    the token is appended as a query parameter under the same name it arrived in.
    """
    url = (config.get(outcome) or "").strip()
    if not url:
        return None
    quoted = urllib.parse.quote(token or "", safe="")
    if "{token}" in url:
        return url.replace("{token}", quoted)
    if not token:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urllib.parse.quote(config['param'], safe='')}={quoted}"


# --- public routes ---

@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    """The user guide, rendered from docs/guide.md, and public like the landing.

    It is linked from the card on borant.eu/tools, where the reader has no
    account yet and is deciding whether the tool is worth asking for one. Behind
    the gate it would document the tool to the people who already use it, which
    is the wrong half of the audience. One source of truth: the markdown in the
    repository, so the page cannot drift from what ships.
    """
    import markdown
    md_text = open(os.path.join(os.path.dirname(__file__), "docs", "guide.md"), encoding="utf-8").read()
    return templates.TemplateResponse(request, "guide.html", {
        "guide_html": markdown.markdown(md_text, extensions=["tables", "fenced_code"]),
        "app_name": "Survey",
        "app_url": "/",
    })


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, error: int = 0, reg_error: str = "", tab: str = ""):
    db = get_db()
    user = auth.current_user(request, db)
    db.close()
    if user:
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(request, "landing.html", {
        "error": error,
        "reg_error": reg_error,
        "tab": "register" if (tab == "register" or reg_error) else "login",
    })


def _panel_stop(request, heading: str, message: str, redirect: str | None, status: int = 200):
    """Dead-end page for a panel respondent who cannot take the survey.
    Redirects to the provider when a return URL is configured, so the provider
    can classify them instead of recording an abandonment."""
    if redirect:
        return RedirectResponse(redirect, status_code=302)
    return templates.TemplateResponse(
        request,
        "panel_stop.html",
        {"heading": heading, "message": message},
        status_code=status,
    )


@app.get("/s/{slug}", response_class=HTMLResponse)
async def survey_page(request: Request, slug: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM surveys WHERE slug = ? AND active = 1", (slug,)
    ).fetchone()
    if not row:
        db.close()
        return templates.TemplateResponse(request, "closed.html", {}, status_code=404)

    # --- panel gate ---
    panel = _panel_config(row)
    panel_token = None
    if panel:
        panel_token = _read_panel_token(request.query_params, panel["param"])
        # owners can walk their own survey without a provider token
        preview = False
        if request.query_params.get("preview") == "1":
            viewer = auth.current_user(request, db)
            preview = bool(viewer and _owned_survey(db, slug, viewer))
        if not panel_token and not preview:
            db.close()
            return _panel_stop(
                request,
                "Invalid link",
                "This survey can only be entered through the link supplied by your panel "
                "provider, which carries the identifier needed to credit your participation. "
                "Please return to the panel and start again from there.",
                _panel_redirect(panel, "screenout", None),
                status=400,
            )
        if panel_token and db.execute(
            "SELECT 1 FROM responses WHERE survey_id = ? AND panel_token = ?",
            (row["id"], panel_token),
        ).fetchone():
            db.close()
            return _panel_stop(
                request,
                "Already completed",
                "Our records show this invitation has already been used to complete the "
                "survey. Each invitation can be used once.",
                _panel_redirect(panel, "screenout", panel_token),
            )

    assigned_pages_list: list = []
    pool_pages_list: list = []
    assignment_ids: list = []
    conditions: dict = {}
    page_orders: list = []
    pools = db.execute(
        "SELECT id, pool_pages, show_count, condition_var, condition_map, page_order "
        "FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (row["id"],),
    ).fetchall()
    for p in pools:
        pool = json.loads(p["pool_pages"])
        sc = p["show_count"]
        if pool and 0 < sc <= len(pool):
            pool_pages_list.extend(pool)
            assigned, assignment_id = _assign_condition(db, p["id"], pool, sc)
            assignment_ids.append(assignment_id)
            assigned_pages_list.extend(assigned)
            # condition variable: only meaningful when show_count=1
            cvar = p["condition_var"]
            if cvar and sc == 1 and len(assigned) == 1:
                page = assigned[0]
                cmap_raw = p["condition_map"]
                if cmap_raw:
                    try:
                        cmap = json.loads(cmap_raw)
                        value = cmap.get(page, page)
                    except (json.JSONDecodeError, TypeError):
                        value = page
                else:
                    value = page
                conditions[cvar] = value
                # optional page reordering, keyed by the condition value just
                # assigned. Only the sequence matters: the named pages are put
                # back into the slots they already occupy, in the order given,
                # so a pool can counterbalance presentation order without
                # duplicating pages (and question names) in the schema.
                porder_raw = p["page_order"]
                if porder_raw:
                    try:
                        pmap = json.loads(porder_raw)
                    except (json.JSONDecodeError, TypeError):
                        pmap = None
                    if isinstance(pmap, dict) and isinstance(pmap.get(value), list):
                        page_orders.append({"var": cvar, "pages": pmap[value]})
    assigned_pages = assigned_pages_list or None
    pool_pages = pool_pages_list or None
    db.close()

    return templates.TemplateResponse(request, "survey.html", {
        "title": row["title"],
        "slug": slug,
        "schema": json.loads(row["schema_json"]),
        "assigned_pages": assigned_pages,
        "pool_pages": pool_pages,
        "assignment_ids": assignment_ids,
        "conditions": conditions,
        "page_orders": page_orders,
        "panel_token": panel_token,
    })


@app.post("/s/{slug}/submit")
async def submit(slug: str, request: Request):
    db = get_db()
    row = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not row or not row["active"]:
        db.close()
        return JSONResponse({"error": "survey not found or closed"}, status_code=404)
    data = await request.json()

    panel = _panel_config(row)
    token = None
    if panel:
        raw = str(data.get("_panel_token") or "").strip()
        token = raw if PANEL_TOKEN_RE.match(raw) else None
        data["_panel_token"] = token

    # outcome drives which return URL the respondent bounces to; the schema sets
    # it with a SurveyJS trigger (see the Panel section of the manage page)
    outcome = str(data.get("_outcome") or "complete").strip().lower()
    if outcome not in PANEL_OUTCOMES:
        outcome = "complete"

    view_token = secrets.token_urlsafe(16)
    try:
        db.execute(
            "INSERT INTO responses (survey_id, response_json, panel_token, view_token) "
            "VALUES (?, ?, ?, ?)",
            (row["id"], json.dumps(data, ensure_ascii=False), token, view_token),
        )
    except sqlite3.IntegrityError:
        # the unique index caught a token being spent twice
        db.close()
        return JSONResponse(
            {"error": "duplicate", "redirect": _panel_redirect(panel, "screenout", token)},
            status_code=409,
        )
    _complete_assignments(db, row["id"], data.get("_assignment_ids") or [])
    db.commit()
    stored = report_model.normalise(row["report_json"], json.loads(row["schema_json"]))
    db.close()
    _invalidate_results(row["id"])
    # A results page this person may see, with their own answers marked. Only
    # offered when the report actually reaches respondents, so the questionnaire
    # never ends on a link to a page that answers "not found".
    reaches = report_model.RANK[stored["audience"]] >= report_model.RANK[aggregate.RESPONDENT]
    # No token means nobody to credit, so an owner previewing the questionnaire
    # is not bounced onto the provider's completion endpoint.
    return JSONResponse({
        "ok": True,
        "redirect": _panel_redirect(panel, outcome, token) if (panel and token) else None,
        "results_url": f"/s/{slug}/results?r={view_token}" if reaches else None,
    })


# --- admin routes ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if user["is_admin"]:
        surveys = db.execute("""
            SELECT s.*, COUNT(r.id) AS response_count, u.email AS owner_email
            FROM surveys s
            LEFT JOIN responses r ON r.survey_id = s.id
            LEFT JOIN users u ON u.id = s.owner_id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """).fetchall()
    else:
        surveys = db.execute("""
            SELECT s.*, COUNT(r.id) AS response_count, NULL AS owner_email
            FROM surveys s
            LEFT JOIN responses r ON r.survey_id = s.id
            WHERE s.owner_id = ?
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """, (user["id"],)).fetchall()
    db.close()
    return templates.TemplateResponse(request, "admin.html", {
        "surveys": surveys, "user": user,
        "public_url": os.environ.get("PUBLIC_URL", "").rstrip("/"),
    })


# --- MCP key ---
# One per user, generated by the user. Nothing to approve: the key reaches
# exactly the surveys its owner already reaches, so handing it out is not a
# grant of anything new.

@app.post("/admin/mcp-key")
async def set_mcp_key(request: Request):
    """Generate a key, replacing any previous one. Regenerating invalidates the
    old key immediately — anything still configured with it stops working, which
    is the point when the old one has leaked."""
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    db.execute("UPDATE users SET mcp_key = ?, mcp_key_created_at = datetime('now'), "
               "mcp_key_last_used_at = NULL WHERE id = ?",
               (auth.new_api_key(), user["id"]))
    db.commit()
    db.close()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/mcp-key/revoke")
async def revoke_mcp_key(request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    db.execute("UPDATE users SET mcp_key = NULL, mcp_key_created_at = NULL, "
               "mcp_key_last_used_at = NULL WHERE id = ?", (user["id"],))
    db.commit()
    db.close()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/surveys")
async def create_survey(
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    schema_file: UploadFile = File(None),
    schema_text: str = Form(""),
):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)

    if schema_file and schema_file.filename:
        raw = await schema_file.read()
        schema_str = raw.decode("utf-8")
    elif schema_text.strip():
        schema_str = schema_text.strip()
    else:
        db.close()
        return RedirectResponse("/admin?error=no_schema", status_code=302)

    try:
        json.loads(schema_str)
    except json.JSONDecodeError:
        db.close()
        return RedirectResponse("/admin?error=invalid_json", status_code=302)

    slug = slug.strip().lower().replace(" ", "-")

    try:
        db.execute(
            "INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?, ?, ?, ?)",
            (slug, title, schema_str, user["id"]),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        return RedirectResponse("/admin?error=duplicate_slug", status_code=302)
    db.close()
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/surveys/{slug}", response_class=HTMLResponse)
async def manage_survey(slug: str, request: Request):
    """Per-survey hub: share link + QR, exports, questionnaire tools,
    configuration links, and the danger zone."""
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if not row:
        db.close()
        return RedirectResponse("/admin", status_code=302)
    stats = db.execute(
        "SELECT COUNT(*) AS n, MAX(submitted_at) AS last FROM responses WHERE survey_id = ?",
        (row["id"],),
    ).fetchone()
    pool_count = db.execute(
        "SELECT COUNT(*) FROM rand_pools WHERE survey_id = ?", (row["id"],)
    ).fetchone()[0]
    owner = db.execute(
        "SELECT email FROM users WHERE id = ?", (row["owner_id"],)
    ).fetchone()
    db.close()

    schema = json.loads(row["schema_json"])
    locales = review_export._locales_in(schema) or {"en"}
    langs = [l for l in review_export.LANGS if l in locales]
    stored_report = report_model.normalise(row["report_json"], schema)

    upload_dir = os.path.join(UPLOADS_PATH, slug)
    files_count = len(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else 0

    # honour the reverse proxy's scheme so the QR points at the public URL
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    public_url = f"{scheme}://{host}/s/{slug}"

    panel = _panel_config(row) or {}
    return templates.TemplateResponse(request, "manage.html", {
        "user": user,
        "slug": slug,
        "title": row["title"],
        "active": row["active"],
        "created_at": row["created_at"],
        "owner_email": owner["email"] if owner else None,
        "response_count": stats["n"],
        "last_response": stats["last"],
        "pool_count": pool_count,
        "files_count": files_count,
        "report_blocks": len(stored_report["blocks"]),
        "report_audience": stored_report["audience"],
        "results_url": f"{public_url}/results",
        "embed_url": f"{public_url}/results/embed",
        # Only worth a QR when there is something to point it at: a report the
        # owner kept private has no link to hand anybody.
        "results_qr": (totp.qr_data_uri(f"{public_url}/results")
                       if stored_report["audience"] == aggregate.PUBLIC else None),
        "langs": langs,
        "public_url": public_url,
        "qr": totp.qr_data_uri(public_url),
        "panel": panel,
        "panel_error": request.query_params.get("panel_error", ""),
        "panel_saved": request.query_params.get("panel_saved") == "1",
    })


@app.post("/admin/surveys/{slug}/panel")
async def save_panel_config(
    slug: str,
    request: Request,
    token_param: str = Form(""),
    complete_url: str = Form(""),
    screenout_url: str = Form(""),
    quotafull_url: str = Form(""),
):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if not row:
        db.close()
        return RedirectResponse("/admin", status_code=302)

    token_param = token_param.strip()
    urls = {k: v.strip() for k, v in (
        ("complete", complete_url), ("screenout", screenout_url), ("quotafull", quotafull_url)
    )}

    def fail(message: str):
        db.close()
        return RedirectResponse(
            f"/admin/surveys/{slug}?panel_error={urllib.parse.quote(message)}",
            status_code=302,
        )

    if token_param and not PANEL_PARAM_RE.match(token_param):
        return fail("The token parameter must be 1 to 32 letters, digits, hyphens or underscores.")
    for name, url in urls.items():
        if url and not url.lower().startswith(("http://", "https://")):
            return fail(f"The {name} URL must start with http:// or https://.")
    if not token_param and any(urls.values()):
        return fail("Set a token parameter to switch panel mode on, or clear the return URLs.")

    db.execute(
        """UPDATE surveys SET panel_token_param = ?, panel_complete_url = ?,
                              panel_screenout_url = ?, panel_quotafull_url = ?
           WHERE id = ?""",
        (token_param or None, urls["complete"] or None,
         urls["screenout"] or None, urls["quotafull"] or None, row["id"]),
    )
    db.commit()
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}?panel_saved=1", status_code=302)


# --- results ---

# Aggregates are recomputed on request and held briefly. The public page polls,
# so without this a room full of people reloading would each re-read the whole
# response table. Nothing here is keyed by a respondent: the view that marks
# somebody's own answers is built fresh, because it is theirs and it is rare.
_RESULTS_CACHE = {}
_RESULTS_TTL = 20


def _invalidate_results(survey_id: int) -> None:
    for key in [k for k in _RESULTS_CACHE if k[0] == survey_id]:
        del _RESULTS_CACHE[key]


def _pool_rows(db, survey_id: int) -> list:
    """Pools as plain dicts, with the JSON columns parsed. Only `pool_pages` is
    read downstream, but the shape matches what flow and review_export expect."""
    out = []
    for p in db.execute(
        "SELECT pool_name, pool_pages, show_count, condition_var, condition_map, page_order "
        "FROM rand_pools WHERE survey_id = ? ORDER BY pool_order", (survey_id,)
    ).fetchall():
        def _loads(value, fallback):
            try:
                return json.loads(value) if value else fallback
            except (json.JSONDecodeError, TypeError):
                return fallback
        out.append({
            "pool_name": p["pool_name"],
            "pool_pages": _loads(p["pool_pages"], []),
            "show_count": p["show_count"],
            "condition_var": p["condition_var"],
            "condition_map": _loads(p["condition_map"], {}),
            "page_order": _loads(p["page_order"], {}),
        })
    return out


def _pick_locale(request: Request, available: list, default: str = None) -> str:
    asked = request.query_params.get("lang") or request.cookies.get("lang")
    if asked in available:
        return asked
    header = (request.headers.get("accept-language") or "").split(",")[0].split("-")[0]
    if header in available:
        return header
    return default or (available[0] if available else "en")


def _build_results(db, survey, viewer: str, locale: str, mine=None, by=None) -> dict:
    schema = json.loads(survey["schema_json"])
    stored = report_model.normalise(survey["report_json"], schema)
    pools = _pool_rows(db, survey["id"])
    responses = [json.loads(r["response_json"]) for r in db.execute(
        "SELECT response_json FROM responses WHERE survey_id = ?", (survey["id"],))]
    return results_view.build(schema, pools, stored, responses, viewer, locale, mine, by)


def _results_payload(db, survey, viewer: str, locale: str, mine=None, by=None) -> dict:
    if mine is not None:
        return _build_results(db, survey, viewer, locale, mine, by)
    key = (survey["id"], viewer, locale, by)
    hit = _RESULTS_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _RESULTS_TTL:
        return hit[1]
    payload = _build_results(db, survey, viewer, locale, None, by)
    _RESULTS_CACHE[key] = (now, payload)
    return payload


def _explore_vars(schema: dict, stored: dict, viewer: str, locale: str) -> list:
    """The variables this viewer may put on an axis.

    The owner reaches everything. A reader reaches nothing unless the report
    says so, and then only the questions the report already shows them:
    crossing two questions is a way of reading them, and a question kept back
    is kept back on both axes too.
    """
    if viewer == aggregate.OWNER:
        return explore.variables(schema, locale)
    if not stored.get("explore"):
        return []
    allowed = report_model.published_names(stored, schema, viewer)
    return [v for v in explore.variables(schema, locale) if v["question"] in allowed]


def _results_context(request, db, survey, slug, viewer, mine=None, by=None,
                     embed=False) -> dict:
    schema = json.loads(survey["schema_json"])
    stored = report_model.normalise(survey["report_json"], schema)
    # Two layers, and the switcher belongs to the outer one. The page's own
    # words exist in all four languages whatever the questionnaire was written
    # in, so a reader who has no Italian at least gets the frame in their own;
    # the question labels follow the schema and fall back to what it has. A
    # page with no switcher at all was the wrong answer for anything a stranger
    # might open.
    content = report_model.locales(schema)
    available = list(review_export.LANGS)
    locale = _pick_locale(request, available, default=content[0])
    payload = _results_payload(db, survey, viewer, locale, mine, by)
    return {
        "title": survey["title"],
        "slug": slug,
        "viewer": viewer,
        # Set here for every view so the template can ask without guarding: the
        # owner's route overrides them when they are looking as somebody else.
        # Anything the page's script reads unconditionally has to be in here,
        # or a view that never sets it renders an exception instead of a page.
        "owner": viewer == aggregate.OWNER,
        "as_who": None,
        "explore_vars": _explore_vars(schema, stored, viewer, locale),
        "explore_help": results_view.help_for(locale),
        "explore_pairs": explore.pair_count(schema),
        "explore_url": (f"/admin/surveys/{slug}/explore.json"
                        if viewer == aggregate.OWNER
                        else f"/s/{slug}/explore.json"),
        "embed": embed,
        "locale": locale,
        "t": results_view.strings(locale),
        "locales": available,
        "audience": stored["audience"],
        "blocks": payload["blocks"],
        "responses": payload["responses"],
        "by": by,
        "condition_vars": [p["condition_var"] for p in _pool_rows(db, survey["id"])
                           if p["condition_var"]],
        "updated_at": time.strftime("%H:%M"),
        "poll_url": (f"/admin/surveys/{slug}/results.json" if viewer == aggregate.OWNER
                     else f"/s/{slug}/results.json"),
    }


def _public_viewer(db, survey, request):
    """(viewer, own answers) for a request to the public results page, or None
    when this report does not reach that far."""
    stored = report_model.normalise(
        survey["report_json"], json.loads(survey["schema_json"]))
    token = (request.query_params.get("r") or "").strip()
    row = None
    if token:
        row = db.execute(
            "SELECT response_json FROM responses WHERE survey_id = ? AND view_token = ?",
            (survey["id"], token)).fetchone()
    if row is not None:
        if report_model.RANK[stored["audience"]] < report_model.RANK[aggregate.RESPONDENT]:
            return None, None
        return aggregate.RESPONDENT, aggregate.answers(json.loads(row["response_json"]))
    if stored["audience"] != aggregate.PUBLIC:
        return None, None
    return aggregate.PUBLIC, None


@app.get("/admin/surveys/{slug}/results", response_class=HTMLResponse)
async def results_owner(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return RedirectResponse("/admin", status_code=302)
    by = request.query_params.get("by") or None
    # Looking at one's own page as somebody else sees it. Nothing is faked: the
    # page is built for that audience, through the same filter it would get.
    # It is the only way to check what the public view actually exposes without
    # publishing it first and opening it in another browser.
    as_who = request.query_params.get("as")
    viewer = as_who if as_who in (aggregate.RESPONDENT, aggregate.PUBLIC) else aggregate.OWNER
    context = _results_context(request, db, survey, slug, viewer,
                               by=by if viewer == aggregate.OWNER else None)
    context["owner"] = True
    context["as_who"] = viewer if viewer != aggregate.OWNER else None
    db.close()
    return templates.TemplateResponse(request, "results.html", context)


@app.get("/admin/surveys/{slug}/explore.json")
async def explore_pair(slug: str, request: Request):
    """Two variables against each other, for the owner and nobody else.

    Not a route with an audience: a contingency table of gender against
    anything, on the samples these studies have, is the re-identification the
    rest of this page spends its effort preventing. There is no public form of
    this question.
    """
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "not signed in"}, status_code=401)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    schema = json.loads(survey["schema_json"])
    content = report_model.locales(schema)
    locale = _pick_locale(request, list(review_export.LANGS), default=content[0])
    responses = [json.loads(r["response_json"]) for r in db.execute(
        "SELECT response_json FROM responses WHERE survey_id = ?", (survey["id"],))]
    db.close()

    x, y = request.query_params.get("x", ""), request.query_params.get("y", "")
    out = explore.associate(schema, responses, x, y, locale)
    out["pairs_available"] = explore.pair_count(schema)
    return JSONResponse(out)


@app.get("/s/{slug}/explore.json")
async def explore_pair_public(slug: str, request: Request):
    """The same question, asked by a reader.

    Only when the report says readers may, only over the questions it already
    publishes, and with the cells of whatever comes back masked. The statistic
    is an aggregate and travels; the people behind the cells do not.
    """
    db = get_db()
    survey = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not survey:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    viewer, _ = _public_viewer(db, survey, request)
    schema = json.loads(survey["schema_json"])
    stored = report_model.normalise(survey["report_json"], schema)
    if viewer is None or not stored.get("explore"):
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    content = report_model.locales(schema)
    locale = _pick_locale(request, list(review_export.LANGS), default=content[0])
    responses = [json.loads(r["response_json"]) for r in db.execute(
        "SELECT response_json FROM responses WHERE survey_id = ?", (survey["id"],))]
    db.close()
    out = explore.associate(
        schema, responses,
        request.query_params.get("x", ""), request.query_params.get("y", ""),
        locale, audience=viewer,
        allowed=report_model.published_names(stored, schema, viewer))
    out["pairs_available"] = len(_explore_vars(schema, stored, viewer, locale))
    return JSONResponse(out)


@app.get("/admin/surveys/{slug}/results.json")
async def results_owner_json(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "not signed in"}, status_code=401)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    schema = json.loads(survey["schema_json"])
    content = report_model.locales(schema)
    locale = _pick_locale(request, list(review_export.LANGS), default=content[0])
    as_who = request.query_params.get("as")
    viewer = as_who if as_who in (aggregate.RESPONDENT, aggregate.PUBLIC) else aggregate.OWNER
    payload = _results_payload(db, survey, viewer, locale,
                               by=request.query_params.get("by") or None
                               if viewer == aggregate.OWNER else None)
    db.close()
    return JSONResponse({**payload, "updated_at": time.strftime("%H:%M")})


async def _public_results(slug: str, request: Request, embed: bool):
    db = get_db()
    survey = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not survey:
        db.close()
        return templates.TemplateResponse(request, "closed.html", {}, status_code=404)
    viewer, mine = _public_viewer(db, survey, request)
    if viewer is None:
        # A report nobody published and a survey that does not exist answer the
        # same way: there is nothing here to find, and no hint that there might be.
        db.close()
        return templates.TemplateResponse(request, "closed.html", {}, status_code=404)
    context = _results_context(request, db, survey, slug, viewer, mine=mine, embed=embed)
    db.close()
    return templates.TemplateResponse(request, "results.html", context)


@app.get("/s/{slug}/results", response_class=HTMLResponse)
async def results_public(slug: str, request: Request):
    return await _public_results(slug, request, embed=False)


@app.get("/s/{slug}/results/embed", response_class=HTMLResponse)
async def results_embed(slug: str, request: Request):
    return await _public_results(slug, request, embed=True)


@app.get("/s/{slug}/results.json")
async def results_public_json(slug: str, request: Request):
    db = get_db()
    survey = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not survey:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    viewer, mine = _public_viewer(db, survey, request)
    if viewer is None:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    schema = json.loads(survey["schema_json"])
    content = report_model.locales(schema)
    locale = _pick_locale(request, list(review_export.LANGS), default=content[0])
    payload = _results_payload(db, survey, viewer, locale, mine=mine)
    db.close()
    return JSONResponse({**payload, "updated_at": time.strftime("%H:%M")})


def _report_context(db, survey, slug: str, saved: bool = False) -> dict:
    """Everything report.html needs: the stored report, and the questionnaire
    described well enough that the editor can offer the right charts for each
    question without asking the server again."""
    schema = json.loads(survey["schema_json"])
    stored = report_model.normalise(survey["report_json"], schema)
    questions = []
    for page in schema.get("pages", []):
        for el in page.get("elements", []):
            if el.get("type") == "html":
                continue
            shape = aggregate.shape_of(el)
            questions.append({
                "name": el.get("name"),
                "title": review_export.loc_text(el.get("title")) or el.get("name"),
                "type": el.get("type"),
                "page": page.get("name"),
                "shape": shape,
                "charts": report_model.CHARTS.get(shape, ["table"]),
            })
    return {
        "title": survey["title"],
        "slug": slug,
        "active": bool(survey["active"]),
        "responses": _response_count(db, survey["id"]),
        "report": stored,
        # The link as saved, not as the selector currently reads: offering one
        # before the change is stored would hand out a URL that answers 404.
        "saved_audience": stored["audience"],
        "questions": questions,
        "locales": report_model.locales(schema),
        "findings": report_model.validate(stored, schema),
        # The editor is English like the rest of the admin, but this one switch
        # describes what readers of a localized page will get, so it borrows
        # their words.
        "t": results_view.strings(report_model.locales(schema)[0]),
        "saved": saved,
    }


@app.get("/admin/surveys/{slug}/report", response_class=HTMLResponse)
async def report_page(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return RedirectResponse("/admin", status_code=302)
    context = _report_context(db, survey, slug,
                              saved=request.query_params.get("saved") == "1")
    db.close()
    return templates.TemplateResponse(request, "report.html", context)


@app.post("/admin/surveys/{slug}/report")
async def save_report(slug: str, request: Request):
    """Store the report. The editor is JavaScript, so the payload is whatever
    the browser sent: it goes through normalise() before it is written, because
    what is written here is later served to people with no account."""
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "not signed in"}, status_code=401)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        db.close()
        return JSONResponse({"error": "body is not JSON"}, status_code=400)

    schema = json.loads(survey["schema_json"])
    clean = report_model.normalise(payload, schema)
    if clean["explore"] and clean["audience"] != aggregate.OWNER:
        log.info("reader exploration enabled on survey %s by user %s",
                 slug, user["id"])
    if clean["audience"] != aggregate.OWNER and survey["active"]:
        # Not a refusal: whoever runs the study decides. But it is written down,
        # because results visible while the field is open change what later
        # respondents answer, and nobody should discover that afterwards.
        log.info("report published (%s) on open survey %s by user %s",
                 clean["audience"], slug, user["id"])
    db.execute("UPDATE surveys SET report_json = ? WHERE id = ?",
               (json.dumps(clean, ensure_ascii=False), survey["id"]))
    db.commit()
    db.close()
    _invalidate_results(survey["id"])
    return JSONResponse({"ok": True, "report": clean,
                         "findings": report_model.validate(clean, schema)})


@app.get("/admin/surveys/{slug}/edit", response_class=HTMLResponse)
async def edit_survey_page(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    held = _response_count(db, row["id"]) if row else 0
    db.close()
    if not row:
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(request, "edit.html", {
        "slug": slug,
        "title": row["title"],
        "schema_json": json.dumps(json.loads(row["schema_json"]), indent=2, ensure_ascii=False),
        "responses_held": held,
    })


@app.post("/admin/surveys/{slug}/edit")
async def edit_survey(
    slug: str,
    request: Request,
    title: str = Form(...),
    schema_file: UploadFile = File(None),
    schema_text: str = Form(""),
    confirm_replace: str = Form(""),
):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if not row:
        db.close()
        return RedirectResponse("/admin", status_code=302)

    if schema_file and schema_file.filename:
        raw = await schema_file.read()
        schema_str = raw.decode("utf-8")
    elif schema_text.strip():
        schema_str = schema_text.strip()
    else:
        db.close()
        return RedirectResponse(f"/admin/surveys/{slug}/edit?error=no_schema", status_code=302)

    try:
        json.loads(schema_str)
    except json.JSONDecodeError:
        db.close()
        return RedirectResponse(f"/admin/surveys/{slug}/edit?error=invalid_json", status_code=302)

    # The same rule the MCP surface has always applied, on the surface a person
    # actually uses: answers already collected were given to the old wording, so
    # replacing the questionnaire underneath them changes what the data means,
    # and no backup brings back the wording a respondent read. Refused unless the
    # person ticks the box that names that consequence — and the refusal hands
    # back what was typed, because losing a pasted schema is not a warning.
    held = _response_count(db, row["id"])
    if held and not confirm_replace:
        db.close()
        return templates.TemplateResponse(request, "edit.html", {
            "slug": slug,
            "title": title,
            "schema_json": schema_str,
            "responses_held": held,
            "error_message": (
                f"This survey already holds {held} response"
                f"{'s' if held != 1 else ''}. Replacing the questionnaire was "
                f"refused: tick the confirmation below if that is intended. "
                f"Nothing was saved."
            ),
        }, status_code=400)

    db.execute(
        "UPDATE surveys SET title = ?, schema_json = ? WHERE slug = ?",
        (title, schema_str, slug),
    )
    db.commit()
    survey_id = row["id"]
    db.close()
    _invalidate_results(survey_id)
    if held:
        log.warning(
            "schema of survey %r replaced over %d collected response(s) "
            "by user %s <%s> (confirmed in the web form)",
            slug, held, user["id"], user["email"],
        )
    return RedirectResponse(f"/admin/surveys/{slug}", status_code=302)


@app.post("/admin/surveys/{slug}/toggle")
async def toggle_survey(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if _owned_survey(db, slug, user):
        db.execute("UPDATE surveys SET active = 1 - active WHERE slug = ?", (slug,))
        db.commit()
    db.close()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/surveys/{slug}/purge")
async def purge_responses(slug: str, request: Request):
    """Empty the survey of what it has collected, keeping the questionnaire.

    The balance ledger goes with the responses. Leaving it would count arms as
    completed by respondents who no longer exist, which is the opposite of what
    someone clearing test data before fielding is asking for; it is the same
    pair that `reset_counters` already treats as one thing.
    """
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if not row:
        db.close()
        return RedirectResponse("/admin", status_code=302)
    db.execute("DELETE FROM responses WHERE survey_id = ?", (row["id"],))
    for pool in db.execute(
        "SELECT id FROM rand_pools WHERE survey_id = ?", (row["id"],)
    ).fetchall():
        db.execute("DELETE FROM assignments WHERE pool_id = ?", (pool["id"],))
        db.execute("DELETE FROM assignment_counts WHERE pool_id = ?", (pool["id"],))
    db.commit()
    _invalidate_results(row["id"])
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}", status_code=302)


@app.post("/admin/surveys/{slug}/delete")
async def delete_survey(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    owned = _owned_survey(db, slug, user)
    if owned:
        db.execute("DELETE FROM surveys WHERE slug = ?", (slug,))
        db.commit()
        _invalidate_results(owned["id"])
        shutil.rmtree(os.path.join(UPLOADS_PATH, slug), ignore_errors=True)
    db.close()
    return RedirectResponse("/admin", status_code=302)


# --- admin randomization ---

def _schema_page_names(schema) -> list:
    """The page names a pool may refer to, named the way the runtime names them."""
    return [p.get("name", f"page{i+1}") for i, p in enumerate(schema.get("pages", []))]


def _randomization_context(db, survey, slug: str) -> dict:
    """Everything randomization.html renders: the schema's page names, and one
    entry per pool with its balance table.

    Split out of the GET handler so that a refused save can hand the same page
    back with the values that were typed, instead of redirecting to a fresh one.
    """
    page_names = _schema_page_names(json.loads(survey["schema_json"]))
    pools_raw = db.execute(
        "SELECT id, pool_name, pool_order, pool_pages, show_count, condition_var, "
        "condition_map, page_order FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (survey["id"],),
    ).fetchall()
    pools_data = []
    for pool in pools_raw:
        completed, pending = _pool_counts(db, pool["id"])
        keys = sorted(set(completed) | set(pending))
        counts = [{
            "condition_key": k,
            "count": completed.get(k, 0),
            "pending": pending.get(k, 0),
        } for k in keys]
        pools_data.append({
            "id": pool["id"],
            "pool_name": pool["pool_name"],
            "pool_pages": json.loads(pool["pool_pages"]),
            "show_count": pool["show_count"],
            "condition_var": pool["condition_var"] or "",
            "condition_map": pool["condition_map"] or "",
            "page_order": pool["page_order"] or "",
            "counts": counts,
            "total": sum(c["count"] for c in counts),
            "pending_total": sum(c["pending"] for c in counts),
        })
    return {
        "slug": slug,
        "title": survey["title"],
        "page_names": page_names,
        "pools": pools_data,
    }


@app.get("/admin/surveys/{slug}/randomization", response_class=HTMLResponse)
async def randomization_page(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    survey = _owned_survey(db, slug, user)
    if not survey:
        db.close()
        return RedirectResponse("/admin", status_code=302)
    context = _randomization_context(db, survey, slug)
    db.close()
    return templates.TemplateResponse(request, "randomization.html", context)


@app.post("/admin/surveys/{slug}/randomization/add-pool")
async def add_pool(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if row:
        order = db.execute(
            "SELECT COALESCE(MAX(pool_order)+1, 0) FROM rand_pools WHERE survey_id = ?", (row["id"],)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO rand_pools (survey_id, pool_name, pool_order) VALUES (?, ?, ?)",
            (row["id"], f"Pool {order + 1}", order),
        )
        db.commit()
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}/randomization", status_code=302)


def _pool_belongs(db, slug: str, pool_id: int, user) -> bool:
    survey = _owned_survey(db, slug, user)
    if not survey:
        return False
    row = db.execute("SELECT survey_id FROM rand_pools WHERE id = ?", (pool_id,)).fetchone()
    return bool(row and row["survey_id"] == survey["id"])


@app.post("/admin/surveys/{slug}/randomization/{pool_id}/save")
async def save_pool(slug: str, pool_id: int, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    survey = _owned_survey(db, slug, user)
    if not survey or not _pool_belongs(db, slug, pool_id, user):
        db.close()
        return RedirectResponse("/admin", status_code=302)
    form = await request.form()
    pool_pages = form.getlist("pool_pages")
    pool_name = form.get("pool_name", "Pool").strip() or "Pool"
    try:
        show_count = max(1, int(form.get("show_count", 1)))
    except ValueError:
        show_count = 1
    condition_var = form.get("condition_var", "").strip() or None
    cmap_raw = form.get("condition_map", "").strip()
    porder_raw = form.get("page_order", "").strip()

    def refuse(message: str):
        """Hand the page back, with the reason and with what was typed still in
        the fields.

        Discarding a rejected value silently — as this route did — shows the
        person an empty box after a save that reported success, which reads as
        data loss with no cause. Nothing is written on this path.
        """
        context = _randomization_context(db, survey, slug)
        db.close()
        for pool in context["pools"]:
            if pool["id"] == pool_id:
                pool.update({
                    "pool_name": pool_name,
                    "pool_pages": pool_pages,
                    "show_count": show_count,
                    "condition_var": condition_var or "",
                    "condition_map": cmap_raw,
                    "page_order": porder_raw,
                })
        context["error"] = message
        context["error_pool_id"] = pool_id
        return templates.TemplateResponse(
            request, "randomization.html", context, status_code=400)

    def where(exc: json.JSONDecodeError) -> str:
        return f"{exc.msg}, line {exc.lineno} column {exc.colno}"

    # condition_map and page_order only apply when show_count = 1 with a
    # condition variable; outside that they are dropped, as they always were.
    condition_map = None
    page_order = None
    if show_count == 1 and condition_var:
        if cmap_raw:
            try:
                json.loads(cmap_raw)
            except json.JSONDecodeError as exc:
                return refuse(f"Value map is not valid JSON — {where(exc)}. "
                              f"Nothing was saved.")
            condition_map = cmap_raw
        if porder_raw:
            try:
                parsed = json.loads(porder_raw)
            except json.JSONDecodeError as exc:
                return refuse(f"Page order is not valid JSON — {where(exc)}. "
                              f"Nothing was saved.")
            # {condition value: [page names in the order they should appear]}
            if not isinstance(parsed, dict) or not all(
                isinstance(v, list) for v in parsed.values()
            ):
                return refuse(
                    "Page order must be a JSON object mapping each condition "
                    "value to a list of page names, e.g. "
                    '{"CONDITION": ["first_page", "second_page"]}. '
                    "Nothing was saved.")
            # A name that no page carries is not an error the runtime can
            # report: the arm quietly keeps the schema order, and the mistake
            # only ever surfaced through the MCP validator. Caught here, where
            # it was typed.
            known = _schema_page_names(json.loads(survey["schema_json"]))
            for value, names in parsed.items():
                for name in names:
                    if name not in known:
                        return refuse(
                            f"Page order names a page this questionnaire does "
                            f"not have: {name!r}, under condition value "
                            f"{value!r}. Its pages are: "
                            f"{', '.join(map(str, known)) or '(none)'}. "
                            f"Nothing was saved.")
            page_order = porder_raw
    db.execute(
        "UPDATE rand_pools SET pool_name = ?, pool_pages = ?, show_count = ?, "
        "condition_var = ?, condition_map = ?, page_order = ? WHERE id = ?",
        (pool_name, json.dumps(pool_pages), show_count, condition_var,
         condition_map, page_order, pool_id),
    )
    # a configuration change invalidates the balance history for this pool
    db.execute("DELETE FROM assignments WHERE pool_id = ?", (pool_id,))
    db.execute("DELETE FROM assignment_counts WHERE pool_id = ?", (pool_id,))
    db.commit()
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}/randomization", status_code=302)


@app.post("/admin/surveys/{slug}/randomization/{pool_id}/delete")
async def delete_pool(slug: str, pool_id: int, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if _pool_belongs(db, slug, pool_id, user):
        db.execute("DELETE FROM rand_pools WHERE id = ?", (pool_id,))
        db.commit()
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}/randomization", status_code=302)


@app.post("/admin/surveys/{slug}/randomization/{pool_id}/reset")
async def reset_pool_counts(slug: str, pool_id: int, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if _pool_belongs(db, slug, pool_id, user):
        db.execute("DELETE FROM assignments WHERE pool_id = ?", (pool_id,))
        db.execute("DELETE FROM assignment_counts WHERE pool_id = ?", (pool_id,))
        db.commit()
    db.close()
    return RedirectResponse(f"/admin/surveys/{slug}/randomization", status_code=302)


# --- file uploads ---

def _safe_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


def _upload_dir(slug: str) -> str:
    path = os.path.join(UPLOADS_PATH, slug)
    os.makedirs(path, exist_ok=True)
    return path


@app.get("/admin/surveys/{slug}/files", response_class=HTMLResponse)
async def files_page(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    db.close()
    if not row:
        return RedirectResponse("/admin", status_code=302)
    d = _upload_dir(slug)
    files = sorted(os.listdir(d))
    return templates.TemplateResponse(request, "files.html", {
        "slug": slug,
        "title": row["title"],
        "files": files,
    })


@app.post("/admin/surveys/{slug}/upload")
async def upload_file(slug: str, request: Request, file: UploadFile = File(...)):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    owned = _owned_survey(db, slug, user)
    db.close()
    if not owned:
        return RedirectResponse("/admin", status_code=302)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return RedirectResponse(f"/admin/surveys/{slug}/files?error=ext", status_code=302)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return RedirectResponse(f"/admin/surveys/{slug}/files?error=size", status_code=302)

    dest = os.path.join(_upload_dir(slug), _safe_filename(file.filename))
    with open(dest, "wb") as f:
        f.write(content)

    return RedirectResponse(f"/admin/surveys/{slug}/files", status_code=302)


@app.post("/admin/surveys/{slug}/files/{filename}/delete")
async def delete_file(slug: str, filename: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    owned = _owned_survey(db, slug, user)
    db.close()
    if not owned:
        return RedirectResponse("/admin", status_code=302)
    safe = _safe_filename(filename)
    path = os.path.join(UPLOADS_PATH, slug, safe)
    if os.path.isfile(path):
        os.remove(path)
    return RedirectResponse(f"/admin/surveys/{slug}/files", status_code=302)


# --- export ---

def _flatten(data: dict, prefix: str = "") -> dict:
    result = {}
    for k, v in data.items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, key))
        elif isinstance(v, list):
            result[key] = ";".join(str(i) for i in v)
        else:
            result[key] = v
    return result


def _get_responses(db, slug: str):
    row = db.execute("SELECT id FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    return db.execute(
        "SELECT response_json, submitted_at FROM responses WHERE survey_id = ? ORDER BY submitted_at",
        (row["id"],),
    ).fetchall()


def _flat_table(rows):
    """(columns, flat_rows) shared by the CSV and Excel exports, so the two
    formats always agree on structure. Column order follows first appearance;
    _submitted_at goes last."""
    flat_rows = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()
    for r in rows:
        flat = _flatten(json.loads(r["response_json"]))
        flat["_submitted_at"] = r["submitted_at"]
        flat_rows.append(flat)
        for k in flat:
            if k not in seen_keys:
                seen_keys.add(k)
                all_keys.append(k)
    cols = [k for k in all_keys if k != "_submitted_at"] + ["_submitted_at"]
    return cols, flat_rows


def _build_xlsx(cols, flat_rows) -> bytes:
    """Excel workbook: frozen bold header, autofilter, native numeric types.
    Strings that look like formulas are forced to text — open-ended answers
    must never execute in a reviewer's Excel."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Responses"
    for j, k in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=j, value=k)
        cell.font = Font(bold=True)
    for i, flat in enumerate(flat_rows, start=2):
        for j, k in enumerate(cols, start=1):
            v = flat.get(k, "")
            cell = ws.cell(row=i, column=j)
            cell.value = v
            if isinstance(v, str) and v.startswith("="):
                cell.data_type = "s"
    for j, k in enumerate(cols, start=1):
        sample = [len(str(fr.get(k, ""))) for fr in flat_rows[:200]]
        ws.column_dimensions[get_column_letter(j)].width = min(60, max(10, len(k), *sample))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.get("/admin/surveys/{slug}/export.csv")
async def export_csv(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if not _owned_survey(db, slug, user):
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = _get_responses(db, slug)
    db.close()
    if rows is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not rows:
        return HTMLResponse("No responses yet.")

    cols, flat_rows = _flat_table(rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", restval="")
    writer.writeheader()
    writer.writerows(flat_rows)

    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{slug}.csv"'},
    )


@app.get("/admin/surveys/{slug}/export.xlsx")
async def export_xlsx(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if not _owned_survey(db, slug, user):
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = _get_responses(db, slug)
    db.close()
    if rows is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not rows:
        return HTMLResponse("No responses yet.")

    cols, flat_rows = _flat_table(rows)
    data = _build_xlsx(cols, flat_rows)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{slug}.xlsx"'},
    )


@app.get("/admin/surveys/{slug}/review.docx")
async def export_review_docx(slug: str, request: Request):
    """Word rendering of the questionnaire itself (not the responses), for
    circulating to reviewers: primary-language texts, answer formats,
    visibility logic, randomization pools, and translation coverage flags."""
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    if not row:
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    pool_rows = db.execute(
        "SELECT pool_name, pool_pages, show_count, condition_var, condition_map, page_order "
        "FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (row["id"],),
    ).fetchall()
    db.close()

    data = review_export.build_review_docx(
        json.loads(row["schema_json"]),
        review_export.pools_from_rows(pool_rows),
        row["title"],
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{slug}-review.docx"'},
    )


@app.get("/admin/surveys/{slug}/export.json")
async def export_json(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if not _owned_survey(db, slug, user):
        db.close()
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = _get_responses(db, slug)
    db.close()
    if rows is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    data = [
        {"submitted_at": r["submitted_at"], "data": json.loads(r["response_json"])}
        for r in rows
    ]
    return StreamingResponse(
        io.BytesIO(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{slug}.json"'},
    )


# --- profile (self-service) ---

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, ok: str = "", error: str = ""):
    db = get_db()
    user = auth.current_user(request, db)
    db.close()
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "ok": ok, "error": error,
    })


@app.post("/profile/password")
async def profile_password(
    request: Request,
    current: str = Form(...),
    new_password: str = Form(...),
):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if not auth.verify_password(current, user["hashed_password"]):
        db.close()
        return RedirectResponse("/profile?error=current", status_code=302)
    if len(new_password) < 8:
        db.close()
        return RedirectResponse("/profile?error=short", status_code=302)
    db.execute("UPDATE users SET hashed_password = ? WHERE id = ?",
               (auth.hash_password(new_password), user["id"]))
    db.commit()
    db.close()
    return RedirectResponse("/profile?ok=password", status_code=302)


@app.post("/api/profile/backup-codes")
async def regenerate_backup_codes(request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not user["totp_enabled"]:
        db.close()
        return JSONResponse({"error": "2FA not enabled"}, status_code=400)
    plain, hashes = totp.generate_backup_codes()
    db.execute("UPDATE users SET backup_codes_json = ? WHERE id = ?",
               (json.dumps(hashes), user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"backup_codes": plain})


# --- admin: user management ---

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, tmp_uid: int = 0, tmp_password: str = ""):
    db = get_db()
    user = auth.current_user(request, db)
    if not user or not user["is_admin"]:
        db.close()
        return RedirectResponse("/admin" if user else "/login", status_code=302)
    users = db.execute("""
        SELECT u.*, COUNT(s.id) AS survey_count
        FROM users u
        LEFT JOIN surveys s ON s.owner_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at
    """).fetchall()
    db.close()
    return templates.TemplateResponse(request, "admin_users.html", {
        "user": user, "users": users,
        "tmp_uid": tmp_uid, "tmp_password": tmp_password,
    })


def _admin_or_none(request, db):
    user = auth.current_user(request, db)
    if not user or not user["is_admin"]:
        return None
    return user


@app.post("/admin/users/{uid}/toggle-active")
async def admin_toggle_active(uid: int, request: Request):
    db = get_db()
    admin = _admin_or_none(request, db)
    if not admin:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if uid != admin["id"]:  # never lock yourself out
        db.execute("UPDATE users SET is_active = 1 - is_active WHERE id = ?", (uid,))
        db.commit()
    db.close()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/users/{uid}/reset-2fa")
async def admin_reset_2fa(uid: int, request: Request):
    """Clear a user's 2FA so they re-enrol on next login (lost-device recovery)."""
    db = get_db()
    admin = _admin_or_none(request, db)
    if not admin:
        db.close()
        return RedirectResponse("/login", status_code=302)
    db.execute(
        "UPDATE users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_json = NULL WHERE id = ?",
        (uid,),
    )
    db.commit()
    db.close()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: int, request: Request):
    """Set a fresh temporary password, shown once to the admin to hand over."""
    db = get_db()
    admin = _admin_or_none(request, db)
    if not admin:
        db.close()
        return RedirectResponse("/login", status_code=302)
    target = db.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        db.close()
        return RedirectResponse("/admin/users", status_code=302)
    temp = secrets.token_urlsafe(9)
    db.execute("UPDATE users SET hashed_password = ? WHERE id = ?",
               (auth.hash_password(temp), uid))
    db.commit()
    db.close()
    return RedirectResponse(f"/admin/users?tmp_uid={uid}&tmp_password={temp}", status_code=302)
