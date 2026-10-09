"""Checks for item banks (docs/item-banks-spec.md): allocation balance by simulation,
quotas, tie-breaking, concurrency, the pending window, validation, the round trip
through the page and the submit, the long export, the admin and MCP surfaces, and the
guard on pools whose combinations would block the server."""
import collections, json, os, random, sys, tempfile, threading

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import auth
import banks
import flow
import main

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

N_ITEMS = 80
TURING = {
    "name": "passages",
    "items": [{"item": f"P{i:03d}", "versions": {"H": f"P{i:03d}_H", "L": f"P{i:03d}_L"}}
              for i in range(1, N_ITEMS + 1)],
    "show_count": 10, "arm_var": "arm", "order": "random",
    "arms": [{"name": "LLM30", "quota": {"H": 7, "L": 3}},
             {"name": "LLM50", "quota": {"H": 5, "L": 5}},
             {"name": "LLM70", "quota": {"H": 3, "L": 7}}],
}
QUOTA = {a["name"]: a["quota"] for a in TURING["arms"]}


def simulate(cfg, n, seed):
    rng = random.Random(seed)
    A, I, IV, draws = {}, {}, {}, []
    for _ in range(n):
        arm, draw = banks.allocate(cfg, A, I, IV, rng)
        draws.append((arm, draw))
        if arm is not None:
            A[arm] = A.get(arm, 0) + 1
        for d in draw:
            I[d["item"]] = I.get(d["item"], 0) + 1
            IV[(d["item"], d["version"])] = IV.get((d["item"], d["version"]), 0) + 1
    return A, I, IV, draws


print("\n--- allocation, simulated (Turing-test configuration, 300 participants) ---")
cfg = banks.normalise(TURING)
for seed in (1, 2, 3):
    A, I, IV, draws = simulate(cfg, 300, seed)
    quota_ok = all(dict(collections.Counter(d["version"] for d in draw)) == QUOTA[arm]
                   for arm, draw in draws)
    distinct = all(len({d["item"] for d in draw}) == 10 for _, draw in draws)
    gaps = [abs(IV.get((it["item"], "H"), 0) - IV.get((it["item"], "L"), 0)) for it in cfg["items"]]
    ok(quota_ok, f"seed {seed}: every draw meets its arm's quota exactly")
    ok(distinct, f"seed {seed}: no item twice in a draw")
    ok(max(A.values()) - min(A.values()) <= 1, f"seed {seed}: arms within 1 ({sorted(A.values())})")
    ok(max(I.values()) - min(I.values()) <= 1, f"seed {seed}: exposure within 1 ({min(I.values())}-{max(I.values())})")
    ok(max(gaps) <= 2, f"seed {seed}: item x version gap at most 2 (max {max(gaps)}, "
                       f"distribution {dict(sorted(collections.Counter(gaps).items()))})")

_, _, _, d1 = simulate(cfg, 5, 1)
_, _, _, d2 = simulate(cfg, 5, 2)
ok([d for _, d in d1] != [d for _, d in d2], "different seeds give different draws (random tie-breaking)")

print("\n--- allocation, unequal version sets and no arms ---")
uneven = banks.normalise({
    "name": "uneven", "show_count": 4, "order": "fixed",
    "items": [{"item": f"V{i}", "versions": ({"a": f"V{i}_a", "b": f"V{i}_b", "c": f"V{i}_c"}
                                              if i % 2 else {"a": f"V{i}_a", "b": f"V{i}_b"})}
              for i in range(12)]})
A, I, IV, draws = simulate(uneven, 120, 7)
ok(all(len(d) == 4 for _, d in draws), "no arms: every draw has show_count items")
ok(max(I.values()) - min(I.values()) <= 1, "no arms: exposure within 1")
three = [it for it in uneven["items"] if len(it["versions"]) == 3]
spread = max(max(IV.get((it["item"], v), 0) for v in it["versions"])
             - min(IV.get((it["item"], v), 0) for v in it["versions"]) for it in three)
