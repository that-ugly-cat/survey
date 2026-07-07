import csv
import io
import itertools
import json
import os
import random
import re
import secrets
import shutil
import sqlite3

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import auth
import crypto
import totp

DB_PATH = os.getenv("DB_PATH", "/data/survey.db")
UPLOADS_PATH = os.getenv("UPLOADS_PATH", "/data/uploads")

# Bootstrap admin — created once on first run, then owns any pre-existing surveys.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@survey.local").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI()
templates = Jinja2Templates(directory="templates")


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
            condition_map  TEXT NULL
        );
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
    db.commit()

    _bootstrap_admin(db)
    db.close()


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


@app.on_event("startup")
def startup():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOADS_PATH, exist_ok=True)
    init_db()
    app.mount("/uploads", StaticFiles(directory=UPLOADS_PATH), name="uploads")


# --- auth helpers ---

def _owned_survey(db, slug: str, user):
    """The survey row if `user` may manage it (owner or admin), else None."""
    row = db.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    if user["is_admin"] or row["owner_id"] == user["id"]:
        return row
    return None


# --- auth routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: int = 0):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", (email.strip().lower(),)
    ).fetchone()
    db.close()
    if not user or not auth.verify_password(password, user["hashed_password"]):
        return RedirectResponse("/login?error=1", status_code=302)
    # password ok → pending session; full access only after the 2FA step
    response = RedirectResponse("/2fa", status_code=302)
    auth.set_session(response, user["id"], "pending_2fa")
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, error: str = ""):
    return templates.TemplateResponse("register.html", {"request": request, "error": error})


