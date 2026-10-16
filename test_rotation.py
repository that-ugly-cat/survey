"""Checks for the rotation helper (docs/item-banks-ui-spec.md, phase 2): the expression
rotation.py builds and recognises, the expected shares, the validation, and the admin
route that writes the calculated value, refused over collected answers unless confirmed."""
import json, logging, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main
import rotation

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

print("\n--- expression ---")
ARMS = ["F1", "F2", "F3"]
PROMETEO = "iif(({codice_giorno} + 1) % 3 = 0, 'F1', iif(({codice_giorno} + 1) % 3 = 1, 'F2', 'F3'))"
ok(rotation.build_expression("codice_giorno", 1, ARMS) == PROMETEO, "the expression is the one PROMETEO's wave 1 uses")
ok(rotation.parse_expression(PROMETEO) == {"question": "codice_giorno", "wave": 1, "arms": ARMS}, "and it is recognised")
ok(rotation.build_expression("d", 2, ["A", "B"]) == "iif(({d} + 2) % 2 = 0, 'A', 'B')", "two groups")
ok(rotation.parse_expression("iif(({d} + 2) % 2 = 0, 'A', 'B') ") is None, "a near miss is not recognised")
ok(rotation.parse_expression("{d} % 3") is None and rotation.parse_expression("") is None, "nor is a hand-written one")

print("\n--- rotation semantics ---")
def arm_at(day, wave):
    return ARMS[(day + wave) % 3]
ok(all(ARMS.index(arm_at(d, w + 1)) == (ARMS.index(arm_at(d, w)) + 1) % 3 for d in range(1, 32) for w in (1, 2)),
   "for every day of birth, each wave gives the next group")
ok(all(len({arm_at(d, w) for w in (1, 2, 3)}) == 3 for d in range(1, 32)), "over three waves everyone sees every group once")

print("\n--- shares ---")
s = rotation.expected_shares({"min": 1, "max": 31}, 1, ARMS)
ok(s == {"F1": 32.6, "F2": 32.6, "F3": 34.8}, f"a day of the month is weighted by the calendar ({s})")
ok(rotation.expected_shares({"min": 0, "max": 5}, 1, ["A", "B", "C"]) == {"A": 33.3, "B": 33.3, "C": 33.3}, "a bounded integer is uniform")
ok(rotation.expected_shares({}, 1, ARMS) is None, "no range, no estimate")

print("\n--- validation ---")
SCHEMA = {"pages": [{"name": "code", "elements": [{"type": "text", "name": "day", "inputType": "number", "min": 1, "max": 31}]},
                    {"name": "p", "elements": [{"type": "rating", "name": "p_x"}]}]}
base = {"name": "form", "question": "day", "wave": 1, "arms": ARMS}
codes = lambda **o: [c for c, _ in rotation.validate(dict(base, **o), SCHEMA)]
ok(codes() == [], "a valid request passes")
ok("bad_name" in codes(name="1x") and "name_taken" in codes(name="day"), "the value name must be new and well formed")
ok("unknown_question" in codes(question="nope"), "the question must exist")
ok("bad_wave" in codes(wave=0) and "bad_wave" in codes(wave="x"), "the wave is a whole number from 1")
ok("few_arms" in codes(arms=["A"]) and "bad_arms" in codes(arms=["A", "A"]) and "bad_arms" in codes(arms=["A", "B'"]),
   "groups: at least two, distinct, no quotes")
new, expr, replaced = rotation.apply(SCHEMA, base)
ok(new["calculatedValues"] == [{"name": "form", "expression": expr, "includeIntoResult": True}] and not replaced,
   "apply adds a saved calculated value")
again, _, replaced2 = rotation.apply(new, dict(base, wave=2))
ok(len(again["calculatedValues"]) == 1 and replaced2 and "+ 2)" in again["calculatedValues"][0]["expression"],
   "applying again replaces the value of the same name")
ok("calculatedValues" not in SCHEMA, "the schema passed in is not changed")

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("rw", "Rotation", json.dumps(SCHEMA), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='rw'").fetchone()["id"]
    db.execute("INSERT INTO item_banks (survey_id, bank_name, bank_order, config_json) VALUES (?,?,?,?)",
               (sid, "b", 0, json.dumps({"name": "b", "show_count": 1, "items": [{"item": "p", "versions": {"F1": "p"}}],
                                         "arms": [], "order": "random"})))
    db.commit(); db.close()
    url = "/admin/surveys/rw/randomization/rotation"

    print("\n--- route ---")
    r = client.post(url, json=base)
    ok(r.status_code == 401 and r.json()["error"] == "not_signed_in", "refused without a session, as a code")
    main.auth.current_user = lambda request, db: db.execute("SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()
    r = client.post(url, json=dict(base, question="nope"))
    ok(r.status_code == 400 and r.json()["error"] == "unknown_question", "a bad request is a coded 400")
    r = client.post(url, content=b"[1]", headers={"Content-Type": "application/json"})
    ok(r.status_code == 400 and r.json()["error"] == "unreadable_request", "a body that is not an object is refused")
    r = client.post("/admin/surveys/nope/randomization/rotation", json=base)
    ok(r.status_code == 404, "another survey, or none, is not found")
    r = client.post(url, json=base)
    body = r.json()
    db = main.get_db()
    stored = json.loads(db.execute("SELECT schema_json FROM surveys WHERE id=?", (sid,)).fetchone()[0])
    db.close()
    ok(r.status_code == 200 and body["expression"] == rotation.build_expression("day", 1, ARMS), "the server builds the expression")
    ok(stored["calculatedValues"][0]["name"] == "form" and stored["calculatedValues"][0]["includeIntoResult"] is True,
       "the questionnaire now holds the saved calculated value")
    ok(body["shares"] == {"F1": 32.6, "F2": 32.6, "F3": 34.8} and body["calculated"][0]["rotation"]["wave"] == 1,
       "the answer carries the shares and the recognised rotation")
    page = client.get("/admin/surveys/rw/randomization").text
    model = json.loads(page.split("var model = ")[1].split(";\n")[0])
    ok(model["calculated"][0]["rotation"] == {"question": "day", "wave": 1, "arms": ARMS}, "the page model recognises it too")
    ok({"name": "day", "min": 1, "max": 31, "inputType": "number"}.items() <= next(q for q in model["questions"] if q["name"] == "day").items(),
       "questions carry their range for the estimate")

    print("\n--- over collected answers ---")
    db = main.get_db()
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, ?)", (sid, json.dumps({"day": 3})))
    db.commit(); db.close()
    r = client.post(url, json=dict(base, wave=2))
    db = main.get_db()
    unchanged = json.loads(db.execute("SELECT schema_json FROM surveys WHERE id=?", (sid,)).fetchone()[0])
    db.close()
    ok(r.status_code == 409 and r.json() == {"error": "responses_held", "held": 1}, "refused over a response, with the count")
    ok(unchanged == stored, "and nothing changed")
    records = []
    handler = logging.Handler(); handler.emit = records.append
    main.log.addHandler(handler)
    r = client.post(url, json=dict(base, wave=2, confirm=True))
    main.log.removeHandler(handler)
    ok(r.status_code == 200 and r.json()["replaced"] is True, "with confirm it goes through and replaces")
    ok(any("over 1 response" in rec.getMessage() for rec in records), "and the change is written to the admin log")

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED'}")
sys.exit(1 if FAILED else 0)
