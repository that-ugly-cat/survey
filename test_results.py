"""Checks for results.py and the three results views: what each audience gets
served, that the page is readable without JavaScript, and that a report nobody
published cannot be found.
"""
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
import main, auth, results, report as R

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
         "choices": [{"value": "a", "text": {"default": "Researcher", "it": "Ricerca"}},
                     {"value": "b", "text": {"default": "Teaching", "it": "Docenza"}},
                     {"value": "c", "text": {"default": "Student", "it": "Studio"}}]},
        {"type": "rating", "name": "score", "title": {"default": "Score", "it": "Voto"},
         "rateMin": 1, "rateMax": 5},
        {"type": "comment", "name": "note", "title": {"default": "Note", "it": "Nota"}},
    ]}],
}

# Fifteen people: a=7, b=6, c=2. Only 'c' is under the threshold, but masking one
# cell alone would give it away by subtraction, so 'b' goes with it and 'a' stays
# visible — which is the masking rule, seen from the outside.
# The open answers carry a sentinel rather than a plausible word: "good" and
# "mine" both appear in the page's own markup, and a leak check that matches a
# CSS class name fails for a reason that has nothing to do with the data.
ANSWERS = ([{"ruolo": "a", "score": 5, "note": "zzOpenAnswerAlpha"} for _ in range(7)] +
           [{"ruolo": "b", "score": 4, "note": "zzOpenAnswerBeta"} for _ in range(6)] +
           [{"ruolo": "c", "score": 3, "note": "zzOpenAnswerGamma"} for _ in range(2)])

REPORT = {"audience": "public", "blocks": [
    {"kind": "text", "md": {"default": "## What we found", "it": "## Cosa abbiamo trovato"}},
    {"kind": "question", "name": "ruolo", "chart": "bar", "value": "count",
     "sort": "schema", "other": "show"},
    {"kind": "question", "name": "score", "chart": "column", "value": "count",
     "sort": "schema", "other": "show"},
    {"kind": "question", "name": "note", "audience": "owner", "chart": "list",
     "value": "count", "sort": "schema", "other": "show"},
]}

print("\n--- markdown ---")
html = results.render_markdown({"default": "## Hi <script>alert(1)</script>",
                                "it": "## Ciao"}, "it")
show("it", html)
ok("Ciao" in html and "<h2>" in html, "the viewer's language, rendered as markdown")
bad = results.render_markdown({"default": "# T <script>alert(1)</script>"}, "en")
show("escaped", bad)
ok("<script>" not in bad and "&lt;script&gt;" in bad,
   "the block's own HTML is escaped: an owner-written page is still a public page")
ok(results.loc({"en": "typed in the editor"}, "en") == "typed in the editor",
   "a text block stores 'en' where the schema would store 'default', and both resolve")

print("\n--- labels come from the schema, per language ---")
built = results.build(SCHEMA, [], REPORT, ANSWERS, "owner", "it")
by_name = {b.get("name"): b for b in built["blocks"] if b["kind"] == "question"}
show("it labels", by_name["ruolo"]["charts"][0]["labels"])
ok(by_name["ruolo"]["charts"][0]["labels"] == ["Ricerca", "Docenza", "Studio"],
   "choice labels in Italian")
en = results.build(SCHEMA, [], REPORT, ANSWERS, "owner", "en")
en_by = {b.get("name"): b for b in en["blocks"] if b["kind"] == "question"}
ok(en_by["ruolo"]["charts"][0]["labels"] == ["Researcher", "Teaching", "Student"],
   "and in English, from the same aggregate: the export stores values, not labels")
ok(en_by["ruolo"]["title"] == "Your role", "the question title is localized too")
show("owner cells", en_by["ruolo"]["charts"][0]["series"][0]["data"])
ok(en_by["ruolo"]["charts"][0]["series"][0]["data"] == [7, 6, 2],
   "the owner sees the real counts even in a published block: how far a block "
   "travels decides who sees it, not what the owner is shown")

print("\n--- what each audience is built ---")
pub = results.build(SCHEMA, [], REPORT, ANSWERS, "public", "en")
names = [b.get("name") for b in pub["blocks"] if b["kind"] == "question"]
show("public blocks", names)
ok("note" not in names, "an owner-only block is not built for the public at all")
ok("note" in [b.get("name") for b in en["blocks"] if b["kind"] == "question"],
   "and the owner still gets it")
