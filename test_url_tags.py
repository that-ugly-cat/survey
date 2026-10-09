"""Link tags (?channel=… recorded with the response) and the response id in the exports."""
import csv, io, json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)
FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def page_tags(body):
    return json.loads(body.split("var url_tags        = ")[1].split(";")[0])


def last_response():
    db = main.get_db()
    r = db.execute("SELECT response_json FROM responses ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    return json.loads(r["response_json"])


with TestClient(main.app):
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    schema = {"pages": [{"name": "p1", "elements": [{"type": "text", "name": "q1"}]}]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?,?,?,?)",
               ("t", "T", json.dumps(schema), owner["id"]))
    db.commit(); db.close()
    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    print("\n--- no tags configured: nothing changes ---")
    r = client.get("/s/t?channel=signatories")
    ok(r.status_code == 200 and page_tags(r.text) == {}, "an unconfigured parameter is not passed to the page")
    client.post("/s/t/submit", json={"q1": "a", "_url": {"channel": "signatories"}})
    ok("_url" not in last_response(), "an unconfigured tag sent by the browser is not stored")

    print("\n--- configuring tags ---")
    r = client.post("/admin/surveys/t/url-tags", data={"names": "channel, wave"}, follow_redirects=False)
    ok(r.status_code == 302 and "tags_saved=1" in r.headers["location"], "two names are saved")
    db = main.get_db()
    ok(main._url_tag_names(db.execute("SELECT * FROM surveys WHERE slug='t'").fetchone()) == ["channel", "wave"],
       "stored in the order given")
    db.close()
    for bad, why in (("a b c d e f", "more than five"), ("chan nel!", "bad characters"),
                     ("preview", "the reserved preview")):
        r = client.post("/admin/surveys/t/url-tags", data={"names": bad}, follow_redirects=False)
        ok("tags_error=" in r.headers["location"], f"refused: {why}")
    db = main.get_db()
    ok(main._url_tag_names(db.execute("SELECT * FROM surveys WHERE slug='t'").fetchone()) == ["channel", "wave"],
       "a refused save leaves the old names")
    db.close()
    r = client.get("/admin/surveys/t")
    ok(r.status_code == 200 and "Link tags" in r.text and "?channel=" in r.text, "the manage page shows the section and the links")

    print("\n--- tags on the public link ---")
    r = client.get("/s/t?CHANNEL=signatories&wave=2&other=x")
    ok(page_tags(r.text) == {"channel": "signatories", "wave": "2"},
       "configured names matched case-insensitively, keyed by their configured spelling; others ignored")
    r = client.get("/s/t?channel=<script>")
    ok(page_tags(r.text) == {}, "a value outside the charset is dropped, not cleaned")
    client.post("/s/t/submit", json={"q1": "b", "_url": {"channel": "signatories", "other": "x", "wave": "a b"}})
    got = last_response()
    ok(got.get("_url") == {"channel": "signatories"}, "submit keeps configured, valid tags only")
    client.post("/s/t/submit", json={"q1": "c", "_url": "channel=signatories"})
    ok("_url" not in last_response(), "a malformed _url is discarded")

    print("\n--- response id in the exports ---")
    db = main.get_db()
    cols, rows = main._flat_table(main._get_responses(db, "t"))
    ids = [r["id"] for r in db.execute("SELECT id FROM responses ORDER BY submitted_at, id")]
    db.close()
    ok(cols[0] == "_response" and cols[-1] == "_submitted_at", "_response is the first column, _submitted_at the last")
    ok(sorted(r["_response"] for r in rows) == sorted(ids), "every row carries its own response id")
    ok("_url_channel" in cols, "a tag flattens to _url_<name>")
    r = client.get("/admin/surveys/t/export.csv")
    head = next(csv.reader(io.StringIO(r.text)))
    ok(r.status_code == 200 and head[0] == "_response", "the CSV export starts with _response")
    r = client.get("/admin/surveys/t/export.json")
    ok(all("response" in x for x in r.json()), "the JSON export carries the response id")

print(f"\n{'ALL PASSED' if not FAILED else str(len(FAILED)) + ' FAILED'}")
sys.exit(1 if FAILED else 0)
