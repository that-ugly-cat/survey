"""End-to-end check of panel support and the assignment ledger."""
import json, os, sqlite3, sys, tempfile

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
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))
FAILED = []

with TestClient(main.app):
    db = main.get_db()
    schema = {"pages": [
        {"name": "info_a", "elements": [{"type": "text", "name": "q1"}]},
        {"name": "info_b", "elements": [{"type": "text", "name": "q1"}]},
        {"name": "common", "elements": [{"type": "text", "name": "q2"}]},
    ]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?,?,?,1)",
               ("t", "T", json.dumps(schema)))
    sid = db.execute("SELECT id FROM surveys WHERE slug='t'").fetchone()["id"]
    db.execute("""INSERT INTO rand_pools (survey_id, pool_pages, show_count, condition_var, condition_map)
                  VALUES (?,?,1,'condition',?)""",
               (sid, json.dumps(["info_a", "info_b"]), json.dumps({"info_a": "A", "info_b": "B"})))
    db.commit(); db.close()

    print("\n--- no panel configured: unchanged behaviour ---")
    r = client.get("/s/t")
    ok(r.status_code == 200, "plain entry works")
    r = client.post("/s/t/submit", json={"q1": "x"})
    ok(r.status_code == 200 and r.json()["redirect"] is None, "submit returns no redirect")

    print("\n--- assignment ledger: page loads must not consume an arm ---")
    db = main.get_db()
    db.execute("DELETE FROM assignments"); db.execute("DELETE FROM responses"); db.commit(); db.close()
    for _ in range(6):
        client.get("/s/t")                      # six abandoned loads
    db = main.get_db()
    done = db.execute("SELECT COUNT(*) c FROM assignments WHERE completed=1").fetchone()["c"]
    pend = db.execute("SELECT COUNT(*) c FROM assignments WHERE completed=0").fetchone()["c"]
    db.close()
    ok(done == 0 and pend == 6, f"6 abandoned loads -> 0 completed, {pend} pending")

    body = client.get("/s/t").text
    aid = json.loads(body.split("var assignment_ids  = ")[1].split(";")[0])
    client.post("/s/t/submit", json={"q1": "x", "_assignment_ids": aid})
    db = main.get_db()
    done = db.execute("SELECT COUNT(*) c FROM assignments WHERE completed=1").fetchone()["c"]
    db.close()
    ok(done == 1, "submitting marks exactly its own assignment completed")

    db = main.get_db()
    ok(main._complete_assignments(db, 999, aid) is None, "cross-survey id update is a no-op call")
    db.commit()
    n = db.execute("SELECT COUNT(*) c FROM assignments WHERE completed=1").fetchone()["c"]
    db.close()
    ok(n == 1, "ids from another survey cannot be flipped")

    print("\n--- panel mode ---")
    db = main.get_db()
    db.execute("""UPDATE surveys SET panel_token_param='RID',
                  panel_complete_url='https://p.example/done',
                  panel_screenout_url='https://p.example/so?x=1',
                  panel_quotafull_url='https://p.example/qf/{token}/end' WHERE id=?""", (sid,))
    db.execute("DELETE FROM responses"); db.commit(); db.close()

    r = client.get("/s/t", follow_redirects=False)
    ok(r.status_code == 302 and "p.example/so" in r.headers["location"],
       "missing token -> redirect to screenout")

    r = client.get("/s/t?RID=../etc/passwd", follow_redirects=False)
    ok(r.status_code == 302, "malformed token treated as missing")

    r = client.get("/s/t?rid=ABC123")          # lower-case param name
    ok(r.status_code == 200 and "ABC123" in r.text, "token matched case-insensitively")

    aid = json.loads(r.text.split("var assignment_ids  = ")[1].split(";")[0])
    r = client.post("/s/t/submit", json={"q1": "x", "_panel_token": "ABC123", "_assignment_ids": aid})
    ok(r.json()["redirect"] == "https://p.example/done?RID=ABC123", "complete URL gets ?RID=token")

    r = client.post("/s/t/submit", json={"q1": "y", "_panel_token": "ABC123"})
    ok(r.status_code == 409, "same token cannot submit twice")

    r = client.get("/s/t?RID=ABC123", follow_redirects=False)
    ok(r.status_code == 302 and "p.example/so" in r.headers["location"],
       "spent token cannot re-enter")

    client.get("/s/t?RID=TOK2")
    r = client.post("/s/t/submit", json={"q1": "z", "_panel_token": "TOK2", "_outcome": "screenout"})
    ok(r.json()["redirect"] == "https://p.example/so?x=1&RID=TOK2",
       "screenout outcome appends with & when URL already has a query")

    client.get("/s/t?RID=TOK3")
    r = client.post("/s/t/submit", json={"q1": "z", "_panel_token": "TOK3", "_outcome": "quotafull"})
    ok(r.json()["redirect"] == "https://p.example/qf/TOK3/end", "{token} placeholder substituted")

    client.get("/s/t?RID=TOK4")
    r = client.post("/s/t/submit", json={"q1": "z", "_panel_token": "TOK4", "_outcome": "nonsense"})
    ok(r.json()["redirect"] == "https://p.example/done?RID=TOK4", "unknown outcome falls back to complete")

    r = client.post("/s/t/submit", json={"q1": "preview"})
    ok(r.json()["redirect"] is None, "preview submission (no token) is not bounced to the provider")

    db = main.get_db()
    n = db.execute("SELECT COUNT(*) c FROM responses WHERE panel_token IS NOT NULL").fetchone()["c"]
    stored = db.execute("SELECT response_json FROM responses ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    ok(n == 4, f"4 panel responses stored (screenouts kept), got {n}")
    ok("_panel_token" in json.loads(stored["response_json"]), "token lands in the exported JSON")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}"))
sys.exit(1 if FAILED else 0)