owner_note = en_by["note"]
ok(any("zzOpenAnswerAlpha" in str(r) for t in owner_note["tables"] for r in t["rows"]),
   "the owner reads the open answers")

pub_ruolo = next(b for b in pub["blocks"] if b.get("name") == "ruolo")
show("public cells", pub_ruolo["charts"][0]["series"][0]["data"])
ok(None in pub_ruolo["charts"][0]["series"][0]["data"],
   "a cell under the threshold arrives as a hole, not as a number")
ok(len(pub_ruolo["charts"][0]["suppressed"]) >= 2,
   "and it takes a second cell with it, so the total cannot give it away")

print("\n--- when everything is masked ---")
# Twelve people spread over five rating points: no point reaches five, so every
# cell is masked and the chart would be an empty canvas.
spread = [{"ruolo": "a", "score": (i % 5) + 1} for i in range(12)]
thin = results.build(SCHEMA, [], REPORT, spread, "public", "en")
thin_score = next(b for b in thin["blocks"] if b.get("name") == "score")
show("charts / note", (len(thin_score["charts"]), thin_score["note"]))
ok(thin_score["charts"] == [],
   "a chart with nothing left to draw is dropped instead of rendered blank")
ok("fewer than five" in (thin_score["note"] or ""), "and the page says why")
ok(thin_score["tables"], "the table stays, with the masks visible as masks")

# A rating keeps its unchosen points as genuine zeros, and a zero draws a bar of
# no height. A chart of masks and zeros passed the first version of this check
# and still rendered blank.
zeros = [{"ruolo": "a", "score": 3} for _ in range(4)]
flat = results.build(SCHEMA, [], REPORT, zeros, "public", "en")
flat_score = next(b for b in flat["blocks"] if b.get("name") == "score")
show("zeros only", [s["data"] for c in flat_score["charts"] for s in c["series"]] or "no chart")
ok(flat_score["charts"] == [], "zeros are not something to draw either")

print("\n--- the respondent's own answer ---")
mine = results.build(SCHEMA, [], REPORT, ANSWERS, "respondent", "en",
                     mine={"ruolo": "b", "score": 4})
mine_ruolo = next(b for b in mine["blocks"] if b.get("name") == "ruolo")
show("mine", mine_ruolo["charts"][0]["mine"])
ok(mine_ruolo["charts"][0]["mine"] == [1], "their own choice is marked by index")
ok(next(b for b in pub["blocks"] if b.get("name") == "ruolo")["charts"][0]["mine"] is None,
   "and nobody else's page marks anything")

print("\n--- every chart has its numbers ---")
for b in en["blocks"]:
    if b["kind"] == "question" and not b.get("below_threshold"):
        if not b["tables"]:
            ok(False, f"{b['name']} has no table under it")
ok(all(b["tables"] for b in en["blocks"]
       if b["kind"] == "question" and not b.get("below_threshold")),
   "a chart without a table would be unreadable with JavaScript off")

# --- routes ---

