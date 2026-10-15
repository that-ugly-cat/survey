"""Checks for banks whose arm comes from the answers (arm_from, section 15 of
docs/item-banks-spec.md): validation, one draw per arm at the page load, the
resolution at submit from the stored answers, loads and status that ignore what was
never resolved, and the admin form keeping the field."""
import json, os, random, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import banks
import flow
import main

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

# A rotation design: three forms of four posts, two posts common to all forms, and the
# form chosen by a calculated value that reads the day of birth.
COMMON = ["A1", "A2"]
FORMS = {"F1": ["P1", "P2"], "F2": ["P3", "P4"], "F3": ["P5", "P6"]}
CFG = {
    "name": "posts", "show_count": 4, "arm_var": "form_shown", "arm_from": "form", "order": "random",
    "items": [{"item": f"slot{k + 1}", "versions": {f: f"post_{FORMS[f][k]}" for f in FORMS}} for k in range(2)]
             + [{"item": f"common_{a}", "versions": {"A": f"post_{a}"}} for a in COMMON],
    "arms": [{"name": f, "quota": {f: 2, "A": 2}} for f in FORMS],
}
POST_PAGES = [f"post_{p}" for f in FORMS for p in FORMS[f]] + [f"post_{a}" for a in COMMON]
SCHEMA = {
    "calculatedValues": [{"name": "form", "includeIntoResult": True,
                          "expression": "iif(({day} + 1) % 3 = 0, 'F1', iif(({day} + 1) % 3 = 1, 'F2', 'F3'))"}],
    "pages": [{"name": "code", "elements": [{"type": "text", "name": "day", "inputType": "number"}]}]
             + [{"name": n, "elements": [{"type": "rating", "name": f"{n}_judgement"}]} for n in POST_PAGES]
             + [{"name": "end", "elements": [{"type": "comment", "name": "notes"}]}],
}
pages_by_name = {p["name"]: p for p in SCHEMA["pages"]}
page_names = [p["name"] for p in SCHEMA["pages"]]
questions = {q["name"] for p in SCHEMA["pages"] for q in p["elements"]}

print("\n--- configuration and validation ---")
cfg = banks.normalise(CFG)
ok(cfg["arm_from"] == "form", "arm_from survives normalise")
ok(banks.normalise({k: v for k, v in CFG.items() if k != "arm_from"})["arm_from"] is None,
   "a bank without arm_from normalises to None")
errors, warnings = flow.bank_findings(SCHEMA, [], [cfg], 0)
ok(not errors, f"a calculated value saved with the result is a valid source ({errors})")
ok(any("not balanced" in w for w in warnings), "the warning says arms are no longer balanced")

def errs(**over):
    return flow.bank_findings(SCHEMA, [], [banks.normalise({**CFG, **over})], 0)[0]

ok(any("neither a question nor a calculated value" in e for e in errs(arm_from="nothing")),
   "an unknown source is refused")
ok(any("no arms" in e for e in errs(arms=[])), "arm_from without arms is refused")
ok(any("variable of its own" in e for e in errs(arm_var="form")), "arm_from equal to arm_var is refused")
hidden = json.loads(json.dumps(SCHEMA))
hidden["calculatedValues"][0]["includeIntoResult"] = False
ok(any("not saved with the answers" in e for e in flow.bank_findings(hidden, [], [cfg], 0)[0]),
   "a calculated value not saved with the result is refused: the server could not read it")
late = json.loads(json.dumps(SCHEMA))
late["pages"].append({"name": "late", "elements": [{"type": "text", "name": "late_form"}]})
ok(any("on or after the first page of the bank" in e
       for e in flow.bank_findings(late, [], [banks.normalise({**CFG, "arm_from": "late_form"})], 0)[0]),
   "a question asked after the bank's pages is refused")