ok(spread <= 1, f"no arms: each item spreads over its own versions (max gap {spread})")
order_pages = [d["page"] for d in draws[0][1]]
ok(order_pages == sorted(order_pages, key=lambda p: banks.bank_pages(uneven).index(p)),
   "order 'fixed' keeps schema order")

partial = banks.normalise({
    "name": "partial", "show_count": 4, "arm_var": "arm",
    "arms": [{"name": "x", "quota": {"a": 2, "b": 2}}],
    "items": [{"item": "p1", "versions": {"a": "p1a"}}, {"item": "p2", "versions": {"a": "p2a"}},
              {"item": "p3", "versions": {"a": "p3a", "b": "p3b"}},
              {"item": "p4", "versions": {"a": "p4a", "b": "p4b"}},
              {"item": "p5", "versions": {"b": "p5b"}}]})
_, _, _, draws = simulate(partial, 30, 3)
ok(all(collections.Counter(d["version"] for d in draw) == {"a": 2, "b": 2} for _, draw in draws),
   "items lacking a version still yield the quota, without crashing")

print("\n--- hungarian ---")
col = banks.hungarian([[4, 1, 3], [2, 0, 5], [3, 2, 2]])
ok(sum([[4, 1, 3], [2, 0, 5], [3, 2, 2]][i][col[i]] for i in range(3)) == 5,
   "minimum-cost assignment found")

print("\n--- validation ---")
pages = ["consent"] + [p for it in TURING["items"] for p in it["versions"].values()] + ["debrief"]
schema = {"pages": [{"name": n, "elements": [{"type": "rating", "name": f"{n}_quality"}]}
                    for n in pages]}
errs, warns = flow.bank_findings(schema, [], [cfg], 0)
ok(not errs and not warns, f"the Turing configuration is clean ({errs + warns})")


def errors_for(**over):
    c = banks.normalise({**TURING, **over})
    return flow.bank_findings(schema, [], [c], 0)[0]


ok(any("sums to" in e for e in errors_for(arms=[{"name": "x", "quota": {"H": 6, "L": 3}}])),
   "a quota that does not sum to show_count is an error")
ok(any("no item has" in e for e in errors_for(arms=[{"name": "x", "quota": {"H": 7, "Z": 3}}])),
   "a quota naming an unknown version is an error")
ok(any("arm_var" in e for e in errors_for(arm_var="")), "arms without arm_var is an error")
ok(any("question name" in e for e in errors_for(arm_var="consent_quality")),
   "arm_var colliding with a question is an error")
ok(any("not in the schema" in e for e in errors_for(
    items=TURING["items"] + [{"item": "X", "versions": {"H": "ghost"}}])), "a missing page is an error")
ok(any("larger than" in e for e in errors_for(show_count=81)), "show_count above the item count is an error")
pool = [{"pool_name": "order", "pool_pages": ["P001_H", "consent"], "condition_var": "vorder"}]
ok(any("already belongs to pool" in e for e in flow.bank_findings(schema, pool, [cfg], 0)[0]),
   "a page in a pool and a bank is an error")
twin = banks.normalise({**TURING, "name": "twin", "arm_var": "arm2"})
ok(any("already belongs to bank" in e for e in flow.bank_findings(schema, [], [cfg, twin], 1)[0]),
   "a page in two banks is an error")
bad_names = {"pages": [{"name": n, "visibleIf": "{x} = 1" if n == "P001_H" else None,
                        "elements": [{"type": "rating", "name": "q"}]} for n in pages]}
w = flow.bank_findings(bad_names, [], [cfg], 0)[1]
ok(any("visibleIf" in m for m in w), "a bank page with a visibleIf is a warning")
ok(any("long export" in m for m in w), "a question not named <page>_<role> is a warning")
ok(banks.pool_combinations(160, 10) > banks.POOL_COMBINATION_CAP
   and banks.pool_combinations(4, 2) == 6, "pool combination count")

