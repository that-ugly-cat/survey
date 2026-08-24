"""Checks for counterbalanced page order: the rand_pools column and its
migration, the admin save route, what the server hands the browser, and the
reorder snippet itself, executed in node exactly as templates/survey.html
ships it."""
import json, os, re, subprocess, sys, tempfile

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

PAGES = ["sociodem", "info_a", "info_b", "info_c",
         "vignette_pgt", "vignette_gge", "comparison"]


def page_names(schema):
    return [p["name"] for p in schema["pages"]]


with TestClient(main.app):
    print("\n--- schema and migration ---")
    db = main.get_db()
    cols = {r[1] for r in db.execute("PRAGMA table_info(rand_pools)").fetchall()}
    ok("page_order" in cols, "fresh database has rand_pools.page_order")

    # a table predating the column must gain it on the next init, the same way
    # condition_var and condition_map do
    db.execute("ALTER TABLE rand_pools DROP COLUMN page_order")
    db.commit(); db.close()
    main.init_db()
    db = main.get_db()
    cols = {r[1] for r in db.execute("PRAGMA table_info(rand_pools)").fetchall()}
    ok("page_order" in cols, "legacy table gains page_order on init")

    schema = {"pages": [{"name": n, "elements": [{"type": "text", "name": f"q_{n}"}]}
                        for n in PAGES]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id) VALUES (?,?,?,1)",
               ("po", "Page order", json.dumps(schema)))
    sid = db.execute("SELECT id FROM surveys WHERE slug='po'").fetchone()["id"]
    db.execute("""INSERT INTO rand_pools (survey_id, pool_pages, show_count,
                                          condition_var, condition_map, page_order)
                  VALUES (?,?,1,'condition',?,?)""",
               (sid,
                json.dumps(["info_a", "info_c"]),
                json.dumps({"info_a": "A", "info_c": "C2"}),
                json.dumps({"C2": ["vignette_gge", "vignette_pgt"]})))
    db.commit(); db.close()

    print("\n--- what the server hands the browser ---")
    # the pool has two arms; collect both by loading until each has been seen
    seen = {}
    for _ in range(40):
        body = client.get("/s/po").text
        conds = json.loads(body.split("var conditions      = ")[1].split(";\n")[0])
        orders = json.loads(body.split("var page_orders     = ")[1].split(";\n")[0])
        seen[conds.get("condition")] = orders
        if {"A", "C2"} <= set(seen):
            break
    ok(set(seen) == {"A", "C2"}, f"both arms observed, got {sorted(seen)}")
    ok(seen.get("A") == [], "arm without a page_order entry gets an empty list")
    ok(seen.get("C2") == [{"var": "condition", "pages": ["vignette_gge", "vignette_pgt"]}],
       "arm with an entry gets exactly its own sequence")

    print("\n--- admin save route ---")
    db = main.get_db()
    pid = db.execute("SELECT id FROM rand_pools WHERE survey_id=?", (sid,)).fetchone()["id"]
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    db.execute("UPDATE surveys SET owner_id=? WHERE id=?", (owner, sid))
    db.commit(); db.close()
    # the save route is behind auth; stand in for a signed-in owner
    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner,)).fetchone()

    def save(**over):
        form = {"pool_name": "P", "pool_pages": ["info_a", "info_c"], "show_count": "1",
                "condition_var": "condition",
                "condition_map": json.dumps({"info_a": "A", "info_c": "C2"}),
                "page_order": json.dumps({"C2": ["vignette_gge", "vignette_pgt"]})}
        form.update(over)
        client.post(f"/admin/surveys/po/randomization/{pid}/save", data=form,
                    follow_redirects=False)
        db = main.get_db()
        row = db.execute("SELECT page_order FROM rand_pools WHERE id=?", (pid,)).fetchone()
        db.close()
        return row["page_order"]

    stored = save()
    ok(stored and json.loads(stored) == {"C2": ["vignette_gge", "vignette_pgt"]},
       "valid page_order is stored")
    ok(save(page_order="{not json") is None, "malformed JSON is discarded")
    ok(save(page_order='{"C2": "vignette_gge"}') is None,
       "values that are not lists are discarded")
    ok(save(show_count="2") is None, "page_order is dropped when Show > 1")
    ok(save(condition_var="") is None, "page_order is dropped without a condition variable")

