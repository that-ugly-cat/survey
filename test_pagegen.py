"""Checks for the page generator (docs/item-banks-ui-spec.md, phase 3): reading the
table, filling a template, renaming questions the way the long export expects, every
problem the plan reports, where the pages go, and the admin route with its dry run and
its guard over collected answers."""
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
import pagegen

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))
codes = lambda plan: [c for c, _ in plan["errors"]]

TEMPLATE = {"name": "post_template", "title": "Post",
            "elements": [{"type": "html", "name": "post_template_card", "html": "<p>{{text}}</p><small>{{ source }}</small>"},
                         {"type": "rating", "name": "post_template_giudizio", "title": "True or false?",
                          "visibleIf": "{post_template_card} notempty"},
                         {"type": "panel", "name": "extra", "elements": [{"type": "comment", "name": "why", "title": "Why, about {{text}}?"}]}]}
SCHEMA = {"pages": [{"name": "code", "elements": [{"type": "text", "name": "day"}]}, TEMPLATE,
                    {"name": "end", "elements": [{"type": "comment", "name": "notes"}]}]}
TABLE = "item\tversion\ttext\tsource\nP01\tA\tFirst text\tWHO\nP01\tB\tFirst, other\tCDC\nP02\tA\tSecond\tNASA\n"

print("\n--- reading the table ---")
cols, rows, errs = pagegen.parse_table(TABLE)
ok(cols == ["item", "version", "text", "source"] and len(rows) == 3 and not errs, "pasted spreadsheet cells (tabs)")
cols, rows, errs = pagegen.parse_table("﻿Item,Version,text\nP01,A,\"a, quoted\"\n")
ok(cols == ["item", "version", "text"] and rows[0]["text"] == "a, quoted", "CSV with a BOM, capitalised headers, quoted commas")
cols, rows, errs = pagegen.parse_table("item;version;text\nP01;A;x\n")
ok(rows[0]["text"] == "x", "semicolons, as European spreadsheets export")
ok([c for c, _ in pagegen.parse_table("")[2]] == ["empty_table"], "an empty table")
ok("missing_column" in [c for c, _ in pagegen.parse_table("item\ttext\nP01\tx")[2]], "a missing version column")
ok("duplicate_column" in [c for c, _ in pagegen.parse_table("item\tversion\ttext\ttext\nP\tA\tx\ty")[2]], "a repeated column")
ok("row_too_long" in [c for c, _ in pagegen.parse_table("item\tversion\nP\tA\textra")[2]], "a row with more cells than the header")

print("\n--- a page from the template ---")
ok(pagegen.placeholders(TEMPLATE) == ["text", "source"], "placeholders found, in order, with or without spaces inside the braces")
page = pagegen.make_page(TEMPLATE, {"item": "P01", "version": "A", "text": "Hello", "source": "WHO"})
names = [e["name"] for e in page["elements"]]
ok(page["name"] == "P01_A" and names == ["P01_A_card", "P01_A_giudizio", "P01_A_extra"], "page and questions renamed page_role")
ok(page["elements"][2]["elements"][0]["name"] == "P01_A_why", "questions inside a panel too")
ok(page["elements"][0]["html"] == "<p>Hello</p><small>WHO</small>" and page["elements"][2]["elements"][0]["title"] == "Why, about Hello?",
   "placeholders filled everywhere, html and titles")
ok(page["elements"][1]["visibleIf"] == "{P01_A_card} notempty", "a condition on a template question follows the rename")
ok(TEMPLATE["elements"][0]["name"] == "post_template_card", "the template is not changed")

print("\n--- the plan ---")
plan = pagegen.plan(SCHEMA, "post_template", TABLE)
ok(not plan["errors"] and [p["name"] for p in plan["pages"]] == ["P01_A", "P01_B", "P02_A"], "three pages from three rows")
ok(plan["items"] == [{"item": "P01", "versions": {"A": "P01_A", "B": "P01_B"}}, {"item": "P02", "versions": {"A": "P02_A"}}],
   "grouped into items for the bank")