items, unmatched = banks.propose_items(["consent", "P001_H", "P001_L", "P002_H", "P002_L", "debrief"])
ok(len(items) == 2 and items[0] == {"item": "P001", "versions": {"H": "P001_H", "L": "P001_L"}}
   and unmatched == ["consent", "debrief"], "the naming helper reads items off page names")

print("\n--- preview ---")
arms = flow.preview(schema, [], {}, bank_cfgs=[cfg])
ok(len(arms) == 3 and all(a.get("example_draw") for a in arms), "one example draw per arm, marked")
walk = [p["page"] for p in arms[0]["pages"]]
ok(walk[0] == "consent" and walk[-1] == "debrief" and len(walk) == 12,
   f"preview shows common pages plus the 10 drawn ({len(walk)} pages)")
ok(arms[0]["condition"].get("arm") in QUOTA, "the arm variable appears in the condition")

with TestClient(main.app) as client:
    print("\n--- page load, submit, export ---")
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    full_schema = {"pages": [{"name": "consent", "elements": [{"type": "boolean", "name": "ok"}]}]
                   + [{"name": n, "elements": [{"type": "rating", "name": f"{n}_quality"},
                                               {"type": "radiogroup", "name": f"{n}_prov",
                                                "choices": ["human", "llm"]}]}
                      for it in TURING["items"] for n in it["versions"].values()]
                   + [{"name": "debrief", "elements": [{"type": "comment", "name": "cues"}]}]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("tt", "Turing", json.dumps(full_schema), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='tt'").fetchone()["id"]
    db.commit(); db.close()

    main.auth.current_user = lambda request, db: db.execute(
        "SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    print("\n--- admin ---")
    r = client.post("/admin/surveys/tt/randomization/add-bank", follow_redirects=False)
    db = main.get_db()
    bid = db.execute("SELECT id FROM item_banks WHERE survey_id=?", (sid,)).fetchone()["id"]
    db.close()
    ok(r.status_code == 302, "an empty bank can be added")
    body = client.get("/s/tt").text
    ok("var assigned_pages  = null" in body, "an empty bank is inert: the page loads, nothing drawn")

    def save(**over):
        form = {"bank_name": "passages", "show_count": "10", "arm_var": "arm", "order": "random",
                "items_json": json.dumps(TURING["items"]), "arms_json": json.dumps(TURING["arms"])}
        form.update(over)
        return client.post(f"/admin/surveys/tt/randomization/bank/{bid}/save", data=form,
                           follow_redirects=False)

    r = save(arms_json=json.dumps([{"name": "x", "quota": {"H": 6, "L": 3}}]))
    ok(r.status_code == 400 and "sums to 9" in r.text and "Nothing was saved" in r.text,
       "a bad quota is refused with the reason")
    ok("&#34;H&#34;: 6" in r.text or '"H": 6' in r.text, "the refused page keeps what was typed")
    r = save(items_json="[{oops")
    ok(r.status_code == 400 and "not valid JSON" in r.text, "broken JSON is refused with where it broke")
    r = save()
    ok(r.status_code == 302, "a valid bank saves")
    page = client.get("/admin/surveys/tt/randomization").text
    ok("Item banks" in page and 'id="bank-help"' in page and "Propose from page names" in page,
       "the randomization page shows the bank section, its help and the helper")

    seen_arms = collections.Counter()
    draw_ids = []
    for n in range(6):
        body = client.get("/s/tt").text
        assigned = json.loads(body.split("var assigned_pages  = ")[1].split(";\n")[0])
        pooled = json.loads(body.split("var pool_pages      = ")[1].split(";\n")[0])
        conds = json.loads(body.split("var conditions      = ")[1].split(";\n")[0])
        orders = json.loads(body.split("var page_orders     = ")[1].split(";\n")[0])
        ids = json.loads(body.split("var assignment_ids  = ")[1].split(";\n")[0]) \
            if "var assignment_ids  = " in body else None
        seen_arms[conds.get("arm")] += 1
        if n == 0:
            ok(len(assigned) == 10 and len(pooled) == 160, "10 versions drawn, all 160 pooled (the rest hidden)")
            ok(orders[-1]["pages"] == assigned, "the draw order travels as a page order")
            items_drawn = {p.rsplit("_", 1)[0] for p in assigned}
            ok(len(items_drawn) == 10, "never two versions of one item")
            v = collections.Counter(p.rsplit("_", 1)[1] for p in assigned)
            ok(dict(v) == QUOTA[conds["arm"]], f"the quota of arm {conds['arm']} is met")
        draw_ids.append((assigned, conds, ids))
    ok(len(seen_arms) == 3, f"arms rotate across arrivals ({dict(seen_arms)})")

    if draw_ids[0][2] is None:
        body = client.get("/s/tt").text
    assigned, conds, ids = draw_ids[0]
    if ids is None:
        import re as _re
        ids = json.loads(_re.search(r"var assignment_ids\s*=\s*(\[.*?\]);", body).group(1))
    bank_ids = [i for i in ids if str(i).startswith("b")]
    ok(len(bank_ids) == 1, "the draw id travels among the assignment ids as b<id>")
    answers = {f"{p}_quality": 5 for p in assigned}
    answers.update({f"{p}_prov": "llm" for p in assigned})
    forged = {"passages": {"arm": "FORGED", "items": ["P999"]}}
    r = client.post("/s/tt/submit", json={**answers, "ok": True, "_assignment_ids": ids,
                                           "_banks": forged})
    ok(r.status_code == 200, "submit accepted")
    db = main.get_db()
    stored = json.loads(db.execute("SELECT response_json FROM responses WHERE survey_id=? "
                                   "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()["response_json"])
    done = db.execute("SELECT completed FROM bank_draws WHERE id=?",
                      (int(bank_ids[0][1:]),)).fetchone()["completed"]
    db.close()
    rec = stored.get("_banks", {}).get("passages", {})
    ok(rec.get("pages") == assigned and rec.get("arm") == conds["arm"],
       "the response records what was shown, from the server's ledger")
    ok(rec.get("arm") != "FORGED", "a client-supplied _banks is discarded")
    ok(done == 1, "submit marks the draw completed")

    flat = client.get("/admin/surveys/tt/export.csv").text.splitlines()[0]
    ok("_banks_passages_arm" in flat and "_banks_passages_items" in flat,
       "the flat export carries arm and items as columns")
    long_csv = client.get("/admin/surveys/tt/export-long.csv").text.splitlines()
    head = long_csv[0].split(",")
    ok(head[:8] == ["response", "submitted_at", "bank", "arm", "position", "item", "version", "page"]
       and "quality" in head and "prov" in head, f"the long export lines answers up by role ({head})")
    ok(len(long_csv) == 11, "one long row per drawn item")
    r = client.get("/admin/surveys/tt/export-long.xlsx")
    ok(r.status_code == 200 and r.content[:2] == b"PK", "the long export comes as Excel too")
    manage = client.get("/admin/surveys/tt").text
    ok("export-long.csv" in manage, "the survey page links the long export")

    print("\n--- pending window and concurrency ---")
    db = main.get_db()
    db.execute("UPDATE bank_draws SET issued_at = datetime('now', '-2 hours') WHERE completed = 0")
    db.commit()
    a_load, i_load, _ = banks.loads(db, bid, main.PENDING_ASSIGNMENT_MINUTES)
    db.close()
    ok(sum(a_load.values()) == 1 and sum(i_load.values()) == 10,
       "abandoned draws older than the window stop counting")

    results = []
    def arrive():
        conn = main.get_db()
        conn.execute("PRAGMA busy_timeout = 5000")
        results.append(banks.assign(conn, bid, banks.normalise(TURING), main.PENDING_ASSIGNMENT_MINUTES))
        conn.close()
    threads = [threading.Thread(target=arrive) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    exposure = collections.Counter(d["item"] for r in results for d in r["draw"])
    ok(len(results) == 6 and max(exposure.values()) == 1,
       "six simultaneous arrivals take 60 different items (no two read the same loads)")

    print("\n--- reset, delete, status ---")
    st = banks.status(main.get_db(), bid, banks.normalise(TURING), 60)
    ok(st["completed"] == 1 and st["exposure_max"] == 1, "status counts completed draws only")
    client.post(f"/admin/surveys/tt/randomization/bank/{bid}/reset", follow_redirects=False)
    db = main.get_db()
    ok(db.execute("SELECT COUNT(*) FROM bank_draws WHERE bank_id=?", (bid,)).fetchone()[0] == 0,
       "reset clears the draws")
    db.close()

    print("\n--- MCP ---")
    import mcp_app
    auth.set_caller(owner)
    out = mcp_app.bank_status("tt")
    ok(out["banks"][0]["name"] == "passages" and out["banks"][0]["items"] == 80, "bank_status reports the bank")
    out = mcp_app.set_bank("tt", {**TURING, "arms": [{"name": "x", "quota": {"H": 1}}]}, bank_id=bid)
    ok("errors" in out and any("sums to" in e for e in out["errors"]), "set_bank refuses with reasons")
    out = mcp_app.set_bank("tt", TURING, bank_id=bid)
    ok(out.get("bank_id") == bid and out.get("draws_reset"), "set_bank replaces a bank")
    out = mcp_app.preview_flow("tt")
    ok(len(out["arms"]) == 3 and all(a.get("example_draw") for a in out["arms"]),
       "preview_flow shows an example draw per arm")
    out = mcp_app.validate_survey("tt")
    ok(out["errors"] == 0, f"validate_survey finds nothing wrong ({out['findings'][:2]})")
    out = mcp_app.set_pool("tt", [p for it in TURING["items"] for p in it["versions"].values()],
                           show_count=10)
    ok("error" in out and "combinations" in out["error"], "set_pool refuses a pool above the combination cap")

    print("\n--- pool guard in the admin and at page load ---")
    db = main.get_db()
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("big", "Big", json.dumps(full_schema), owner["id"]))
    big = db.execute("SELECT id FROM surveys WHERE slug='big'").fetchone()["id"]
    many = [p for it in TURING["items"] for p in it["versions"].values()]
    pid = db.execute("INSERT INTO rand_pools (survey_id, pool_name, pool_pages, show_count) "
                     "VALUES (?,?,?,?)", (big, "too big", json.dumps(many), 10)).lastrowid
    db.commit(); db.close()
    r = client.get("/s/big")
    ok(r.status_code == 200, "a pool saved before the guard is skipped at page load, not left to block it")
    r = client.post(f"/admin/surveys/big/randomization/{pid}/save",
                    data={"pool_name": "too big", "pool_pages": many, "show_count": "10"},
                    follow_redirects=False)
    ok(r.status_code == 400 and "combinations" in r.text and "item bank" in r.text,
       "the admin refuses such a pool and points at item banks")

    r = client.post(f"/admin/surveys/tt/randomization/bank/{bid}/delete", follow_redirects=False)
    db = main.get_db()
    gone = db.execute("SELECT COUNT(*) FROM item_banks WHERE id=?", (bid,)).fetchone()[0] == 0
    kept = "_banks" in json.loads(db.execute("SELECT response_json FROM responses WHERE survey_id=?",
                                             (sid,)).fetchone()["response_json"])
    db.close()
    ok(gone and kept, "deleting a bank keeps what responses recorded")

print("\nALL PASS" if not FAILED else f"\n{len(FAILED)} FAILED:\n  " + "\n  ".join(FAILED))
sys.exit(1 if FAILED else 0)
