"""Checks for the defects the documentation review of 16 October 2026 found in the
code, and for the duplicate action it asked for: purge empties the item banks' draws
too; a respondent's results page keeps its token when it polls; the MCP validates a
schema with the survey's banks in view, and no pool takes a bank's page; a bank saved
over responses needs its name typed back, checked by the server; the report editor
offers questions inside panels; a numeric or date block gets the chart it asks for;
and a survey duplicates into a new, closed one with its instrument and nothing of its
fielding."""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["AUTH_MODE"] = "local"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import auth
import main
import mcp_app
import results

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

BANK = {"name": "Vig", "show_count": 1, "arm_var": "grp", "order": "random",
        "items": [{"item": "p1", "versions": {"A": "p1_A", "B": "p1_B"}}],
        "arms": [{"name": "A", "quota": {"A": 1}}, {"name": "B", "quota": {"B": 1}}]}


def schema_with_bank(extra_pages=()):
    return {"pages": [{"name": "intro", "elements": [{"type": "text", "name": "age"}]},
                      {"name": "p1_A", "elements": [{"type": "rating", "name": "p1_A_q"}]},
                      {"name": "p1_B", "elements": [{"type": "rating", "name": "p1_B_q"}]},
                      *extra_pages]}


def seed(db, slug, owner, schema, active=1, report=None, url_tags=None, bank=True):
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active, report_json, url_tags) "
               "VALUES (?,?,?,?,?,?,?)", (slug, slug.upper(), json.dumps(schema), owner["id"], active,
                                         json.dumps(report) if report else None, url_tags))
    sid = db.execute("SELECT id FROM surveys WHERE slug = ?", (slug,)).fetchone()["id"]
    bid = None
    if bank:
        bid = db.execute("INSERT INTO item_banks (survey_id, bank_name, bank_order, config_json) "
                         "VALUES (?,?,0,?)", (sid, BANK["name"], json.dumps(BANK))).lastrowid
    return sid, bid


