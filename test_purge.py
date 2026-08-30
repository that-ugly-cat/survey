"""Check that purging answers empties the survey without touching the instrument."""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main, auth

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def counts(slug):
    """(responses, assignments, assignment_counts) for one survey."""
    db = main.get_db()
    sid = db.execute("SELECT id FROM surveys WHERE slug = ?", (slug,)).fetchone()["id"]
    n_r = db.execute("SELECT COUNT(*) FROM responses WHERE survey_id = ?", (sid,)).fetchone()[0]
    n_a = db.execute(
        "SELECT COUNT(*) FROM assignments WHERE pool_id IN "
        "(SELECT id FROM rand_pools WHERE survey_id = ?)", (sid,)).fetchone()[0]
    n_c = db.execute(
        "SELECT COUNT(*) FROM assignment_counts WHERE pool_id IN "
        "(SELECT id FROM rand_pools WHERE survey_id = ?)", (sid,)).fetchone()[0]
    db.close()
    return n_r, n_a, n_c


def seed(slug, owner_id, n_responses=3):
    db = main.get_db()
    schema = {"pages": [
        {"name": "info_a", "elements": [{"type": "text", "name": "q1"}]},
        {"name": "info_b", "elements": [{"type": "text", "name": "q1"}]},
    ]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?,?,?,?)",
               (slug, slug.upper(), json.dumps(schema), owner_id))
    sid = db.execute("SELECT id FROM surveys WHERE slug = ?", (slug,)).fetchone()["id"]
    db.execute("INSERT INTO rand_pools (survey_id, pool_pages, show_count) VALUES (?,?,1)",
               (sid, json.dumps(["info_a", "info_b"])))
    pid = db.execute("SELECT id FROM rand_pools WHERE survey_id = ?", (sid,)).fetchone()["id"]
    for i in range(n_responses):
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps({"q1": f"answer {i}"})))
        db.execute("INSERT INTO assignments (pool_id, condition_key, completed) VALUES (?,?,1)",
                   (pid, "info_a" if i % 2 else "info_b"))
    db.execute("INSERT INTO assignment_counts (pool_id, condition_key, count) VALUES (?,?,?)",
               (pid, "info_a", n_responses))
    db.commit(); db.close()
    return sid


with TestClient(main.app) as client:
    db = main.get_db()
    for email in ("owner@x.test", "other@x.test"):
        db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
                   (email, email, "x"))
    owner_id = db.execute("SELECT id FROM users WHERE email='owner@x.test'").fetchone()["id"]
    other_id = db.execute("SELECT id FROM users WHERE email='other@x.test'").fetchone()["id"]
    db.commit(); db.close()

    owner_cookie = {"session": auth.make_token(owner_id, "full")}
    other_cookie = {"session": auth.make_token(other_id, "full")}
    pending_cookie = {"session": auth.make_token(owner_id, "pending")}

    seed("mine", owner_id)
    seed("theirs", other_id)

    print("\n--- the guard: who may purge ---")
    r = client.post("/admin/surveys/mine/purge", follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/login", "signed out redirects to /login")
    ok(counts("mine")[0] == 3, "signed out deleted nothing")

    r = client.post("/admin/surveys/mine/purge", cookies=pending_cookie, follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/login", "half-authenticated (pre-TOTP) redirects to /login")
    ok(counts("mine")[0] == 3, "half-authenticated deleted nothing")

    r = client.post("/admin/surveys/mine/purge", cookies=other_cookie, follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/admin", "a non-owner is bounced to /admin")
    ok(counts("mine")[0] == 3, "a non-owner deleted nothing")

    r = client.post("/admin/surveys/nosuch/purge", cookies=owner_cookie, follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/admin", "unknown slug is bounced to /admin")

    print("\n--- what a purge removes, and what it leaves ---")
    before = counts("mine")
    ok(before == (3, 3, 1), f"seeded: 3 responses, 3 assignments, 1 legacy counter (got {before})")

    r = client.post("/admin/surveys/mine/purge", cookies=owner_cookie, follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/admin/surveys/mine",
       "purge returns to the manage page")
    ok(counts("mine") == (0, 0, 0), "responses, assignments and legacy counters all gone")

    db = main.get_db()
    row = db.execute("SELECT title, schema_json, active FROM surveys WHERE slug='mine'").fetchone()
    pools = db.execute(
        "SELECT COUNT(*) FROM rand_pools WHERE survey_id = "
        "(SELECT id FROM surveys WHERE slug='mine')").fetchone()[0]
    db.close()
    ok(row is not None, "the survey itself survives")
    ok(len(json.loads(row["schema_json"])["pages"]) == 2, "the questionnaire is untouched")
    ok(pools == 1, "the pool configuration is untouched")

    ok(counts("theirs") == (3, 3, 1), "another survey's data is untouched")

    print("\n--- purging twice is not an error ---")
    r = client.post("/admin/surveys/mine/purge", cookies=owner_cookie, follow_redirects=False)
    ok(r.status_code == 302 and counts("mine") == (0, 0, 0), "second purge is a no-op")

    print("\n--- the button on the manage page ---")
    r = client.get("/admin/surveys/mine", cookies=owner_cookie)
    ok("Purge answers" in r.text, "the button is rendered")
    ok('action="/admin/surveys/mine/purge"' in r.text, "it posts to the purge route")
    ok(r.text.index("Purge answers") < r.text.index("Delete survey"),
       "it sits before Delete survey in the danger zone")
    ok("disabled>Purge answers" in r.text, "at zero responses the button is disabled")

    r = client.get("/admin/surveys/theirs", cookies=other_cookie)
    ok("disabled>Purge answers" not in r.text, "with responses to purge the button is live")
    ok("3 responses" in r.text, "the copy names the count")

print("\n" + (f"{len(FAILED)} FAILED: " + "; ".join(FAILED) if FAILED else "ALL PASS"))
sys.exit(1 if FAILED else 0)