with TestClient(main.app) as client:
    db = main.get_db()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("owner@x.test", "o", "x"))
    owner_id = db.execute("SELECT id FROM users WHERE email='owner@x.test'").fetchone()["id"]
    for slug, audience in (("open", "public"), ("shut", "owner"), ("resp", "respondent")):
        rep = dict(REPORT, audience=audience)
        db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active, "
                   "report_json) VALUES (?,?,?,?,1,?)",
                   (slug, slug, json.dumps(SCHEMA), owner_id, json.dumps(rep)))
        sid = db.execute("SELECT id FROM surveys WHERE slug=?", (slug,)).fetchone()["id"]
        for a in ANSWERS:
            db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                       (sid, json.dumps(a)))
    db.commit()
    cols = {r[1] for r in db.execute("PRAGMA table_info(responses)").fetchall()}
    db.close()
    owner_cookie = {"session": auth.make_token(owner_id, "full")}

    print("\n--- storage ---")
    ok("view_token" in cols, "the migration adds the read-only handle to responses")

    print("\n--- who can open what ---")
    r = client.get("/s/open/results")
    ok(r.status_code == 200 and "What we found" in r.text, "a public report is public")
    ok("Researcher" in r.text and "<table" in r.text,
       "and its numbers are in the HTML, so the page reads with JavaScript off")
    ok("<summary>the numbers</summary>" in r.text,
       "with a label on the table: the first version shadowed the string table "
       "with the loop variable and rendered an empty summary")

    it = client.get("/s/open/results?lang=it")
    ok("i numeri" in it.text and "hanno risposto" in it.text,
       "the page's own words follow the language, not only the questionnaire's")
    ok("answered" not in it.text.split("<script")[0],
       "and no English is left in the Italian body")
    ok("zzOpenAnswerAlpha" not in r.text and "zzOpenAnswerBeta" not in r.text,
       "open answers are not in it")

    r = client.get("/s/shut/results")
    ok(r.status_code == 404, "a report the owner kept private answers 404")
    r = client.get("/s/resp/results")
    ok(r.status_code == 404, "so does one meant for respondents, without a token")
    r = client.get("/s/nope/results")
    ok(r.status_code == 404, "and so does a survey that does not exist: the same answer")

    r = client.get("/admin/surveys/shut/results", cookies=owner_cookie)
    ok(r.status_code == 200 and "Note" in r.text,
       "the owner reads their own private report")
    r = client.get("/admin/surveys/shut/results", follow_redirects=False)
    ok(r.status_code == 302, "signed out does not")

    print("\n--- submitting earns a look ---")
    r = client.post("/s/resp/submit",
                    json={"ruolo": "a", "score": 3, "note": "zzMyOwnAnswer"})
    body = r.json()
    show("submit", body)
    ok(body.get("results_url", "").startswith("/s/resp/results?r="),
       "a survey whose report reaches respondents hands back a link")
    r2 = client.post("/s/shut/submit", json={"ruolo": "a"})
    ok(r2.json().get("results_url") is None,
       "one whose report does not reach them hands back nothing to click")

    page = client.get(body["results_url"])
    ok(page.status_code == 200, "the link opens")
    ok("your own answers are marked" in page.text, "and says so")
    ok("zzMyOwnAnswer" not in page.text and "zzOpenAnswerAlpha" not in page.text,
       "without showing them anybody's open answers, their own included")
    ok(client.get("/s/resp/results?r=made-up-token").status_code == 404,
       "a token nobody minted opens nothing")

    print("\n--- looking as somebody else ---")
    own = client.get("/admin/surveys/open/results", cookies=owner_cookie)
    ok("zzOpenAnswerAlpha" in own.text, "the owner's own view holds the open answers")
    as_pub = client.get("/admin/surveys/open/results?as=public", cookies=owner_cookie)
    ok(as_pub.status_code == 200 and "looking as" in as_pub.text,
       "the owner can look at their page as anyone else")
    ok("zzOpenAnswerAlpha" not in as_pub.text,
       "and the preview really is filtered, not a label over the same page")
    ok("Edit report" in as_pub.text,
       "while keeping the admin chrome, so it is clear whose page this is")

    print("\n--- live, and embedded ---")
    j = client.get("/s/open/results.json")
    ok(j.status_code == 200 and "blocks" in j.json() and j.json()["updated_at"],
       "the page has something to poll, stamped with the time")
    e = client.get("/s/open/results/embed")
    ok(e.status_code == 200 and "Survey Admin" not in e.text and "<canvas" in e.text,
       "the embed view drops the chrome and keeps the charts")

    print("\n--- the cache lets go when the data moves ---")
    before = client.get("/s/open/results.json").json()
    n_before = next(b for b in before["blocks"] if b.get("name") == "ruolo")["n"]
    client.post("/s/open/submit", json={"ruolo": "a", "score": 2})
    after = client.get("/s/open/results.json").json()
    n_after = next(b for b in after["blocks"] if b.get("name") == "ruolo")["n"]
    show("n before/after", (n_before, n_after))
    ok(n_after == n_before + 1,
       "a new response shows up at once instead of waiting for the cache to age out")

    print("\n--- chart.js is ours, not a CDN's ---")
    r = client.get("/static/chart.umd.min.js")
    ok(r.status_code == 200 and "Chart.js v4" in r.text[:400],
       "the library is served from this app")
    page = client.get("/s/open/results")
    ok("/static/chart.umd.min.js" in page.text and "cdn." not in page.text,
       "and the page asks for it by that path, with no network dependency")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
