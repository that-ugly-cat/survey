"""Checks for the participant-facing notice and the chrome that carries it.

The notice makes factual claims about this application — no cookies, no browser
storage, nothing loaded from a third party. Those claims are only worth writing
if something fails when they stop being true, so the assertions here read the
rendered questionnaire and the response headers rather than the source of the
text.
"""
import json, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["AUTH_MODE"] = "local"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main, notice

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def show(label, value):
    print(f"    [{label}] {value}")
    return value


SCHEMA = {
    "title": {"default": "Course", "it": "Corso"},
    "pages": [{"name": "p1", "elements": [
        {"type": "radiogroup", "name": "ruolo",
         "title": {"default": "Your role", "it": "Il tuo ruolo"},
         "choices": ["a", "b"]},
    ]}],
}

ONE_LANG = {"title": "Course", "pages": [{"name": "p1", "elements": [
    {"type": "text", "name": "q1", "title": "Name a colour"}]}]}


def setup():
    db = main.get_db()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("o@x.ch", "Owner", "x"))
    uid = db.execute("SELECT id FROM users WHERE email = 'o@x.ch'").fetchone()["id"]
    for slug, schema in (("multi", SCHEMA), ("solo", ONE_LANG)):
        db.execute("INSERT INTO surveys (slug, title, schema_json, active, owner_id) "
                   "VALUES (?, ?, ?, 1, ?)", (slug, "Course", json.dumps(schema), uid))
    db.commit()
    db.close()


# --- 1. the text itself ---------------------------------------------------
print("\n=== the text ===")
ok(set(notice.NOTICE) == set(notice.LANGS),
   f"the notice exists in all four languages: {sorted(notice.NOTICE)}")
shapes = {l: (len(notice.NOTICE[l]["sections"]),
              sum(len(s["p"]) for s in notice.NOTICE[l]["sections"]))
          for l in notice.LANGS}
show("sections, paragraphs", shapes)
ok(len(set(shapes.values())) == 1,
   "every language has the same sections and paragraphs (nothing dropped in translation)")
missing = [(l, k) for l in notice.LANGS
           for k in ("link", "close", "title", "sections", "contact")
           if not notice.NOTICE[l].get(k)]
ok(not missing, f"no empty field in any language: {missing or 'none'}")
ok(notice.notice_for("xx") is notice.NOTICE["en"],
   "an unknown locale falls back to English rather than raising")
contacts = {notice.NOTICE[l]["contact"] for l in notice.LANGS}
ok(all("giovanni.spitale@ibme.uzh.ch" in c for c in contacts),
   "the technical contact survives in every language")

# --- 2. the rendered questionnaire ---------------------------------------
client = TestClient(main.app)
client.__enter__()          # runs the lifespan, which is what creates the schema
setup()
r = client.get("/s/multi")
html = r.text
print("\n=== the rendered questionnaire ===")
ok(r.status_code == 200, f"the page renders: {r.status_code}")

# The claim: nothing is fetched from a third party.
remote = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
show("remote src/href", remote or "none")
ok(not remote, "no script or stylesheet is loaded from another host")
for asset in ("survey-core.min.css", "survey.core.min.js",
              "survey.i18n.min.js", "survey-js-ui.min.js"):
    ok(f"/static/{asset}" in html and os.path.exists(os.path.join("static", asset)),
       f"{asset} is served from this application's own /static, and the file is here")

# The claim: no cookies, nothing in browser storage.
show("Set-Cookie", r.headers.get("set-cookie") or "none")
ok("set-cookie" not in r.headers, "the questionnaire sets no cookie")
storage = [w for w in ("document.cookie", "localStorage", "sessionStorage") if w in html]
show("storage APIs in the page", storage or "none")
ok(not storage, "the page touches no browser storage API")

# The notice travels with the page, in all four languages. Read it back out of
# the rendered page rather than grepping for substrings: `tojson` escapes
# apostrophes, and half this text is French and Italian.
def notice_in(page):
    m = re.search(r"var NOTICE\s*=\s*(\{.*?\});\n", page, re.S)
    return json.loads(m.group(1)) if m else None

served = notice_in(html)
ok(served is not None, "the notice is inlined in the page as JSON")
show("languages served", sorted(served or {}))
ok(served == notice.NOTICE,
   "what the browser receives is the notice, unaltered and complete")
ok(served["it"]["sections"][0]["p"][0] == notice.NOTICE["it"]["sections"][0]["p"][0],
   "body paragraphs reach the browser, not just the headings")
ok(html.count("data-notice-label onclick") == 1,
   f"one opener, in the header: {html.count('data-notice-label onclick')}")
ok("<footer" not in html, "no footer bar: the header alone carries the link")
ok('id="notice"' in html and "function openNotice()" in html,
   "the modal and its opener are both present")

# --- 3. the one-language case --------------------------------------------
print("\n=== a one-language questionnaire ===")
solo = client.get("/s/solo").text
ok(sorted(notice_in(solo) or {}) == sorted(notice.LANGS),
   "the notice still ships in four languages when the form has one")
ok("if (available.length <= 1 && NOTICE[browserLang()]) renderNotice(browserLang())" in solo,
   "and it follows the browser, because there is no switch to press")

# --- 4. nothing regressed in the questionnaire machinery -----------------
print("\n=== the questionnaire still works ===")
for needle in ("survey.onComplete.add", "_timing", "getAvailableLocales",
               "RESULTS_LINK_TEXT", "page_orders", "panel_token"):
    ok(needle in html, f"{needle} survived the rewrite")
ok('"schema"' not in html and '"pages"' in html,
   "the schema is still inlined as JSON")

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}):")
    for m in FAILED:
        print("  - " + m)
    sys.exit(1)
print("all notice checks passed")