print("\n--- admin template ---")
t = main.templates.get_template("randomization.html")
pool = {"id": 1, "pool_name": "P", "pool_pages": ["info_a", "info_c"], "show_count": 1,
        "condition_var": "condition", "condition_map": "",
        "page_order": '{"C2": ["vignette_gge", "vignette_pgt"]}',
        "total": 0, "pending_total": 0, "counts": []}
html = t.render(request=None, slug="t", title="T", page_names=["info_a"], pools=[pool])
ok('name="page_order"' in html, "randomization.html renders the page_order field")
ok("vignette_gge" in html, "the stored value is rendered back into the textarea")
ok(html.count("cond-field-1") >= 3,
   "page_order is disabled alongside the other condition fields when Show > 1")
ok("Page order" in html, "the help modal documents page order")
# a pool row that predates the column must still render
del pool["page_order"]
try:
    t.render(request=None, slug="t", title="T", page_names=["info_a"], pools=[pool])
    ok(True, "renders for a pool with no page_order key")
except Exception as e:
    ok(False, f"renders for a pool with no page_order key ({e})")

print("\n--- the reorder snippet, run as templates/survey.html ships it ---")
tpl = open(os.path.join("templates", "survey.html"), encoding="utf-8").read()
m = re.search(r"(// --- randomization: reorder pages.*?)\n\s*var survey = new Survey\.Model",
              tpl, re.S)
if not m:
    FAILED.append("could not locate the reorder snippet in templates/survey.html")
    print("  FAIL  could not locate the reorder snippet in templates/survey.html")
else:
    snippet = m.group(1)
    cases = [
        ("adjacent pages are swapped",
         PAGES, [{"var": "condition", "pages": ["vignette_gge", "vignette_pgt"]}],
         ["sociodem", "info_a", "info_b", "info_c",
          "vignette_gge", "vignette_pgt", "comparison"]),
        ("no page_orders leaves the schema alone", PAGES, [], PAGES),
        ("unknown page names leave the schema alone",
         PAGES, [{"var": "condition", "pages": ["nope", "also_nope"]}], PAGES),
        ("a single resolvable name is a no-op",
         PAGES, [{"var": "condition", "pages": ["vignette_gge", "nope"]}], PAGES),
        ("non-adjacent pages keep the slots they occupy",
         ["a", "vignette_pgt", "filler", "vignette_gge", "z"],
         [{"var": "condition", "pages": ["vignette_gge", "vignette_pgt"]}],
         ["a", "vignette_gge", "filler", "vignette_pgt", "z"]),
        ("three pages rotate",
         ["a", "p1", "p2", "p3", "z"],
         [{"var": "condition", "pages": ["p3", "p1", "p2"]}],
         ["a", "p3", "p1", "p2", "z"]),
    ]
    harness = """
const cases = %s;
const out = cases.map(function (c) {
  var schema = {pages: c.names.map(function (n) { return {name: n}; })};
  var page_orders = c.orders;
  %s
  return schema.pages.map(function (p) { return p.name; });
});
console.log(JSON.stringify(out));
""" % (json.dumps([{"names": c[1], "orders": c[2]} for c in cases]), snippet)
    path = os.path.join(tempfile.mkdtemp(), "reorder.js")
    open(path, "w", encoding="utf-8").write(harness)
    res = subprocess.run(["node", path], capture_output=True, text=True)
    if res.returncode != 0:
        FAILED.append("node could not run the snippet")
        print("  FAIL  node could not run the snippet:\n" + res.stderr)
    else:
        got = json.loads(res.stdout)
        for (label, _, _, expected), actual in zip(cases, got):
            ok(actual == expected, label + ("" if actual == expected else f" — got {actual}"))

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
