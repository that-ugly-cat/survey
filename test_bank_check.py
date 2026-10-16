"""Checks for the server side of the visual bank editor (docs/item-banks-ui-spec.md,
phase 1): the check endpoint gives the save's findings and an example draw and stores
nothing; it is owner-scoped; the randomization page carries the editor's model and
keeps the JSON fields the editor writes into."""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

SCHEMA = {
    "calculatedValues": [{"name": "form", "includeIntoResult": True, "expression": "'F1'"},
                         {"name": "tmp", "expression": "1"}],
    "pages": [{"name": "code", "title": "Your code", "elements": [{"type": "text", "name": "day"}]}]
             + [{"name": f"v{i}_{lb}", "elements": [{"type": "rating", "name": f"v{i}_{lb}_score"}]}
                for i in range(1, 5) for lb in ("A", "B")]
             + [{"name": "end", "elements": [{"type": "comment", "name": "notes"}]}],
}
CFG = {"name": "vign", "show_count": 2, "arm_var": "grp", "order": "random",
       "items": [{"item": f"v{i}", "versions": {"A": f"v{i}_A", "B": f"v{i}_B"}} for i in range(1, 5)],
       "arms": [{"name": "AA", "quota": {"A": 2}}, {"name": "AB", "quota": {"A": 1, "B": 1}}]}

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("vb", "Vignettes", json.dumps(SCHEMA), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='vb'").fetchone()["id"]
    db.execute("INSERT INTO item_banks (survey_id, bank_name, bank_order, config_json) VALUES (?,?,?,?)",
               (sid, "vign", 0, json.dumps(CFG)))
    bid = db.execute("SELECT id FROM item_banks WHERE survey_id=?", (sid,)).fetchone()["id"]
    db.commit(); db.close()

    print("\n--- check without a session ---")
    r = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/check", json=CFG)
    ok(r.status_code == 401 and r.json().get("error") == "not_signed_in", "refused without a session, as a code")

    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    print("\n--- check ---")
    r = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/check", json=CFG)
    body = r.json()
    ok(r.status_code == 200 and body["errors"] == [], f"a valid config has no errors ({body.get('errors')})")
    ok(len(body["example"]) == 2 and all(len(e["draw"]) == 2 for e in body["example"]),
       "an example draw per arm, of show_count items")
    bad = dict(CFG, arms=[{"name": "AA", "quota": {"A": 3}}])
    body = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/check", json=bad).json()
    ok(any("sums to 3" in e for e in body["errors"]) and body["example"] == [],
       "a wrong quota gives the save's error, and no example")
    src = dict(CFG, arm_from="tmp")
    body = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/check", json=src).json()
    ok(any("not saved with the answers" in e for e in body["errors"]), "arm_from rules are the save's rules")
    db = main.get_db()
    stored = json.loads(db.execute("SELECT config_json FROM item_banks WHERE id=?", (bid,)).fetchone()[0])
    draws = db.execute("SELECT COUNT(*) FROM bank_draws WHERE bank_id=?", (bid,)).fetchone()[0]
    db.close()
    ok(stored == CFG and draws == 0, "checking stores nothing: config unchanged, no draws")
    r = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/check", content=b"not json",
                    headers={"Content-Type": "application/json"})
    ok(r.status_code == 400 and r.json().get("error") == "unreadable_config", "an unreadable body is a coded 400")
    r = client.post(f"/admin/surveys/vb/randomization/bank/{bid + 99}/check", json=CFG)
    ok(r.status_code == 404, "a bank of another survey, or none, is not found")

    print("\n--- the page ---")
    page = client.get("/admin/surveys/vb/randomization").text
    ok('src="/static/bank-editor.js"' in page and 'class="bank-json"' in page,
       "the page loads the editor and keeps the JSON fields it writes into")
    ok(f'data-bank-id="{bid}"' in page and 'data-draws="0"' in page, "each bank form carries its id and draw count")
    model = json.loads(page.split("var model = ")[1].split(";\n")[0])
    owners = {p["name"]: p["owner"] for p in model["pages"]}
    ok(owners["v1_A"] == {"kind": "bank", "name": "vign", "id": bid} and owners["code"] is None,
       "the model says which bank holds a page, and that a common page is free")
    ok({"name": "day", "title": "", "page": 0, "type": "text"} in model["questions"], "questions carry the page that asks them")
    ok({"name": "form", "saved": True} in model["calculated"] and {"name": "tmp", "saved": False} in model["calculated"],
       "calculated values carry whether they are saved")
    ok(client.get("/static/bank-editor.js").status_code == 200, "the editor script is served")

    print("\n--- saving through the form still works ---")
    form = {"bank_name": "vign", "show_count": "2", "arm_var": "grp", "arm_from": "", "order": "fixed",
            "items_json": json.dumps(CFG["items"]), "arms_json": json.dumps(CFG["arms"])}
    r = client.post(f"/admin/surveys/vb/randomization/bank/{bid}/save", data=form, follow_redirects=False)
    db = main.get_db()
    stored = json.loads(db.execute("SELECT config_json FROM item_banks WHERE id=?", (bid,)).fetchone()[0])
    db.close()
    ok(r.status_code == 302 and stored["order"] == "fixed", "the fields the editor fills save as before")

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED'}")
sys.exit(1 if FAILED else 0)
