"""Checks for the MCP surface: the key gate, ownership, and the write guards.

The tools are exercised as plain functions with a caller set, which is what the
gate does before handing a request to them; the gate itself is exercised over
HTTP, because that is the only place it runs.
"""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "survey.db")
os.environ["UPLOADS_PATH"] = os.path.join(tmp, "uploads")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["PUBLIC_URL"] = "http://testserver"
from cryptography.fernet import Fernet
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
import auth, main, mcp_app

client = TestClient(main.app)
FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

CONSENT = "{consent_agree} contains 'agree'"
SCHEMA = {"title": "T", "pages": [
    {"name": "consent", "elements": [
        {"type": "checkbox", "name": "consent_agree", "isRequired": True,
         "choices": [{"value": "agree", "text": "ok"}]}]},
    {"name": "info_a", "elements": []},
    {"name": "info_b", "elements": []},
    {"name": "core", "visibleIf": CONSENT, "elements": [
        {"type": "rating", "name": "q1", "isRequired": True},
        {"type": "dropdown", "name": "q1a", "visibleIf": "{q1} >= 2",
         "choices": [{"value": "yes", "text": "Yes"}]}]},
    {"name": "extra", "visibleIf": CONSENT + " and {condition} = 'B'", "elements": [
        {"type": "rating", "name": "q2", "isRequired": True}]},
]}