def count(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()
    auth.set_caller(owner)

    print("\n--- purge empties the item banks' draws ---")
    sid, bid = seed(db, "pg", owner, schema_with_bank())
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, '{}')", (sid,))
    for arm in ("A", "B", "A"):
        db.execute("INSERT INTO bank_draws (bank_id, arm, draw_json, completed) VALUES (?,?,'[]',1)", (bid, arm))
    db.commit()
    client.post("/admin/surveys/pg/purge", follow_redirects=False)
    ok(count(db, "SELECT COUNT(*) FROM bank_draws WHERE bank_id = ?", bid) == 0, "the bank's draws are gone")
    ok(count(db, "SELECT COUNT(*) FROM item_banks WHERE id = ?", bid) == 1, "the bank itself stays")

    print("\n--- a respondent's page keeps its token ---")
    sid, _ = seed(db, "rp", owner, {"pages": [{"name": "p", "elements": [
        {"type": "radiogroup", "name": "colour", "choices": ["red", "blue"]}]}]},
        report={"audience": "respondent", "blocks": [{"kind": "all_questions"}]}, bank=False)
    db.execute("INSERT INTO responses (survey_id, response_json, view_token) VALUES (?,?,?)",
               (sid, json.dumps({"colour": "red"}), "tok123"))
    db.commit()
    page = client.get("/s/rp/results?r=tok123").text
    ok('const MINE     = "tok123"' in page, "the page carries the token for its own requests")
    ok("+ MINE_Q;" in page, "the poll sends it")
    db.execute("UPDATE surveys SET report_json = ? WHERE id = ?", (json.dumps(
        {"audience": "respondent", "explore": True, "blocks": [{"kind": "all_questions"}]}), sid))
    db.commit()
    page = client.get("/s/rp/results?r=tok123").text
    ok('+ "&lang=" + encodeURIComponent(LOCALE) + MINE_Q' in page, "and so does the exploration, when on")
    ok(client.get("/s/rp/results.json?r=tok123").status_code == 200, "the poll with the token answers")
    ok(client.get("/s/rp/results.json").status_code == 404, "without it a respondent-only report does not")
    ok('const MINE     = ""' in client.get("/admin/surveys/rp/results").text, "the owner's page carries none")

    print("\n--- the MCP sees the banks ---")
    seed(db, "mb", owner, schema_with_bank())
    db.commit()
    gated = schema_with_bank([{"name": "follow", "visibleIf": "{grp} = 'A'",
                               "elements": [{"type": "text", "name": "why"}]}])
    res = mcp_app.update_schema("mb", gated)
    ok(not res.get("error"), "a condition on a bank's arm variable is not a dangling reference")
    res = mcp_app.set_pool("mb", ["p1_A", "intro"])
    ok("never both" in (res.get("error") or ""), "set_pool refuses a page a bank holds")
    res = mcp_app.set_pool("mb", ["intro"])
    ok(not res.get("error") and "findings" in res, "a pool on free pages saves, with findings")

    print("\n--- the web pool form refuses a bank's page ---")
    pid = db.execute("SELECT id FROM rand_pools WHERE survey_id = (SELECT id FROM surveys WHERE slug='mb')").fetchone()["id"]
    r = client.post(f"/admin/surveys/mb/randomization/{pid}/save",
                    data={"pool_name": "P", "pool_pages": ["p1_B", "intro"], "show_count": "1"})
    ok(r.status_code == 400 and "p1_B (bank Vig)" in r.text, "refused, naming the page and the bank")

    print("\n--- a bank saved over responses needs its name typed ---")
    sid, bid = seed(db, "bs", owner, schema_with_bank())
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, '{}')", (sid,))
    db.commit()
    form = {"bank_name": "Vig", "show_count": "1", "arm_var": "grp", "arm_from": "", "order": "random",
            "items_json": json.dumps(BANK["items"]), "arms_json": json.dumps(BANK["arms"])}
    r = client.post(f"/admin/surveys/bs/randomization/bank/{bid}/save", data=form, follow_redirects=False)
    ok(r.status_code == 400 and "type the bank name" in r.text, "without the name: refused, nothing saved")
    r = client.post(f"/admin/surveys/bs/randomization/bank/{bid}/save",
                    data={**form, "confirm_name": "vig"}, follow_redirects=False)
    ok(r.status_code == 400, "a name typed differently is not the name")
    r = client.post(f"/admin/surveys/bs/randomization/bank/{bid}/save",
                    data={**form, "confirm_name": " Vig "}, follow_redirects=False)
    ok(r.status_code == 302, "with the name typed back: saved")
    page = client.get("/admin/surveys/bs/randomization").text
    ok('name="confirm_name"' in page and "confirmBankSave(this)" in page, "the JSON form asks for it too")

    print("\n--- questions inside panels reach the report editor ---")
    seed(db, "pn", owner, {"pages": [{"name": "p", "elements": [{"type": "panel", "name": "box", "elements": [
        {"type": "radiogroup", "name": "inside_q", "choices": ["a", "b"]}]}]}]}, bank=False)
    db.commit()
    ok("inside_q" in client.get("/admin/surveys/pn/report").text, "the editor offers the question in the panel")

    print("\n--- numeric and date blocks get their chart ---")
    ok(results._chosen({"chart": "column"}, "temporal") == "column", "a date block asking for columns gets them")
    ok(results._chosen({"chart": "pie"}, "temporal") == "line", "a chart the shape lacks falls back to the default")
    ok(results._chosen({}, "numeric") == "histogram", "no choice: the default")

    print("\n--- duplicate ---")
    up = os.path.join(os.environ["UPLOADS_PATH"], "orig")
    os.makedirs(up, exist_ok=True)
    open(os.path.join(up, "x.png"), "wb").write(b"png")
    orig_schema = schema_with_bank([{"name": "pic", "elements": [
        {"type": "image", "name": "img", "imageLink": "/uploads/orig/x.png"}]}])
    sid, bid = seed(db, "orig", owner, orig_schema, active=1, url_tags="channel",
                    report={"audience": "public", "explore": True, "blocks": [{"kind": "all_questions"}]})
    db.execute("INSERT INTO rand_pools (survey_id, pool_pages, show_count) VALUES (?,?,1)",
               (sid, json.dumps(["intro", "pic"])))
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, '{}')", (sid,))
    db.execute("INSERT INTO bank_draws (bank_id, arm, draw_json, completed) VALUES (?,?,'[]',1)", (bid, "A"))
    db.execute("UPDATE surveys SET panel_token_param = 'RID' WHERE id = ?", (sid,))
    db.commit()
    r = client.post("/admin/surveys/orig/duplicate", data={"new_slug": "orig-w2", "new_title": "Wave 2"},
                    follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/admin/surveys/orig-w2", "lands on the copy's page")
    new = db.execute("SELECT * FROM surveys WHERE slug = 'orig-w2'").fetchone()
    ok(new is not None and new["title"] == "Wave 2" and new["active"] == 0, "a new survey, closed")
    ok("/uploads/orig-w2/x.png" in new["schema_json"] and "/uploads/orig/" not in new["schema_json"],
       "the schema points at the copy's files")
    ok(os.path.exists(os.path.join(os.environ["UPLOADS_PATH"], "orig-w2", "x.png")), "the files are copied")
    ok(count(db, "SELECT COUNT(*) FROM rand_pools WHERE survey_id = ?", new["id"]) == 1, "the pool travels")
    nb = db.execute("SELECT * FROM item_banks WHERE survey_id = ?", (new["id"],)).fetchall()
    ok(len(nb) == 1 and json.loads(nb[0]["config_json"])["arm_var"] == "grp", "the bank travels")
    ok(count(db, "SELECT COUNT(*) FROM bank_draws WHERE bank_id = ?", nb[0]["id"]) == 0, "without its draws")
    ok(count(db, "SELECT COUNT(*) FROM responses WHERE survey_id = ?", new["id"]) == 0, "without responses")
    rep = json.loads(new["report_json"])
    ok(rep["audience"] == "owner" and rep["explore"] is False and len(rep["blocks"]) == 1,
       "the report's blocks, private to the owner")
    ok(new["url_tags"] == "channel" and not new["panel_token_param"], "link tags travel, panel recruitment stays")
    ok(new["owner_id"] == owner["id"], "owned by whoever duplicated it")
    old = db.execute("SELECT * FROM surveys WHERE slug = 'orig'").fetchone()
    ok(old["active"] == 1 and "/uploads/orig/x.png" in old["schema_json"], "the original is untouched")

    for data, code, what in (({"new_slug": "Bad Slug!", "new_title": "T"}, "bad_slug", "a bad address"),
                             ({"new_slug": "orig-w2", "new_title": "T"}, "slug_taken", "a taken address"),
                             ({"new_slug": "orig-w3", "new_title": " "}, "no_title", "an empty title")):
        r = client.post("/admin/surveys/orig/duplicate", data=data, follow_redirects=False)
        ok(f"dup_error={code}" in r.headers.get("location", ""), f"refused: {what}")
    page = client.get("/admin/surveys/orig?dup_error=slug_taken").text
    ok("Duplicate survey" in page and "already used by another survey" in page, "the form, and the reason")
    db.close()

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED: ' + '; '.join(FAILED)}")
sys.exit(1 if FAILED else 0)
