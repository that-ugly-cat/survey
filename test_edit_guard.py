"""Check that replacing a questionnaire over collected answers is refused on the
web surface too, and only goes through when the person says so in the form.

The MCP `update_schema` has always refused this without `force`; the admin form,
which is where the change is actually made, had no check at all. These are the
cases that say the two surfaces now hold the same rule.
"""
import json, logging, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
# Same reason as test_purge: this test signs its own cookie, and a container
# built for the gateway would bounce every request to /login and pass the guard
# checks for the wrong reason.
os.environ["AUTH_MODE"] = "local"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main, auth

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

OLD = {"pages": [{"name": "p1", "elements": [{"type": "text", "name": "q1",
                                             "title": "How old are you?"}]}]}
NEW = {"pages": [{"name": "p1", "elements": [{"type": "text", "name": "q1",
                                             "title": "What is your age?"}]}]}


class Collect(logging.Handler):
    """The application log is the only durable record of a forced replacement,
    so the test reads it rather than trusting that the call was made."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def stored(slug):
    db = main.get_db()
    row = db.execute("SELECT title, schema_json FROM surveys WHERE slug = ?", (slug,)).fetchone()
    db.close()
    return row["title"], json.loads(row["schema_json"])


def seed(slug, owner_id, n_responses):
    db = main.get_db()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?,?,?,?)",
               (slug, "Before", json.dumps(OLD), owner_id))
    sid = db.execute("SELECT id FROM surveys WHERE slug = ?", (slug,)).fetchone()["id"]
    for i in range(n_responses):
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps({"q1": str(30 + i)})))
    db.commit(); db.close()


with TestClient(main.app) as client:
    db = main.get_db()
    for email in ("owner@x.test", "other@x.test"):
        db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
                   (email, email, "x"))
    owner_id = db.execute("SELECT id FROM users WHERE email='owner@x.test'").fetchone()["id"]
    other_id = db.execute("SELECT id FROM users WHERE email='other@x.test'").fetchone()["id"]
    db.commit(); db.close()

    owner = {"session": auth.make_token(owner_id, "full")}
    other = {"session": auth.make_token(other_id, "full")}

    seed("held", owner_id, 3)    # three answers already given to the old wording
    seed("empty", owner_id, 0)   # nothing collected yet

    def post(slug, cookies=owner, **over):
        form = {"title": "After", "schema_text": json.dumps(NEW)}
        form.update(over)
        return client.post(f"/admin/surveys/{slug}/edit", data=form,
                           cookies=cookies, follow_redirects=False)

    print("\n--- with answers already collected, an unconfirmed replacement is refused ---")
    r = post("held")
    ok(r.status_code == 400, f"the save is refused (got {r.status_code})")
    title, schema = stored("held")
    ok(schema == OLD, "the questionnaire on disk is untouched")
    ok(title == "Before", "and so is the title — the refusal is of the whole save")
    ok("3 response" in r.text, "the message names how many answers are held")
    ok("refused" in r.text, "and says the save did not happen")
    ok("What is your age?" in r.text,
       "the submitted schema is handed back, so a paste is not lost")
    ok('name="confirm_replace"' in r.text, "the confirmation box is offered")

    print("\n--- the checkbox carries the consequence, not a generic warning ---")
    r = client.get("/admin/surveys/held/edit", cookies=owner)
    ok('name="confirm_replace"' in r.text, "the edit page shows the box before anything is typed")
    ok("cannot be undone from a backup" in r.text, "the label states the consequence")
    ok("3 response" in r.text, "and the page names the count")
    ok('type="checkbox" name="confirm_replace"' in r.text,
       "it is a checkbox, so it has to be ticked")

    print("\n--- confirmed, it goes through, and the app says so where that lasts ---")
    handler = Collect()
    main.log.addHandler(handler)
    try:
        r = post("held", confirm_replace="1")
    finally:
        main.log.removeHandler(handler)
    ok(r.status_code == 302 and r.headers["location"] == "/admin/surveys/held",
       "the save returns to the manage page")
    title, schema = stored("held")
    ok(schema == NEW and title == "After", "the new questionnaire is stored")
    forced = [ln for ln in handler.lines if "replaced over" in ln]
    ok(len(forced) == 1, f"one log line records the replacement (got {handler.lines})")
    ok(forced and "3 collected response" in forced[0], "it names how many answers it went over")
    ok(forced and "'held'" in forced[0] and "owner@x.test" in forced[0],
       "and which survey, and who did it")

    print("\n--- an empty survey is not asked to confirm anything ---")
    r = client.get("/admin/surveys/empty/edit", cookies=owner)
    ok('name="confirm_replace"' not in r.text, "no checkbox with nothing to lose")
    handler = Collect()
    main.log.addHandler(handler)
    try:
        r = post("empty")
    finally:
        main.log.removeHandler(handler)
    ok(r.status_code == 302, "the save goes through unconfirmed")
    ok(stored("empty")[1] == NEW, "and the schema is replaced")
    ok(not [ln for ln in handler.lines if "replaced over" in ln],
       "nothing is logged: no answers were reinterpreted")

    print("\n--- the guard does not replace the ownership check ---")
    r = post("held", cookies=other, confirm_replace="1")
    ok(r.status_code == 302 and r.headers["location"] == "/admin",
       "a non-owner is bounced to /admin even with the box ticked")
    ok(stored("held")[1] == NEW, "and changed nothing")

print("\n" + (f"{len(FAILED)} FAILED: " + "; ".join(FAILED) if FAILED else "ALL PASS"))
sys.exit(1 if FAILED else 0)
