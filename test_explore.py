"""Checks for explore.py and its route: which test gets picked, what comes back
with it, and that none of it is reachable by anybody but the owner.

The fixtures are built so the right answer is known in advance — a pair that is
perfectly associated, one that is perfectly independent — rather than asserted
against whatever the code happened to produce.
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
import main, auth, explore

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def show(label, value):
    print(f"    [{label}] {value}")
    return value


SCHEMA = {"locale": "it", "pages": [{"name": "p", "elements": [
    {"type": "html", "name": "blurb", "html": "<p>x</p>"},
    {"type": "radiogroup", "name": "gruppo", "title": "Gruppo",
     "choices": [{"value": "a", "text": "A"}, {"value": "b", "text": "B"}]},
    {"type": "radiogroup", "name": "eco", "title": "Eco del gruppo",
     "choices": [{"value": "a", "text": "A"}, {"value": "b", "text": "B"}]},
    {"type": "radiogroup", "name": "tre", "title": "Tre livelli",
     "choices": [{"value": "x", "text": "X"}, {"value": "y", "text": "Y"},
                 {"value": "z", "text": "Z"}]},
    {"type": "rating", "name": "voto", "title": "Voto", "rateMin": 1, "rateMax": 5},
    {"type": "rating", "name": "altro_voto", "title": "Altro voto",
     "rateMin": 1, "rateMax": 5},
    {"type": "text", "name": "eta", "title": "Età", "inputType": "number"},
    {"type": "checkbox", "name": "scelte", "title": "Scelte",
     "choices": [{"value": "uno", "text": "Uno"}, {"value": "due", "text": "Due"}]},
    {"type": "comment", "name": "libero", "title": "Libero"},
]}]}

# Thirty people. `eco` repeats `gruppo` exactly, so their association is
# perfect; `tre` cycles independently of it; `voto` tracks `gruppo` and
# `altro_voto` tracks `voto`; `eta` is unrelated to everything.
R = []
for i in range(30):
    g = "a" if i % 2 == 0 else "b"
    R.append({
        "gruppo": g,
        "eco": g,
        "tre": ["x", "y", "z"][i % 3],
        "voto": 4 + (i % 2),
        "altro_voto": 4 + (i % 2),
        "eta": 30 + (i * 7) % 23,
        "scelte": ["uno"] if i % 2 == 0 else ["due"],
        "libero": "qualcosa",
    })

print("\n--- which variables are offered ---")
vs = {v["id"]: v for v in explore.variables(SCHEMA, "it")}
show("variables", sorted(vs))
ok("libero" not in vs, "open text is not a variable to correlate")
ok("blurb" not in vs, "and neither is a block of html")
ok(vs["gruppo"]["kind"] == "nominal" and vs["voto"]["kind"] == "ordinal"
   and vs["eta"]["kind"] == "numeric", "each question arrives with its kind")
ok("scelte:uno" in vs and "scelte:due" in vs and "scelte" not in vs,
   "a checkbox becomes one yes/no variable per option, not one muddled variable")
ok(vs["scelte:uno"]["label"].endswith("Uno"), "labelled with the option it stands for")

print("\n--- the test follows the shapes ---")
cases = [("gruppo", "eco", "fisher"), ("gruppo", "tre", "chi2"),
         ("gruppo", "voto", "mannwhitney"), ("tre", "voto", "kruskal"),
         ("voto", "altro_voto", "spearman"), ("eta", "voto", "spearman")]
for x, y, expected in cases:
    got = explore.associate(SCHEMA, R, x, y, "it")
    print(f"    {x} × {y:<12} -> {got.get('test')}  p={got.get('p')}")
    ok(got.get("test") == expected, f"{x} × {y} is a {expected}")

print("\n--- the answers are the known ones ---")
perfect = explore.associate(SCHEMA, R, "gruppo", "eco", "it")
ok(perfect["p"] < 0.001 and perfect["effect"]["value"] == 1.0,
   "a variable against its own echo is a perfect association, V = 1")
ok(perfect["effect"]["band"] == "large", "and reads as large")
ok(perfect["table"]["counts"] == [[15, 0], [0, 15]],
   "with the contingency table to look at")

indep = explore.associate(SCHEMA, R, "gruppo", "tre", "it")
show("gruppo × tre", (round(indep["p"], 3), indep["effect"]["value"]))
ok(indep["p"] > 0.2, "a variable that cycles independently shows nothing")

rank = explore.associate(SCHEMA, R, "voto", "altro_voto", "it")
ok(rank["effect"]["value"] == 1.0 and rank["effect"]["name"] == "rho",
   "two ratings that move together give rho = 1")

groups = explore.associate(SCHEMA, R, "gruppo", "voto", "it")
show("groups", [(g["label"], g["n"], g["median"]) for g in groups["groups"]])
ok(len(groups["groups"]) == 2 and groups["groups"][0]["median"] == 4,
   "a group comparison comes back with each group's n and middle")

print("\n--- what it refuses ---")
ok(explore.associate(SCHEMA, R, "voto", "voto", "it")["error"] == "same_variable",
   "a variable against itself is refused rather than answered with 1")
ok(explore.associate(SCHEMA, R, "voto", "nonesiste", "it")["error"] == "unknown_variable",
   "and so is a name the schema does not have")
few = explore.associate(SCHEMA, R[:4], "gruppo", "voto", "it")
ok(few["error"] == "too_few" and few["n"] == 4,
   "four people are not enough, and it says how many there were")
flat = [{"gruppo": "a", "voto": 3} for _ in range(20)]
ok(explore.associate(SCHEMA, flat, "gruppo", "voto", "it")["error"] == "no_variation",
   "a variable that never varies has nothing to compare")

print("\n--- the asterisks, and what they are worth ---")
import results as RS
show("stars", {round(p, 4): explore._stars(p)
               for p in (0.0001, 0.005, 0.03, 0.2)})
ok(explore._stars(0.0001) == "***" and explore._stars(0.005) == "**"
   and explore._stars(0.03) == "*" and explore._stars(0.2) == "",
   "the usual three thresholds, and nothing above the top one")
ok(explore._stars(None) == "", "and nothing at all when there is no p")
ok(perfect["stars"] == "***", "they travel with the result")

topics = RS.help_for("it")
ok(set(topics) >= {"how", "spearman", "mannwhitney", "kruskal", "chi2", "fisher"},
   "there is an explanation for how to read a result and for every test offered")
ok(all(t["test"] in topics for t in
       [{"test": k} for k in ("spearman", "mannwhitney", "kruskal", "chi2", "fisher")]),
   "each test name matches the topic the panel will ask for")
ok("causa" in " ".join(topics["how"]["body"]).lower(),
   "the reading guide says that moving together is not causing")
ok("Pearson" in " ".join(topics["spearman"]["body"])
   and "t" in " ".join(topics["mannwhitney"]["body"]),
   "and each test says which familiar one it is standing in for")
ok(RS.help_for("de")["chi2"]["title"].startswith("Chi-square"),
   "a language with no translation falls back per topic rather than breaking")

print("\n--- the caveats travel with the answer ---")
kinds = [c["kind"] for c in perfect["caveats"]]
show("caveats", kinds)
ok("exploratory" in kinds, "every answer says it is exploratory")
ok("multiple" in kinds,
   "and one below 0.05 is reminded how many pairs were available to try")
small = explore.associate(SCHEMA, R[:12], "gruppo", "voto", "it")
ok(any(c["kind"] == "small_n" for c in small["caveats"]),
   "a thin sample says so on the result rather than in a footnote")
ok(explore.pair_count(SCHEMA) == 28,
   "and the number of pairs on offer is counted, since that is the multiplier")

# --- the route ---

with TestClient(main.app) as client:
    db = main.get_db()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("owner@x.test", "o", "x"))
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("other@x.test", "t", "x"))
    owner_id = db.execute("SELECT id FROM users WHERE email='owner@x.test'").fetchone()["id"]
    other_id = db.execute("SELECT id FROM users WHERE email='other@x.test'").fetchone()["id"]
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active, "
               "report_json) VALUES (?,?,?,?,1,?)",
               ("demo", "Demo", json.dumps(SCHEMA), owner_id,
                json.dumps({"audience": "public", "blocks": [{"kind": "all_questions"}]})))
    sid = db.execute("SELECT id FROM surveys WHERE slug='demo'").fetchone()["id"]
    R_ROWS = list(R)
    for r in R:
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps(r)))
    db.commit(); db.close()
    owner_cookie = {"session": auth.make_token(owner_id, "full")}
    other_cookie = {"session": auth.make_token(other_id, "full")}

    print("\n--- the panel is the owner's alone ---")
    url = "/admin/surveys/demo/explore.json?x=gruppo&y=eco"
    ok(client.get(url).status_code == 401, "signed out gets nothing")
    ok(client.get(url, cookies=other_cookie).status_code == 404,
       "another account gets 'not found', like everywhere else here")
    r = client.get(url, cookies=owner_cookie)
    ok(r.status_code == 200 and r.json()["test"] == "fisher", "the owner gets the answer")
    ok(r.json()["pairs_available"] == 28, "with the pair count the warning needs")

    page = client.get("/admin/surveys/demo/results", cookies=owner_cookie)
    ok('class="fab"' in page.text and "explore.json" in page.text,
       "the owner's page carries the button")
    # The panel is a few lines of script on a page that defines its own
    # globals; the first version reached for one this template never set.
    for name in ("SLUG", "LOCALE", "EXPLORE_PAIRS", "T"):
        ok(re.search(r"const\s+" + name + r"\s*=", page.text),
           f"and defines {name}, which its script reads")
    ok('<option value="scelte:uno"' in page.text,
       "and the picker knows the checkbox options")

    public = client.get("/s/demo/results")
    # Written out rather than chained: `a in b is False` is a chained
    # comparison and quietly asks something else entirely.
    ok(('class="fab"' not in public.text) and ("explore.json" not in public.text),
       "the public page carries neither the button nor the address of the route")
    preview = client.get("/admin/surveys/demo/results?as=public", cookies=owner_cookie)
    ok("explore.json" not in preview.text,
       "and it is gone from the owner's preview too, which is what the preview is for")

    print("\n--- letting readers in, one survey at a time ---")
    import report as R
    db = main.get_db()
    rep = {"audience": "public", "blocks": [{"kind": "all_questions"}]}
    db.execute("UPDATE surveys SET report_json=? WHERE slug='demo'", (json.dumps(rep),))
    db.commit(); db.close()
    main._invalidate_results(sid)

    pub_url = "/s/demo/explore.json?x=gruppo&y=eco"
    ok(client.get(pub_url).status_code == 404,
       "with the switch off, the reader's route is not there at all")
    page = client.get("/s/demo/results")
    ok('class="fab"' not in page.text, "and the public page carries no button")

    rep["explore"] = True
    db = main.get_db()
    db.execute("UPDATE surveys SET report_json=? WHERE slug='demo'", (json.dumps(rep),))
    db.commit(); db.close()
    main._invalidate_results(sid)

    r = client.get(pub_url)
    body = r.json()
    show("public answer", {k: body.get(k) for k in ("test", "p", "n")})
    ok(r.status_code == 200 and body["test"] == "fisher",
       "with it on, a reader gets the same test")
    ok(body["effect"]["value"] == 1.0, "and the same effect size, computed on everything")
    ok(client.get("/s/demo/results").text.count('class="fab"') == 1,
       "and the page carries the button")

    print("\n--- but not the people behind the cells ---")
    owner_view = client.get("/admin/surveys/demo/explore.json?x=gruppo&y=eco",
                            cookies=owner_cookie).json()
    show("owner cells", owner_view["table"]["counts"])
    show("public cells", body["table"]["counts"])
    ok(owner_view["table"]["counts"] == [[15, 0], [0, 15]],
       "the owner sees the table as it is")
    ok(body["table"]["counts"] == [[15, 0], [0, 15]],
       "and so does a reader when every cell is large enough")

    thin = [{"gruppo": "a", "eco": "a"}] * 18 + [{"gruppo": "b", "eco": "b"}] * 3
    db = main.get_db()
    db.execute("DELETE FROM responses WHERE survey_id=?", (sid,))
    for t in thin:
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps(t)))
    db.commit(); db.close()
    main._invalidate_results(sid)
    masked = client.get(pub_url).json()
    show("thin, public", masked["table"]["counts"])
    ok(any(c is None for row in masked["table"]["counts"] for c in row),
       "a cell of three people is hidden from a reader")
    ok(sum(1 for row in masked["table"]["counts"] for c in row if c is None) >= 2,
       "with a second one, so the margins cannot give it back")
    ok(masked["p"] is not None and masked["effect"]["value"] is not None,
       "while the test and the effect still come back: they are aggregates")
    ok(any(c["kind"] == "cells_hidden" for c in masked["caveats"]),
       "and the page says the cells were hidden")
    owner_thin = client.get("/admin/surveys/demo/explore.json?x=gruppo&y=eco",
                            cookies=owner_cookie).json()
    ok(all(c is not None for row in owner_thin["table"]["counts"] for c in row),
       "the owner still sees them")

    print("\n--- and not everything, and not too early ---")
    few = [{"gruppo": "a", "eco": "a"}] * 8 + [{"gruppo": "b", "eco": "b"}] * 7
    db = main.get_db()
    db.execute("DELETE FROM responses WHERE survey_id=?", (sid,))
    for t in few:
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps(t)))
    db.commit(); db.close()
    main._invalidate_results(sid)
    # The floor is the same for everybody. Whether a page should be open at
    # all is the decision of whoever runs the study, taken once with the
    # switch, and not something to second-guess a second time per request.
    ok(not client.get(pub_url).json().get("error"),
       "fifteen is enough for a reader too: the floor does not move with the audience")
    ok(client.get(pub_url).json()["n"] == 15, "and it is the same fifteen pairs")

    db = main.get_db()
    db.execute("DELETE FROM responses WHERE survey_id=?", (sid,))
    for row in R_ROWS:
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps(row)))
    half = {"audience": "public", "explore": True, "blocks": [
        {"kind": "question", "name": "gruppo"},
        {"kind": "question", "name": "eco", "audience": "owner"}]}
    db.execute("UPDATE surveys SET report_json=? WHERE slug='demo'", (json.dumps(half),))
    db.commit(); db.close()
    main._invalidate_results(sid)
    ok(client.get(pub_url).json().get("error") == "not_published",
       "a question the report keeps back cannot be put on an axis either")
    picker = client.get("/s/demo/results").text
    ok('value="gruppo"' in picker and 'value="eco"' not in picker,
       "and it is not in the reader's picker to begin with")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