@app.post("/register")
async def register(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    name = name.strip()
    if not EMAIL_RE.match(email):
        return RedirectResponse("/register?error=email", status_code=302)
    if len(password) < 8:
        return RedirectResponse("/register?error=pwd", status_code=302)
    if not name:
        return RedirectResponse("/register?error=name", status_code=302)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (email, name, hashed_password) VALUES (?, ?, ?)",
            (email, name, auth.hash_password(password)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        return RedirectResponse("/register?error=taken", status_code=302)
    user_id = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    db.close()
    # accounts are active immediately, but 2FA enrolment is mandatory before use
    response = RedirectResponse("/2fa", status_code=302)
    auth.set_session(response, user_id, "pending_2fa")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
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
    return templates.TemplateResponse("twofa.html", {
        "request": request,
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

def _assign_condition(db, pool_id: int, pool: list, show_count: int) -> list:
    all_conditions = [
        ",".join(sorted(combo))
        for combo in itertools.combinations(pool, show_count)
    ]
    rows = db.execute(
        "SELECT condition_key, count FROM assignment_counts WHERE pool_id = ?",
        (pool_id,),
    ).fetchall()
    counts = {r["condition_key"]: r["count"] for r in rows}
    min_count = min((counts.get(c, 0) for c in all_conditions), default=0)
    candidates = [c for c in all_conditions if counts.get(c, 0) == min_count]
    chosen = random.choice(candidates)
    db.execute(
        """INSERT INTO assignment_counts (pool_id, condition_key, count) VALUES (?, ?, 1)
           ON CONFLICT (pool_id, condition_key) DO UPDATE SET count = count + 1""",
        (pool_id, chosen),
    )
    db.commit()
    return chosen.split(",")


# --- public routes ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    db.close()
    if user:
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/s/{slug}", response_class=HTMLResponse)
async def survey_page(request: Request, slug: str):
    db = get_db()
    row = db.execute(
        "SELECT id, title, schema_json FROM surveys WHERE slug = ? AND active = 1", (slug,)
    ).fetchone()
    if not row:
        db.close()
        return templates.TemplateResponse("closed.html", {"request": request}, status_code=404)

    assigned_pages_list: list = []
    pool_pages_list: list = []
    conditions: dict = {}
    pools = db.execute(
        "SELECT id, pool_pages, show_count, condition_var, condition_map FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (row["id"],),
    ).fetchall()
    for p in pools:
        pool = json.loads(p["pool_pages"])
        sc = p["show_count"]
        if pool and 0 < sc <= len(pool):
            pool_pages_list.extend(pool)
            assigned = _assign_condition(db, p["id"], pool, sc)
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
    assigned_pages = assigned_pages_list or None
    pool_pages = pool_pages_list or None
    db.close()

    return templates.TemplateResponse("survey.html", {
        "request": request,
        "title": row["title"],
        "slug": slug,
        "schema": json.loads(row["schema_json"]),
        "assigned_pages": assigned_pages,
        "pool_pages": pool_pages,
        "conditions": conditions,
    })


@app.post("/s/{slug}/submit")
async def submit(slug: str, request: Request):
    db = get_db()
    row = db.execute(
        "SELECT id, active FROM surveys WHERE slug = ?", (slug,)
    ).fetchone()
    if not row or not row["active"]:
        db.close()
        return JSONResponse({"error": "survey not found or closed"}, status_code=404)
    data = await request.json()
    db.execute(
        "INSERT INTO responses (survey_id, response_json) VALUES (?, ?)",
        (row["id"], json.dumps(data, ensure_ascii=False)),
    )
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


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
    return templates.TemplateResponse("admin.html", {
        "request": request, "surveys": surveys, "user": user,
    })


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


@app.get("/admin/surveys/{slug}/edit", response_class=HTMLResponse)
async def edit_survey_page(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    row = _owned_survey(db, slug, user)
    db.close()
    if not row:
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("edit.html", {
        "request": request,
        "slug": slug,
        "title": row["title"],
        "schema_json": json.dumps(json.loads(row["schema_json"]), indent=2, ensure_ascii=False),
    })


@app.post("/admin/surveys/{slug}/edit")
async def edit_survey(
    slug: str,
    request: Request,
    title: str = Form(...),
    schema_file: UploadFile = File(None),
    schema_text: str = Form(""),
):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if not _owned_survey(db, slug, user):
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

    db.execute(
        "UPDATE surveys SET title = ?, schema_json = ? WHERE slug = ?",
        (title, schema_str, slug),
    )
    db.commit()
    db.close()
    return RedirectResponse("/admin", status_code=302)


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


@app.post("/admin/surveys/{slug}/delete")
async def delete_survey(slug: str, request: Request):
    db = get_db()
    user = auth.current_user(request, db)
    if not user:
        db.close()
        return RedirectResponse("/login", status_code=302)
    if _owned_survey(db, slug, user):
        db.execute("DELETE FROM surveys WHERE slug = ?", (slug,))
        db.commit()
        shutil.rmtree(os.path.join(UPLOADS_PATH, slug), ignore_errors=True)
    db.close()
    return RedirectResponse("/admin", status_code=302)


# --- admin randomization ---

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
    schema = json.loads(survey["schema_json"])
    page_names = [p.get("name", f"page{i+1}") for i, p in enumerate(schema.get("pages", []))]
    pools_raw = db.execute(
        "SELECT id, pool_name, pool_order, pool_pages, show_count, condition_var, condition_map FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (survey["id"],),
    ).fetchall()
    pools_data = []
    for pool in pools_raw:
        counts = db.execute(
            "SELECT condition_key, count FROM assignment_counts WHERE pool_id = ? ORDER BY condition_key",
            (pool["id"],),
        ).fetchall()
        pools_data.append({
            "id": pool["id"],
            "pool_name": pool["pool_name"],
            "pool_pages": json.loads(pool["pool_pages"]),
            "show_count": pool["show_count"],
            "condition_var": pool["condition_var"] or "",
            "condition_map": pool["condition_map"] or "",
            "counts": counts,
            "total": sum(r["count"] for r in counts),
        })
    db.close()
    return templates.TemplateResponse("randomization.html", {
        "request": request,
        "slug": slug,
        "title": survey["title"],
        "page_names": page_names,
        "pools": pools_data,
    })


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
    if not _pool_belongs(db, slug, pool_id, user):
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
    # condition_map only valid when show_count=1; validate JSON if provided
    condition_map = None
    if show_count == 1 and condition_var:
        cmap_raw = form.get("condition_map", "").strip()
        if cmap_raw:
            try:
                json.loads(cmap_raw)
                condition_map = cmap_raw
            except json.JSONDecodeError:
                pass  # silently discard invalid JSON
    db.execute(
        "UPDATE rand_pools SET pool_name = ?, pool_pages = ?, show_count = ?, condition_var = ?, condition_map = ? WHERE id = ?",
        (pool_name, json.dumps(pool_pages), show_count, condition_var, condition_map, pool_id),
    )
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
    return templates.TemplateResponse("files.html", {
        "request": request,
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

    # _submitted_at last
    cols = [k for k in all_keys if k != "_submitted_at"] + ["_submitted_at"]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", restval="")
    writer.writeheader()
    writer.writerows(flat_rows)

    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{slug}.csv"'},
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
    return templates.TemplateResponse("profile.html", {
        "request": request, "user": user, "ok": ok, "error": error,
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
    return templates.TemplateResponse("admin_users.html", {
        "request": request, "user": user, "users": users,
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
