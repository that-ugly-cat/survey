"""Checks for the balance panel (docs/item-banks-ui-spec.md, phase 4): the verdict in
words, one bar per arm, the exposure strip, the largest version gaps, the arm_from case
where arms are not judged, and a rotation's expected shares beside the actual ones; then
the panel on the page, from draws completed in the database."""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import banks
import main
import rotation

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def status(arms, items, completed):
    shown = [sum(v.values()) for v in items.values()]
    gaps = [max(v.values()) - min(v.values()) for v in items.values()]
    return {"completed": completed, "arms": arms,
            "exposure_min": min(shown), "exposure_max": max(shown), "version_imbalance_max": max(gaps),
            "per_item": [{"item": k, "shown": sum(v.values()), "versions": v} for k, v in items.items()]}


print("\n--- verdict ---")
cfg = {"arms": [{"name": "X"}, {"name": "Y"}]}
st = status({"X": {"completed": 10, "in_progress": 2}, "Y": {"completed": 10, "in_progress": 0}},
            {"P1": {"H": 5, "L": 5}, "P2": {"H": 5, "L": 4}, "P3": {"H": 4, "L": 5}}, 20)
v = banks.balance_view(st, cfg)
ok(v["kind"] == "balanced" and v["text"].startswith("Balanced"), "even exposure and small gaps: balanced")
st2 = status(st["arms"], {"P1": {"H": 9, "L": 2}, "P2": {"H": 6, "L": 4}, "P3": {"H": 1, "L": 1}}, 20)
v2 = banks.balance_view(st2, cfg)
ok(v2["kind"] == "drifting" and "favour" in v2["text"], "uneven: drifting, with what the next draws do about it")

print("\n--- bars, strip, gaps ---")
ok([a["name"] for a in v["arms"]] == ["X", "Y"] and v["arms"][0]["pct"] == 50.0, "one bar per arm, share of completed")
ok(v["arms"][0]["done_width"] + v["arms"][0]["live_width"] == 100 and v["arms"][1]["done_width"] < 100,
   "bar widths relative to the largest arm, in progress stacked after completed")
ok([e["item"] for e in v2["exposure"]] == ["P1", "P2", "P3"] and v2["exposure"][0]["height"] == 100, "the strip in item order, scaled to the most shown")
ok([e["low"] for e in v2["exposure"]] == [False, False, True], "items shown less than the rest are marked")
ok([g["item"] for g in v2["gaps"]] == ["P1", "P2"] and v2["gaps"][0]["gap"] == 7, "gaps largest first, zero gaps left out")
many = status(st["arms"], {f"P{i}": {"H": i, "L": 0} for i in range(1, 9)}, 20)
vm = banks.balance_view(many, cfg)
ok(len(vm["gaps"]) == 5 and vm["more_gaps"] == 3, "five gaps shown, and how many more")

print("\n--- arm from the answers ---")
cfg_a = {"arm_from": "form", "arms": [{"name": "F1"}, {"name": "F2"}, {"name": "F3"}]}
st_a = status({"F1": {"completed": 30, "in_progress": 0}, "F2": {"completed": 32, "in_progress": 0},
               "F3": {"completed": 38, "in_progress": 0}},
              {"slot1": {"F1": 30, "F2": 32, "F3": 38}, "anchor": {"A": 100}}, 100)
exp = {"F1": 32.6, "F2": 32.6, "F3": 34.8}
va = banks.balance_view(st_a, cfg_a, exp)
ok(va["kind"] == "answers" and "does not balance" in va["text"], "groups from the answers are not judged")
ok([a["expected"] for a in va["arms"]] == [32.6, 32.6, 34.8] and va["has_expected"], "the rotation's expected shares beside the actual")
ok(not any(a["off"] for a in va["arms"]), "close to expected: no flag")
far = status({"F1": {"completed": 60, "in_progress": 0}, "F2": {"completed": 20, "in_progress": 0},
              "F3": {"completed": 20, "in_progress": 0}}, {"s": {"F1": 60, "F2": 20, "F3": 20}}, 100)
ok(banks.balance_view(far, cfg_a, exp)["arms"][0]["off"], "more than 10 points away with 30+ responses: flagged")
few = status({"F1": {"completed": 6, "in_progress": 0}, "F2": {"completed": 2, "in_progress": 0},
              "F3": {"completed": 2, "in_progress": 0}}, {"s": {"F1": 6, "F2": 2, "F3": 2}}, 10)
ok(not any(a["off"] for a in banks.balance_view(few, cfg_a, exp)["arms"]), "too few responses to flag anything")

with TestClient(main.app) as client:
    db = main.get_db()
    owner = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    schema = {"calculatedValues": [{"name": "form", "includeIntoResult": True,
                                    "expression": rotation.build_expression("day", 1, ["F1", "F2", "F3"])}],
              "pages": [{"name": "code", "elements": [{"type": "text", "name": "day", "inputType": "number", "min": 1, "max": 31}]}]
                       + [{"name": f"s_{f}", "elements": [{"type": "rating", "name": f"s_{f}_x"}]} for f in ("F1", "F2", "F3")]}
    cfg_db = {"name": "Post", "show_count": 1, "arm_var": "fp", "arm_from": "form", "order": "random",
              "items": [{"item": "s", "versions": {f: f"s_{f}" for f in ("F1", "F2", "F3")}}],
              "arms": [{"name": f, "quota": {f: 1}} for f in ("F1", "F2", "F3")]}
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,1)",
               ("bal", "Balance", json.dumps(schema), owner["id"]))
    sid = db.execute("SELECT id FROM surveys WHERE slug='bal'").fetchone()["id"]
    db.execute("INSERT INTO item_banks (survey_id, bank_name, bank_order, config_json) VALUES (?,?,?,?)",
               (sid, "Post", 0, json.dumps(cfg_db)))
    bid = db.execute("SELECT id FROM item_banks WHERE survey_id=?", (sid,)).fetchone()["id"]
    for f, n in (("F1", 3), ("F2", 3), ("F3", 4)):
        for _ in range(n):
            db.execute("INSERT INTO bank_draws (bank_id, arm, draw_json, completed) VALUES (?,?,?,1)",
                       (bid, f, json.dumps([{"item": "s", "version": f, "page": f"s_{f}"}])))
    db.commit(); db.close()
    main.auth.current_user = lambda request, db: db.execute("SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()

    print("\n--- the panel on the page ---")
    page = client.get("/admin/surveys/bal/randomization").text
    ok('class="bank-verdict bv-answers"' in page and "does not balance" in page, "the verdict for a bank whose groups follow the answers")
    ok(page.count('class="bv-arm"') == 3 and "(expected 32.6%)" in page and "(expected 34.8%)" in page,
       "three bars, with the rotation's expected shares")
    ok('aria-label="F3: 4 completed, 0 in progress"' in page, "each bar says its numbers to a screen reader")
    ok('class="bv-strip"' in page and 'title="s: 10"' in page, "the exposure strip, with each item's count")
    ok("Largest gaps between an item's versions (they follow the group sizes)" in page, "gaps explained for groups from the answers")
    ok("Per item" in page, "the full table is still there")

print(f"\n{'ALL PASSED' if not FAILED else f'{len(FAILED)} FAILED'}")
sys.exit(1 if FAILED else 0)