print("\n--- allocation ---")
by_arm = banks.allocate_deferred(cfg, {}, {}, {}, random.Random(1))
ok(set(by_arm) == set(FORMS), "one draw per arm")
ok(all(sorted(d["page"] for d in draw) == sorted([f"post_{p}" for p in FORMS[arm]] + [f"post_{a}" for a in COMMON])
       for arm, draw in by_arm.items()), "each arm's draw is that form's posts plus the common ones")

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("rot", "Rotation", json.dumps(SCHEMA), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='rot'").fetchone()["id"]
    db.commit(); db.close()
    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    print("\n--- admin form ---")
    client.post("/admin/surveys/rot/randomization/add-bank", follow_redirects=False)
    db = main.get_db()
    bid = db.execute("SELECT id FROM item_banks WHERE survey_id=?", (sid,)).fetchone()["id"]
    db.close()
    form = {"bank_name": "posts", "show_count": "4", "arm_var": "form_shown", "arm_from": "form",
            "order": "random", "items_json": json.dumps(CFG["items"]), "arms_json": json.dumps(CFG["arms"])}
    r = client.post(f"/admin/surveys/rot/randomization/bank/{bid}/save", data=form, follow_redirects=False)
    ok(r.status_code == 302, "the admin form saves a bank with arm_from")
    db = main.get_db()
    stored_cfg = json.loads(db.execute("SELECT config_json FROM item_banks WHERE id=?", (bid,)).fetchone()[0])
    db.close()
    ok(stored_cfg.get("arm_from") == "form", "the stored config keeps arm_from")
    page = client.get("/admin/surveys/rot/randomization").text
    ok('name="arm_from"' in page and 'value="form"' in page, "the admin page shows the field with its value")
    bad = dict(form, arm_from="nothing")
    r = client.post(f"/admin/surveys/rot/randomization/bank/{bid}/save", data=bad, follow_redirects=False)
    ok(r.status_code == 400 and "neither a question" in r.text, "the admin refuses an unknown source")

    def load():
        body = client.get("/s/rot").text
        grab = lambda var: json.loads(body.split(f"var {var}")[1].split("=", 1)[1].split(";\n")[0])
        return grab("deferred_banks"), grab("assignment_ids"), grab("assigned_pages"), grab("pool_pages")

    print("\n--- page load ---")
    deferred, ids, assigned, pooled = load()
    ok(len(deferred) == 1 and deferred[0]["arm_from"] == "form", "the page carries the deferred bank")
    ok(set(deferred[0]["draws"]) == set(FORMS), "with every arm's sequence")
    ok(sorted(deferred[0]["pages"]) == sorted(POST_PAGES) and set(POST_PAGES) <= set(pooled or []),
       "all the bank's pages are pooled, so none shows before the answer")
    ok(not assigned or not set(assigned) & set(POST_PAGES), "no bank page is assigned in advance")
    draw_id = int([i for i in ids if str(i).startswith("b")][0][1:])
    db = main.get_db()
    row = db.execute("SELECT arm, draw_json, completed FROM bank_draws WHERE id=?", (draw_id,)).fetchone()
    db.close()
    ok(row["arm"] is None and "by_arm" in json.loads(row["draw_json"]), "the ledger holds the draw, arm still open")

    print("\n--- submit ---")
    chosen = "F2"
    shown = deferred[0]["draws"][chosen]
    answers = {"day": 1, "form": chosen, **{f"{p}_judgement": 3 for p in shown}}
    r = client.post("/s/rot/submit", json={**answers, "_assignment_ids": ids,
                                            "_banks": {"posts": {"arm": "F3"}}})
    ok(r.status_code == 200, "submit accepted")
    db = main.get_db()
    row = db.execute("SELECT arm, draw_json, completed FROM bank_draws WHERE id=?", (draw_id,)).fetchone()
    stored = json.loads(db.execute("SELECT response_json FROM responses WHERE survey_id=? "
                                   "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()[0])
    db.close()
    ok(row["arm"] == chosen and row["completed"] == 1, "the draw is resolved to the answered arm and completed")
    ok([d["page"] for d in json.loads(row["draw_json"])] == shown, "the ledger keeps that arm's sequence only")
    rec = stored.get("_banks", {}).get("posts", {})
    ok(rec.get("arm") == chosen and rec.get("pages") == shown, "the response records what that arm showed")
    ok(stored.get("form_shown") == chosen, "the arm variable is stored with the answers")

    print("\n--- an answer naming no arm ---")
    deferred, ids, _, _ = load()
    draw_id = int([i for i in ids if str(i).startswith("b")][0][1:])
    r = client.post("/s/rot/submit", json={"day": 1, "form": "F9", "_assignment_ids": ids})
    db = main.get_db()
    row = db.execute("SELECT arm, draw_json FROM bank_draws WHERE id=?", (draw_id,)).fetchone()
    stored = json.loads(db.execute("SELECT response_json FROM responses WHERE survey_id=? "
                                   "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()[0])
    db.close()
    ok(r.status_code == 200 and row["arm"] is None, "an unknown arm resolves nothing")
    ok("posts" not in (stored.get("_banks") or {}), "and the response records no bank rather than a guess")

    print("\n--- loads and status ---")
    client.get("/s/rot")  # one more pending, unresolved draw
    db = main.get_db()
    arm_load, item_load, _ = banks.loads(db, bid, 60)
    st = banks.status(db, bid, banks.normalise(stored_cfg), 60)
    db.close()
    ok(arm_load == {"F2": 1}, f"loads count resolved draws only ({arm_load})")
    ok(st["completed"] == 1 and st["arms"]["F2"]["completed"] == 1, "status counts the resolved response")

    print("\n--- an ordinary bank is untouched ---")
    plain = banks.normalise({**CFG, "arm_from": None})
    db = main.get_db()
    got = banks.assign(db, bid, plain, 60)
    db.close()
    ok(got["by_arm"] is None and got["arm"] in FORMS and len(got["draw"]) == 4,
       "without arm_from the bank draws its arm as before")

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED'}")
sys.exit(1 if FAILED else 0)
