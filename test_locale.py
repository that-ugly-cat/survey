"""Checks for the language a respondent is spoken to in.

A questionnaire written in plain strings is in the language its schema declares
(`locale`), as SurveyJS reads it; counting those strings as English made an
Italian questionnaire show "Next" and the English required-answer messages under
Italian questions, with the page declaring lang="en", and made the manage page
and the review document call it English. The language detection of the page is
run in node, extracted from templates/survey.html so the test cannot drift from
it. Then the pages a respondent meets outside the questionnaire: a closed survey
in its own language, an unknown one in the reader's, and the panel refusals."""
import json, os, re, subprocess, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["AUTH_MODE"] = "local"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import main
import review_export

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

ITALIAN = {"locale": "it", "pages": [{"name": "p", "elements": [
    {"type": "radiogroup", "name": "q", "title": "Ti piace?", "choices": ["sì", "no"]}]}]}
PLAIN = {"pages": [{"name": "p", "elements": [{"type": "text", "name": "q", "title": "Your age"}]}]}
BILINGUAL_IT = {"locale": "it", "pages": [{"name": "p", "elements": [
    {"type": "text", "name": "q", "title": {"default": "Età", "en": "Age"}}]}]}

print("\n--- the page's language detection, run as written ---")
src = open("templates/survey.html", encoding="utf-8").read()
m = re.search(r"(function schemaLocale\(schema\).*?\n    function getAvailableLocales\(obj\).*?\n    \}\n)", src, re.S)
ok(m is not None, "the detection functions are where the test expects them")
if m:
    cases = {"italian": ITALIAN, "plain": PLAIN, "bilingual": BILINGUAL_IT,
             "declared_unknown": {"locale": "xx", "pages": []},
             "translated": {"pages": [{"name": "p", "elements": [
                 {"type": "text", "name": "q", "title": {"default": "Age", "de": "Alter"}}]}]}}
    script = ('var LANGS = ["en", "de", "fr", "it"];\n' + m.group(1) +
              "\nvar cases = " + json.dumps(cases) + ";\n"
              "var out = {}; Object.keys(cases).forEach(function (k) { out[k] = getAvailableLocales(cases[k]); });\n"
              "console.log(JSON.stringify(out));\n")
    path = os.path.join(tmp, "locale.js")
    open(path, "w", encoding="utf-8").write(script)
    res = subprocess.run(["node", path], capture_output=True, text=True)
    if res.returncode:
        ok(False, "node could not run the detection: " + res.stderr[:200])
    else:
        got = json.loads(res.stdout)
        ok(got["italian"] == ["it"], "plain strings under locale it are Italian, not English")
        ok(got["plain"] == ["en"], "plain strings with no locale are English, as SurveyJS assumes")
        ok(got["bilingual"] == ["en", "it"], "default keys follow the declared locale, beside the translations")
        ok(got["declared_unknown"] == ["en"], "a locale the platform does not speak falls back to English")
        ok(got["translated"] == ["en", "de"], "a questionnaire with translations offers them")

print("\n--- the server's scans ---")
ok(review_export.schema_locale(ITALIAN) == "it" and review_export.schema_locale(PLAIN) == "en",
   "schema_locale reads the declaration, English without one")
ok(review_export._locales_in(BILINGUAL_IT) == {"it", "en"}, "default keys count as the declared language")
ok(review_export._locales_in(PLAIN) == set(), "plain strings alone are no translation")
flag = review_export._translation_flag(
    {"type": "text", "name": "q", "title": "Età"}, {"it", "en"}, "it")
ok(flag and "MISSING: EN" in flag and "IT" not in flag.split("MISSING")[1],
   "the review document flags the missing English, not the Italian it is written in")

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    def add(slug, schema, active=1):
        db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,?)",
                   (slug, slug, json.dumps(schema), owner["id"], active))
        db.commit()

    add("ita", ITALIAN)
    add("eng", PLAIN)
    add("chiusa", ITALIAN, active=0)

    print("\n--- the questionnaire page ---")
    ok('<html lang="it">' in client.get("/s/ita").text, "an Italian questionnaire says so before its script runs")
    ok('<html lang="en">' in client.get("/s/eng").text, "an English one too")
    manage = client.get("/admin/surveys/ita").text
    ok('lang-chip">IT<' in manage and 'lang-chip">EN<' not in manage, "the manage page calls it Italian")

    print("\n--- the dead ends ---")
    r = client.get("/s/chiusa")
    ok(r.status_code == 404 and '<html lang="it">' in r.text and "Questionario non disponibile" in r.text,
       "a closed Italian survey says so in Italian")
    r = client.get("/s/chiusa", headers={"Accept-Language": "de-CH,de;q=0.9"})
    ok("Questionario non disponibile" in r.text, "in its own language, whatever the browser, when it speaks only one")
    r = client.get("/s/nobody", headers={"Accept-Language": "fr-CH,fr;q=0.9"})
    ok(r.status_code == 404 and "Questionnaire indisponible" in r.text and '<html lang="fr">' in r.text,
       "an unknown survey answers in the reader's language")
    ok("Survey not available" in client.get("/s/nobody").text, "and in English when the browser says nothing")
    r = client.get("/s/chiusa/results", headers={"Accept-Language": "fr"})
    ok(r.status_code == 404 and "Questionnaire indisponible" in r.text,
       "an unpublished report does not reveal its survey's language")

    db.execute("UPDATE surveys SET panel_token_param = 'RID' WHERE slug = 'ita'")
    db.commit()
    r = client.get("/s/ita")
    ok(r.status_code == 400 and "Link non valido" in r.text and '<html lang="it">' in r.text,
       "the panel's invalid-link page speaks the questionnaire's language")
    db.close()

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED: ' + '; '.join(FAILED)}")
sys.exit(1 if FAILED else 0)