with TestClient(main.app):
    db = main.get_db()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("me@t", "Me", auth.hash_password("x")))
    owner = db.execute("SELECT * FROM users WHERE email=?", ("me@t",)).fetchone()
    db.execute("INSERT INTO users (email, name, hashed_password) VALUES (?,?,?)",
               ("other@t", "Other", auth.hash_password("x")))
    other = db.execute("SELECT * FROM users WHERE email='other@t'").fetchone()
    key = auth.new_api_key()
    db.execute("UPDATE users SET mcp_key = ? WHERE id = ?", (key, owner["id"]))
    db.execute("INSERT INTO surveys (slug, title, schema_json, owner_id, active) "
               "VALUES ('theirs','Theirs',?,?,1)", (json.dumps(SCHEMA), other["id"]))
    db.commit(); db.close()

    print("\n--- the key gate ---")
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    ok(r.status_code == 401, f"no key -> 401 (got {r.status_code})")
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": "svy_nonsense"})
    ok(r.status_code == 401, f"bad key -> 401 (got {r.status_code})")
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": key, "Accept": "application/json, text/event-stream"})
    ok(r.status_code == 200, f"good key -> 200 (got {r.status_code})")
    names = {t["name"] for t in r.json().get("result", {}).get("tools", [])}
    ok({"list_surveys", "preview_flow", "validate_survey", "update_schema",
        "set_pool", "get_responses"} <= names, f"tools advertised: {len(names)}")

    r = client.post(f"/mcp/k/{key}/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"Accept": "application/json, text/event-stream"})
    ok(r.status_code == 200, "the key can also travel in the path")

    db = main.get_db()
    used = db.execute("SELECT mcp_key_last_used_at FROM users WHERE id=?",
                      (owner["id"],)).fetchone()
    db.close()
    ok(used["mcp_key_last_used_at"] is not None,
       "a used key is stamped, so a stale one is visible")

    db = main.get_db()
    db.execute("UPDATE users SET mcp_key = NULL WHERE id = ?", (owner["id"],))
    db.commit(); db.close()
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": key})
    ok(r.status_code == 401, "a revoked key stops working immediately")

    db = main.get_db()
    db.execute("UPDATE users SET mcp_key = ? WHERE id = ?", (key, owner["id"]))
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (owner["id"],))
    db.commit(); db.close()
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": key})
    ok(r.status_code == 401, "a disabled account cannot be reached through its key either")
    db = main.get_db(); db.execute("UPDATE users SET is_active = 1 WHERE id = ?",
                                   (owner["id"],))
    db.commit(); db.close()

    db = main.get_db()
    other_key = auth.new_api_key()
    db.execute("UPDATE users SET mcp_key = ? WHERE id = ?", (other_key, other["id"]))
    db.commit(); db.close()
    ok(auth.check_api_key(main.get_db(), other_key)["id"] == other["id"],
       "two users hold two different keys, each resolving to its own owner")

    print("\n--- ownership ---")
    auth.set_caller(owner)
    ok(mcp_app.get_survey("theirs").get("error"),
       "another user's survey reports not found, not forbidden")
    ok("theirs" not in [s["slug"] for s in mcp_app.list_surveys()["surveys"]],
       "and does not appear in the listing")

    print("\n--- create and validate ---")
    res = mcp_app.create_survey("Bad Slug", "T", SCHEMA)
    ok(res.get("error"), "a slug with capitals and spaces is refused")

    dup = json.loads(json.dumps(SCHEMA))
    dup["pages"][3]["elements"].append({"type": "rating", "name": "q1"})
    res = mcp_app.create_survey("dup", "Dup", dup)
    ok(res.get("error") and "q1" in res["error"],
       "a schema with two questions of the same name is refused, with the reason")

    # A condition reading a name nothing defines cannot be judged before the
    # pools exist — the variable it names comes into being with the pool. So it
    # is reported as pending at creation, and becomes an error afterwards.
    pending = json.loads(json.dumps(SCHEMA))
    pending["pages"][3]["elements"][1]["visibleIf"] = "{nowhere} >= 2"
    res = mcp_app.create_survey("pending", "Pending", pending)
    ok(res.get("slug") == "pending"
       and any("nowhere" in f["message"] for f in res["pending"]),
       "an unresolvable reference is created but reported as pending")
    ok(any(f["kind"] == "dangling_reference"
           for f in mcp_app.validate_survey("pending")["findings"]),
       "and validate_survey calls it an error once the survey exists")
    ok(mcp_app.update_schema("pending", pending).get("error"),
       "update_schema refuses it outright — by then the pools have had their chance")

    res = mcp_app.create_survey("mine", "Mine", SCHEMA)
    ok(res.get("slug") == "mine" and res["active"] is False,
       "created, and closed until explicitly opened")
    ok(mcp_app.create_survey("mine", "Again", SCHEMA).get("error"), "slug is taken")

    print("\n--- pools, flow and validation ---")
    res = mcp_app.set_pool("mine", ["info_a", "info_gone"])
    ok(res.get("error") and "info_gone" in res["error"], "a pool page not in the schema is refused")
    res = mcp_app.set_pool("mine", ["info_a", "info_b"], pool_name="Version",
                           condition_var="condition",
                           condition_map={"info_a": "A", "info_b": "B"})
    pool_id = res.get("pool_id")
    ok(pool_id and res["counters_reset"], "pool created, and it says the counters went")

    res = mcp_app.set_pool("mine", ["info_a", "info_b"], condition_var="condition",
                           condition_map={"info_a": "A", "info_b": "B"},
                           page_order={"B": ["core", "nope"]}, pool_id=pool_id)
    ok(res.get("error") and "nope" in res["error"], "a page order naming a missing page is refused")

    arms = mcp_app.preview_flow("mine", answers={"consent_agree": ["agree"]})["arms"]
    walks = {a["condition"]["condition"]: [p["page"] for p in a["pages"]] for a in arms}
    ok(walks["A"] == ["consent", "info_a", "core"], f"arm A walk: {walks['A']}")
    ok(walks["B"] == ["consent", "info_b", "core", "extra"], f"arm B walk: {walks['B']}")
    core = next(p for p in arms[0]["pages"] if p["page"] == "core")
    ok({q["name"]: q["shown"] for q in core["questions"]} == {"q1": "always", "q1a": "conditional"},
       "a question gated on an answer stays marked conditional")

    ok(mcp_app.validate_survey("mine")["errors"] == 0, "the created survey validates clean")

    db = main.get_db()
    sid = db.execute("SELECT id FROM surveys WHERE slug='mine'").fetchone()["id"]
    bad = json.loads(json.dumps(SCHEMA))
    bad["pages"][3]["elements"][1]["choices"] = [{"value": "Item 1", "text": "Yes"}]
    db.execute("UPDATE surveys SET schema_json=? WHERE id=?", (json.dumps(bad), sid))
    db.commit(); db.close()
    kinds = {f["kind"] for f in mcp_app.validate_survey("mine")["findings"]}
    ok("placeholder_choice_value" in kinds, "validation reports placeholder choice values")
    mcp_app.update_schema("mine", SCHEMA)

    print("\n--- write guards ---")
    db = main.get_db()
    db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?, ?)",
               (sid, json.dumps({"q1": 4, "_conditions": {"condition": "A"},
                                 "_timing": {"total_seconds": 120}})))
    db.commit(); db.close()

    res = mcp_app.update_schema("mine", SCHEMA)
    ok(res.get("error") and "1 response" in res["error"],
       "editing a schema that already holds responses is refused by default")
    res = mcp_app.update_schema("mine", SCHEMA, force=True)
    ok(res.get("responses_held") == 1, "force goes through, and says how many were held")

    print("\n--- responses ---")
    res = mcp_app.get_responses("mine")
    ok(res["total"] == 1 and res["responses"][0]["data"]["q1"] == 4,
       "responses come back with their full payload")
    stats = mcp_app.response_stats("mine")
    ok(stats["responses"] == 1 and stats["answered"]["q1"] == 1,
       "stats count answers per question")
    ok("q1a" in stats["never_answered"],
       "a question nobody reached is called out — the quick way to spot a dead branch")

    print("\n--- counters and open/close ---")
    db = main.get_db()
    db.execute("INSERT INTO assignments (pool_id, condition_key, completed) VALUES (?, 'info_a', 1)",
               (pool_id,))
    db.commit(); db.close()
    ok(mcp_app.randomization_status("mine")["pools"][0]["total_completed"] == 1,
       "randomization_status reports the balance")
    ok(mcp_app.reset_counters("mine")["pools_reset"] == [pool_id], "counters reset")
    ok(mcp_app.randomization_status("mine")["pools"][0]["total_completed"] == 0,
       "and the balance is back to zero")
    ok(mcp_app.set_active("mine", True)["active"] is True, "a survey can be opened")
    ok(mcp_app.delete_pool("mine", pool_id)["pools"] == [], "a pool can be removed")
    ok(mcp_app.delete_pool("mine", pool_id).get("error"), "and not twice")

    ok(not hasattr(mcp_app, "delete_survey") and not hasattr(mcp_app, "delete_responses"),
       "deleting surveys and responses is not exposed at all")

    print("\n--- the results surface ---")
    db = main.get_db()
    sid = db.execute("SELECT id FROM surveys WHERE slug='mine'").fetchone()["id"]
    # Seven fives, three fours, two threes: two cells under the threshold, so the
    # public view has something to mask and the owner view something to show.
    for i in range(12):
        db.execute("INSERT INTO responses (survey_id, response_json) VALUES (?,?)",
                   (sid, json.dumps({"consent_agree": ["agree"],
                                     "q1": 5 if i < 7 else (4 if i < 10 else 3),
                                     "_conditions": {"condition": "A" if i % 2 else "B"}})))
    db.commit()
    total = db.execute("SELECT COUNT(*) c FROM responses WHERE survey_id=?",
                       (sid,)).fetchone()["c"]
    db.close()
    main._invalidate_results(sid)

    res = mcp_app.question_summary("mine")
    q1 = next(q for q in res["questions"] if q["name"] == "q1")
    print(f"    [q1] exposed={q1['exposed']} n={q1['n']} missing={q1['missing']} "
          f"cells={[(c['value'], c['n']) for c in q1['cells']]}")
    ok(res["responses"] == total and q1["n"] == 12,
       "the aggregates come back as numbers")
    ok(q1["exposed"] == 12 and total == 13,
       "and the one response that never consented never reached the question")
    ok(q1["exposed"] == 12 and q1["missing"] == 0,
       "with the three counts kept apart rather than conflated")

    pub = mcp_app.question_summary("mine", name="q1", audience="public")
    print(f"    [q1 public] {[(c['value'], c['n']) for c in pub['questions'][0]['cells']]}")
    ok(any(c.get("suppressed") for c in pub["questions"][0]["cells"]),
       "asking as the public shows what the public would be served")
    ok(mcp_app.question_summary("mine", audience="everybody").get("error"),
       "an audience nobody defined is refused")
    ok(mcp_app.question_summary("mine", name="nope").get("error"),
       "so is a question the schema does not have")
    ok(mcp_app.question_summary("theirs").get("error"),
       "and somebody else's survey stays out of reach")

    split = mcp_app.question_summary("mine", by="condition")
    got = next(q for q in split["questions"] if q["name"] == "q1")
    ok(set(got.get("groups", {})) == {"A", "B"}, "by= splits the aggregate per arm")

    print("\n--- the report, from the conversation ---")
    ok(mcp_app.get_report("mine")["blocks"] == [], "a survey starts with an empty report")
    res = mcp_app.set_report("mine", [{"kind": "all_questions"}], audience="public")
    ok(res.get("error") and "open" in res["error"],
       "publishing while the survey is still collecting is refused, and says why")
    ok(mcp_app.get_report("mine")["audience"] == "owner", "and nothing was stored")

    res = mcp_app.set_report("mine", [
        {"kind": "text", "md": {"en": "## What we found"}},
        {"kind": "all_questions"},
        {"kind": "nonsense"},
    ], audience="public", publish_on_open=True)
    print(f"    [stored] {[b['kind'] for b in res['blocks']]} dropped={res['dropped']}")
    ok([b["kind"] for b in res["blocks"]] == ["text", "all_questions"],
       "forced, it stores the blocks it understands")
    ok(res["dropped"] == 1, "and says how many it threw away")
    ok(res["url"] == "/s/mine/results", "handing back the link it just created")
    ok(mcp_app.get_report("mine")["audience"] == "public", "the report is published")

    print("\n--- a setting that does not unset itself ---")
    res = mcp_app.set_report("mine", [{"kind": "all_questions"}], audience="public",
                             publish_on_open=True, explore=True)
    ok(res["explore"] is True, "readers can be let in from here too")
    res = mcp_app.set_report("mine", [{"kind": "all_questions"}], audience="public",
                             publish_on_open=True)
    ok(res["explore"] is True,
       "and a later write that says nothing about it leaves it alone, rather than "
       "switching off what somebody turned on in the editor")
    res = mcp_app.set_report("mine", [{"kind": "all_questions"}], audience="public",
                             publish_on_open=True, explore=False)
    ok(res["explore"] is False and mcp_app.get_report("mine")["explore"] is False,
       "saying so explicitly turns it off")

    mcp_app.set_active("mine", False)
    res = mcp_app.set_report("mine", [{"kind": "all_questions"}], audience="public")
    ok(not res.get("error"), "on a closed survey publishing needs no override")
    mcp_app.set_active("mine", True)

    print("\n--- key management in the admin ---")
    t = main.templates.get_template("admin.html")
    class _Req:
        query_params = {}
    holder = {"name": "Me", "is_admin": 0, "mcp_key": "svy_visible",
              "mcp_key_created_at": "now", "mcp_key_last_used_at": None}
    html = t.render(request=_Req(), user=holder, surveys=[],
                    public_url="https://survey.example")
    ok("svy_visible" in html, "the key is shown so it can be copied")
    ok("https://survey.example/mcp" in html, "the endpoint is spelled out for the client")
    ok('action="/admin/mcp-key"' in html and "Regenerate" in html,
       "and it can be regenerated or revoked")

    empty = t.render(request=_Req(), user={"name": "Me", "is_admin": 0, "mcp_key": None},
                     surveys=[], public_url="https://survey.example")
    ok("No key yet" in empty and 'action="/admin/mcp-key"' in empty,
       "a user without a key is offered one")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
