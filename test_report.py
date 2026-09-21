"""Checks for report.py and the report editor: what a stored report may contain,
how far each block reaches, and that nothing a browser posts is trusted.

The model half needs no database. The route half signs its own session against a
throwaway one, the way the other route checks here do.
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
import main, auth, report as R

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def show(label, value):
    print(f"    [{label}] {value}")
    return value


SCHEMA = {
    "title": {"default": "T", "it": "T"},
    "pages": [
        {"name": "p1", "elements": [
            {"type": "html", "name": "blurb", "html": {"default": "hi", "it": "ciao"}},
            {"type": "radiogroup", "name": "ruolo",
             "title": {"default": "Role", "it": "Ruolo"}, "choices": ["a", "b"]},
            {"type": "comment", "name": "note", "title": {"default": "Note", "it": "Nota"}},
        ]},
        {"name": "p2", "elements": [
            {"type": "rating", "name": "score", "title": {"default": "Score", "it": "Voto"}},
        ]},
    ],
}

print("\n--- what may be stored ---")
clean = R.normalise({
    "audience": "everyone",                                   # not an audience
    "blocks": [
        {"kind": "text", "md": {"default": "# Hello", "it": "# Ciao", "xx": "nope"},
         "extra": "dropped"},
        {"kind": "question", "name": "ruolo", "chart": "sunburst",
         "value": "fraction", "sort": "random", "other": "maybe"},
        {"kind": "sabotage", "md": {"default": "x"}},
        {"kind": "text", "md": {"default": "   "}},
        {"kind": "question"},
        {"kind": "all_questions", "audience": "public"},
    ],
}, SCHEMA)
show("audience", clean["audience"])
show("blocks", clean["blocks"])
ok(clean["audience"] == "owner", "an audience nobody defined falls back to the narrowest")
ok([b["kind"] for b in clean["blocks"]] == ["text", "question", "all_questions"],
   "unknown kinds, empty text and a nameless question block are dropped")
ok("xx" not in clean["blocks"][0]["md"] and "extra" not in clean["blocks"][0],
   "a locale the platform does not speak, and stray keys, do not survive the trip")
q = clean["blocks"][1]
ok(q["chart"] == "bar" and q["value"] == "count" and q["sort"] == "schema"
   and q["other"] == "show",
   "options outside the allowed set fall back to the default for that shape")

print("\n--- letting readers explore is a switch, and it is off ---")
ok(R.normalise({"blocks": []}, SCHEMA)["explore"] is False,
   "a report nobody configured does not invite readers to cross questions")
on = R.normalise({"audience": "public", "explore": True, "blocks": [
    {"kind": "question", "name": "ruolo"},
    {"kind": "question", "name": "note", "audience": "owner"}]}, SCHEMA)
ok(on["explore"] is True, "and the switch survives the trip through normalise")
show("published to the public", sorted(R.published_names(on, SCHEMA, "public")))
ok(R.published_names(on, SCHEMA, "public") == {"ruolo"},
   "what a reader may cross is what the report already shows them")
ok("note" in R.published_names(on, SCHEMA, "owner"), "the owner reaches both")

plain = R.normalise({"blocks": [{"kind": "text", "md": "just a string"}]}, SCHEMA)
ok(plain["blocks"][0]["md"] == {"default": "just a string"},
   "a report written before anyone thought about translation reads as the default locale")

orphan = R.normalise({"blocks": [{"kind": "question", "name": "gone", "chart": "pie"}]}, SCHEMA)
ok(orphan["blocks"][0]["chart"] == "pie",
   "an orphan keeps its options: the question may come back under the same name")

print("\n--- how far a block reaches ---")
rep = {"audience": "public", "blocks": [
    {"kind": "question", "name": "ruolo"},                      # inherits public
    {"kind": "question", "name": "score", "audience": "owner"},  # keeps its own
]}
ok(R.effective_audience(rep, rep["blocks"][0]) == "public", "a block with no level inherits")
ok(R.effective_audience(rep, rep["blocks"][1]) == "owner", "a block with one keeps it")

capped = {"audience": "respondent", "blocks": [{"kind": "question", "name": "ruolo",
                                                "audience": "public"}]}
ok(R.effective_audience(capped, capped["blocks"][0]) == "respondent",
   "the report is a ceiling: a public block in a report that is not public is not public")

ok(R.visible_to(rep, rep["blocks"][1], "owner"), "the owner sees an owner-only block")
ok(not R.visible_to(rep, rep["blocks"][1], "respondent"), "a respondent does not")
ok(not R.visible_to(rep, rep["blocks"][1], "public"), "and neither does the public")
ok(R.visible_to(rep, rep["blocks"][0], "public"), "a public block reaches the public")

print("\n--- resolving for a viewer ---")
mixed = {"audience": "public", "blocks": [
    {"kind": "text", "md": {"default": "intro"}},
    {"kind": "all_questions"},
    {"kind": "question", "name": "note", "audience": "owner"},
]}
seen = R.resolve(mixed, SCHEMA, "public")
show("public sees", [(b["kind"], b.get("name")) for b in seen])
ok([b.get("name") for b in seen if b["kind"] == "question"] == ["ruolo", "note", "score"],
   "all_questions expands in schema order, and skips the html element")
ok(len(R.resolve(mixed, SCHEMA, "owner")) == len(seen) + 1,
   "the owner additionally sees the owner-only block")
ok(all(b["audience"] == "public" for b in seen),
   "expanded questions inherit the level of the block they came from")

print("\n--- what the editor is told ---")
f = R.validate({"audience": "owner", "blocks": [
    {"kind": "question", "name": "ghost"},
    {"kind": "question", "name": "ruolo"},
    {"kind": "question", "name": "ruolo"},
    {"kind": "text", "md": {"default": "only english"}},
]}, SCHEMA)
show("findings", [(x["severity"], x["kind"]) for x in f])
kinds = [x["kind"] for x in f]
ok("orphan_block" in kinds, "a block naming a question the schema lost is reported")
ok("duplicate_block" in kinds, "so is the same question twice")
ok("untranslated_text" in kinds, "so is a text block that exists in one of the two languages")

f = R.validate({"audience": "public", "blocks": [
    {"kind": "question", "name": "ruolo", "audience": "owner"}]}, SCHEMA)
ok(any(x["kind"] == "nothing_published" for x in f),
   "a shared report whose every block is private says so, instead of serving an empty page")

ok(R.locales(SCHEMA) == ["en", "it"], "text blocks are offered the languages the survey speaks")
ok(R.locales({"locale": "it", "pages": [{"name": "p", "elements": [
    {"type": "text", "name": "q", "title": "Quanti anni hai?"}]}]}) == ["it"],
   "a questionnaire written in one language is taken at its declared word, "
   "instead of defaulting to English over Italian questions")
ok(R.locales({"pages": []}) == ["en"], "and with nothing to go on, English")

# --- routes ---

with TestClient(main.app) as client:
    db = main.get_db()
    for email in ("owner@x.test", "other@x.test"):
        db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
                   (email, email, "x"))
    owner_id = db.execute("SELECT id FROM users WHERE email='owner@x.test'").fetchone()["id"]
    other_id = db.execute("SELECT id FROM users WHERE email='other@x.test'").fetchone()["id"]
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) "
               "VALUES (?,?,?,?,1)", ("demo", "Demo", json.dumps(SCHEMA), owner_id))
    db.commit()
    cols = {r[1] for r in db.execute("PRAGMA table_info(surveys)").fetchall()}
    db.close()

    owner_cookie = {"session": auth.make_token(owner_id, "full")}
    other_cookie = {"session": auth.make_token(other_id, "full")}
    pending_cookie = {"session": auth.make_token(owner_id, "pending")}

    print("\n--- storage ---")
    ok("report_json" in cols, "the migration adds the column to an existing surveys table")

    print("\n--- the editor is the owner's ---")
    r = client.get("/admin/surveys/demo/report", follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/login",
       "signed out goes to /login")
    r = client.get("/admin/surveys/demo/report", cookies=pending_cookie,
                   follow_redirects=False)
    ok(r.status_code == 302, "half-authenticated (pre-TOTP) does not get in")
    r = client.get("/admin/surveys/demo/report", cookies=other_cookie,
                   follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"] == "/admin",
       "someone else's survey bounces to /admin")

    r = client.get("/admin/surveys/demo/report", cookies=owner_cookie)
    ok(r.status_code == 200 and "Results report" in r.text, "the owner gets the editor")
    ok("ruolo" in r.text and "score" in r.text,
       "and the questionnaire's questions are in it, ready to add")

    print("\n--- saving ---")
    r = client.post("/admin/surveys/demo/report", cookies=other_cookie,
                    json={"audience": "public", "blocks": []})
    ok(r.status_code == 404, "a non-owner cannot save, and is told 'not found' rather than 'no'")

    r = client.post("/admin/surveys/demo/report", cookies=owner_cookie,
                    content="not json at all",
                    headers={"Content-Type": "application/json"})
    ok(r.status_code == 400, "a body that is not JSON is refused rather than stored")

    payload = {"audience": "public", "blocks": [
        {"kind": "text", "md": {"default": "# Results", "it": "# Risultati"}},
        {"kind": "question", "name": "ruolo", "chart": "pie", "value": "percent"},
        {"kind": "explode-the-server", "md": {"default": "x"}},
    ]}
    r = client.post("/admin/surveys/demo/report", cookies=owner_cookie, json=payload)
    body = r.json()
    show("stored", body["report"])
    ok(r.status_code == 200 and body["ok"], "the owner saves")
    ok(len(body["report"]["blocks"]) == 2,
       "what comes back is what was stored, with the unknown kind already gone")

    db = main.get_db()
    stored = json.loads(db.execute(
        "SELECT report_json FROM surveys WHERE slug='demo'").fetchone()["report_json"])
    db.close()
    ok(stored == body["report"], "and the database holds exactly what the answer showed")
    ok(all(b["kind"] in R.KINDS for b in stored["blocks"]),
       "nothing a browser invented reaches the column a public page will be built from")

    r = client.get("/admin/surveys/demo", cookies=owner_cookie)
    ok("Results report" in r.text and "visible to anyone" in r.text,
       "the manage page says the report exists and how far it reaches")

    print("\n--- the links to hand out ---")
    ok("/s/demo/results<" in r.text.replace("</code>", "<"),
       "a published report puts its own link on the manage page")
    ok("/s/demo/results/embed" in r.text and "iframe" in r.text,
       "with an iframe snippet for putting it inside another site")
    ok(r.text.count("<img src=\"data:image/png;base64") >= 2,
       "and a QR beside it, as the questionnaire link already had")

    ed = client.get("/admin/surveys/demo/report", cookies=owner_cookie)
    ok("share-url" in ed.text and "savedAudience" in ed.text,
       "the editor carries the same link, driven by what is saved")

    client.post("/admin/surveys/demo/report", cookies=owner_cookie,
                json={"audience": "owner", "blocks": [{"kind": "all_questions"}]})
    private = client.get("/admin/surveys/demo", cookies=owner_cookie)
    ok("nothing to share yet" in private.text,
       "a private report offers no link: there would be nothing behind it")
    ok(private.text.count("<img src=\"data:image/png;base64") == 1,
       "and no QR for a page that answers 404")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