ok("unknown_template" in codes(pagegen.plan(SCHEMA, "nope", TABLE)), "an unknown template")
ok("template_in_use" in codes(pagegen.plan(SCHEMA, "post_template", TABLE, {"post_template": "bank b"})), "a template held by a bank")
ok("missing_placeholder" in codes(pagegen.plan(SCHEMA, "post_template", "item\tversion\ttext\nP01\tA\tx")), "a placeholder without a column")
notes = pagegen.plan(SCHEMA, "post_template", TABLE.replace("source\n", "source\textra\n").replace("WHO\n", "WHO\tz\n"))["notes"]
ok(any("not used" in n for n in notes), "a column the template does not use is noted, not refused")
ok("bad_item" in codes(pagegen.plan(SCHEMA, "post_template", "item\tversion\ttext\tsource\nP 1\tA\tx\ty")), "an item with a space")
ok("bad_version" in codes(pagegen.plan(SCHEMA, "post_template", "item\tversion\ttext\tsource\nP1\tA_B\tx\ty")), "a version with an underscore")
ok("duplicate_row" in codes(pagegen.plan(SCHEMA, "post_template", TABLE + "P02\tA\tagain\tx\n")), "the same item and version twice")
clash = {"pages": SCHEMA["pages"] + [{"name": "P01_A", "elements": []}]}
ok("page_exists" in codes(pagegen.plan(clash, "post_template", TABLE)), "a page that already exists")
qclash = {"pages": SCHEMA["pages"] + [{"name": "x", "elements": [{"type": "text", "name": "P02_A_giudizio"}]}]}
ok("question_exists" in codes(pagegen.plan(qclash, "post_template", TABLE)), "a question name that already exists")

print("\n--- where the pages go ---")
kept = pagegen.apply(SCHEMA, "post_template", plan["pages"], remove_template=False)
gone = pagegen.apply(SCHEMA, "post_template", plan["pages"], remove_template=True)
ok([p["name"] for p in kept["pages"]] == ["code", "post_template", "P01_A", "P01_B", "P02_A", "end"], "after the template, which stays")
ok([p["name"] for p in gone["pages"]] == ["code", "P01_A", "P01_B", "P02_A", "end"], "in its place, when it is removed")
ok([p["name"] for p in SCHEMA["pages"]] == ["code", "post_template", "end"], "the schema passed in is not changed")

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("pg", "Generator", json.dumps(SCHEMA), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='pg'").fetchone()["id"]
    db.commit(); db.close()
    url = "/admin/surveys/pg/generate-pages"
    stored = lambda: json.loads(main.get_db().execute("SELECT schema_json FROM surveys WHERE id=?", (sid,)).fetchone()[0])

    print("\n--- route ---")
    r = client.post(url, json={"template": "post_template", "table": TABLE, "dry_run": True})
    ok(r.status_code == 401 and r.json()["error"] == "not_signed_in", "refused without a session, as a code")
    main.auth.current_user = lambda request, db: db.execute("SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()
    r = client.post(url, json={"template": "post_template", "table": TABLE, "dry_run": True})
    d = r.json()
    ok(r.status_code == 200 and d["count"] == 3 and d["names"] == ["P01_A", "P01_B", "P02_A"] and len(d["sample"]) == 3,
       "a dry run says what it would do")
    ok(stored() == SCHEMA, "and writes nothing")
    r = client.post(url, json={"template": "post_template", "table": "item\tversion\nP 1\tA", "dry_run": True})
    ok(r.status_code == 200 and "bad_item" in r.json()["codes"], "a dry run with problems lists them")
    r = client.post(url, json={"template": "post_template", "table": "item\tversion\nP 1\tA"})
    ok(r.status_code == 400 and stored() == SCHEMA, "a real run with problems is refused and writes nothing")
    r = client.post(url, content=b"[]", headers={"Content-Type": "application/json"})
    ok(r.status_code == 400 and r.json()["error"] == "unreadable_request", "a body that is not an object")
    r = client.post("/admin/surveys/nope/generate-pages", json={"template": "x", "table": "", "dry_run": True})
    ok(r.status_code == 404, "another survey, or none")
    r = client.post(url, json={"template": "post_template", "table": TABLE, "remove_template": True})
    d = r.json()
    names_now = [p["name"] for p in stored()["pages"]]
    ok(r.status_code == 200 and d["count"] == 3 and names_now == ["code", "P01_A", "P01_B", "P02_A", "end"],
       "a real run writes the pages and removes the template")
    ok({p["name"] for p in d["model"]["pages"]} == set(names_now) and len(d["items"]) == 2,
       "and returns the editor's new model and the items")

    print("\n--- over collected answers ---")
    db = main.get_db()
    db.execute("UPDATE surveys SET schema_json=? WHERE id=?", (json.dumps(SCHEMA), sid))
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, ?)", (sid, json.dumps({"day": 1})))
    db.commit(); db.close()
    r = client.post(url, json={"template": "post_template", "table": TABLE})
    ok(r.status_code == 409 and r.json() == {"error": "responses_held", "held": 1} and stored() == SCHEMA,
       "refused over a response, nothing written")
    records = []
    handler = logging.Handler(); handler.emit = records.append
    main.log.addHandler(handler)
    r = client.post(url, json={"template": "post_template", "table": TABLE, "confirm": True})
    main.log.removeHandler(handler)
    ok(r.status_code == 200 and "post_template" in [p["name"] for p in stored()["pages"]],
       "with confirm it goes through, and the template stays when not asked to remove it")
    ok(any("over 1 response" in rec.getMessage() for rec in records), "the change is written to the admin log")

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED'}")
sys.exit(1 if FAILED else 0)
