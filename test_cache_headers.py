"""Check that nothing but the public assets can be kept by a cache.

On 16 Oct 2026 an owner's export at /admin/surveys/{slug}/export.xlsx was cached
by Cloudflare, which caches by file extension, and served from the edge to anyone
for four hours. The fix is a header on every response outside /static and
/uploads; these checks read the headers the app actually sends.
"""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
# The local cookie login, as in test_purge: this test signs its own session.
os.environ["AUTH_MODE"] = "local"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main, auth

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))
PRIVATE = "private, no-store"


def cc(r):
    return r.headers.get("cache-control", "")


with TestClient(main.app) as client:
    db = main.get_db()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)", ("o@x.test", "Owner", "x"))
    uid = db.execute("SELECT id FROM users WHERE email='o@x.test'").fetchone()["id"]
    schema = {"pages": [{"name": "p1", "elements": [{"type": "text", "name": "q1"}]}]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("mine", "Mine", json.dumps(schema), uid))
    sid = db.execute("SELECT id FROM surveys WHERE slug='mine'").fetchone()["id"]
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)", (sid, json.dumps({"q1": "a"})))
    db.commit(); db.close()
    owner = {"session": auth.make_token(uid, "full")}
    os.makedirs(os.path.join(tmp, "uploads", "mine"), exist_ok=True)
    with open(os.path.join(tmp, "uploads", "mine", "pic.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")

    print("\n--- what an owner downloads ---")
    for path in ("/admin/surveys/mine/export.xlsx", "/admin/surveys/mine/export.csv", "/admin/surveys/mine"):
        r = client.get(path, cookies=owner, follow_redirects=False)
        ok(r.status_code == 200 and cc(r) == PRIVATE, f"{path}: {r.status_code}, Cache-Control '{cc(r)}'")

    print("\n--- what anyone else gets ---")
    r = client.get("/admin/surveys/mine/export.xlsx", follow_redirects=False)
    ok(r.status_code in (302, 303) and cc(r) == PRIVATE,
       f"signed out, the export redirects and is not cacheable either: {r.status_code}, '{cc(r)}'")
    r = client.get("/s/mine")
    ok(r.status_code == 200 and cc(r) == PRIVATE, f"the questionnaire page: '{cc(r)}'")

    print("\n--- public assets keep their caching ---")
    r = client.get("/static/survey-core.min.css")
    ok(r.status_code == 200 and "no-store" not in cc(r), f"/static: {r.status_code}, '{cc(r) or 'none'}'")
    r = client.get("/uploads/mine/pic.png")
    ok(r.status_code == 200 and "no-store" not in cc(r), f"/uploads: {r.status_code}, '{cc(r) or 'none'}'")

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}):")
    for m in FAILED:
        print("  - " + m)
    sys.exit(1)
print("all cache-header checks passed")
