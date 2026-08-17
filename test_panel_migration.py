"""Migration of an existing deployment, plus template rendering."""
import json, os, sqlite3, sys, tempfile

tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "survey.db")
os.environ["DB_PATH"] = DB
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

# --- build a database in the pre-panel shape ---
os.makedirs(os.path.dirname(DB), exist_ok=True)
old = sqlite3.connect(DB)
old.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, hashed_password TEXT NOT NULL, totp_secret_encrypted TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0, backup_codes_json TEXT,
        is_admin INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE surveys (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL, schema_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE responses (id INTEGER PRIMARY KEY AUTOINCREMENT,
        survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
        response_json TEXT NOT NULL, submitted_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE rand_pools (id INTEGER PRIMARY KEY AUTOINCREMENT,
        survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
        pool_name TEXT NOT NULL DEFAULT 'Pool', pool_order INTEGER NOT NULL DEFAULT 0,
        pool_pages TEXT NOT NULL DEFAULT '[]', show_count INTEGER NOT NULL DEFAULT 1,
        condition_var TEXT NULL, condition_map TEXT NULL);
    CREATE TABLE assignment_counts (pool_id INTEGER NOT NULL REFERENCES rand_pools(id) ON DELETE CASCADE,
        condition_key TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (pool_id, condition_key));
""")
schema = {"pages": [{"name": "info_a", "elements": []}, {"name": "info_b", "elements": []}]}
old.execute("INSERT INTO surveys (slug,title,schema_json) VALUES ('legacy','Legacy',?)",
            (json.dumps(schema),))
old.execute("INSERT INTO rand_pools (survey_id,pool_pages,show_count) VALUES (1,?,1)",
            (json.dumps(["info_a", "info_b"]),))
old.execute("INSERT INTO responses (survey_id,response_json) VALUES (1,'{\"q\":1}')")
old.executemany("INSERT INTO assignment_counts VALUES (1,?,?)", [("info_a", 12), ("info_b", 9)])
old.commit(); old.close()

import main
main.init_db()

db = main.get_db()
print("\n--- migration of an existing deployment ---")
cols = {r[1] for r in db.execute("PRAGMA table_info(surveys)")}
ok({"panel_token_param", "panel_complete_url", "panel_screenout_url",
    "panel_quotafull_url"} <= cols, "panel columns added to surveys")
ok("panel_token" in {r[1] for r in db.execute("PRAGMA table_info(responses)")},
   "panel_token added to responses")
rows = dict(db.execute(
    "SELECT condition_key, COUNT(*) FROM assignments WHERE completed=1 GROUP BY condition_key"))
ok(rows == {"info_a": 12, "info_b": 9}, f"old counters backfilled as completed: {rows}")
ok(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 1, "existing response preserved")
completed, pending = main._pool_counts(db, 1)
ok(completed == {"info_a": 12, "info_b": 9} and not any(pending.values()),
   "balancing reads the backfilled history")
ok(main._panel_config(db.execute("SELECT * FROM surveys WHERE slug='legacy'").fetchone()) is None,
   "panel mode off by default after migration")

# idempotence: a second startup must not double the backfill
db.close()
main.init_db()
db = main.get_db()
rows2 = dict(db.execute(
    "SELECT condition_key, COUNT(*) FROM assignments WHERE completed=1 GROUP BY condition_key"))
ok(rows2 == rows, "re-running init_db does not duplicate the backfill")

# two NULL-token responses must coexist under the unique index
db.execute("INSERT INTO responses (survey_id,response_json) VALUES (1,'{}')")
db.execute("INSERT INTO responses (survey_id,response_json) VALUES (1,'{}')")
db.commit()
ok(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 3,
   "unique index does not block non-panel responses")
db.close()

print("\n--- templates ---")
for name in ("manage.html", "randomization.html", "survey.html", "panel_stop.html", "closed.html"):
    try:
        main.templates.get_template(name)
        ok(True, f"{name} compiles")
    except Exception as e:
        ok(False, f"{name} compiles ({e})")

t = main.templates.get_template("manage.html")
for label, panel in (("panel off", {}),
                     ("panel on", {"param": "RID", "complete": "https://p/c",
                                   "screenout": "https://p/s", "quotafull": ""})):
    html = t.render(request=None, user={"name": "x"}, slug="t", title="T", active=1,
                    created_at="", owner_email="a@b.c", response_count=3, last_response="",
                    pool_count=1, files_count=0, langs=["en"], public_url="https://s/s/t",
                    qr="", panel=panel, panel_error="", panel_saved=False)
    ok("Panel recruitment" in html, f"manage.html renders ({label})")
    ok(("Entry URL to give the provider" in html) == bool(panel), f"entry-URL box gated ({label})")
    # both help modals ship regardless of whether panel mode is on
    ok('id="panel-help"' in html and 'id="config-help"' in html,
       f"both help modals present ({label})")
    ok("openHelp('panel-help')" in html and "openHelp('config-help')" in html,
       f"both ? buttons wired ({label})")
    ok("Routing a respondent who does not complete" in html and "_outcome" in html,
       f"panel modal documents the outcome routing ({label})")
    ok("balanced allocation" in html.lower() and "/uploads/t/" in html,
       f"config modal documents randomization and files ({label})")
    ok("pseudonymous identifier" in html, f"panel modal carries the ethics note ({label})")
    # the entry-URL example must fall back to a placeholder when panel mode is off
    expected = (panel.get("param") or "RID") + "=[respondent id]"
    ok(expected in html, f"entry-URL example uses {expected.split('=')[0]} ({label})")

t = main.templates.get_template("randomization.html")
html = t.render(request=None, slug="t", title="T", page_names=["info_a"], pools=[{
    "id": 1, "pool_name": "P", "pool_pages": ["info_a", "info_b"], "show_count": 1,
    "condition_var": "condition", "condition_map": "", "total": 21, "pending_total": 2,
    "counts": [{"condition_key": "info_a", "count": 12, "pending": 2},
               {"condition_key": "info_b", "count": 9, "pending": 0}]}])
ok("21 completed" in html and "2 in progress" in html, "randomization.html shows both counts")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}"))
sys.exit(1 if FAILED else 0)
