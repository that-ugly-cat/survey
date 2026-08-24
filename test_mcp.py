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
    db.execute("INSERT INTO api_keys (user_id, name, key) VALUES (?,?,?)",
               (owner["id"], "test", key))
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
    used = db.execute("SELECT last_used_at FROM api_keys WHERE key=?", (key,)).fetchone()
    db.close()
    ok(used["last_used_at"] is not None, "a used key is stamped, so a stale one is visible")

    db = main.get_db()
    db.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
    db.commit(); db.close()
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": key})
    ok(r.status_code == 401, "a revoked key stops working immediately")
    db = main.get_db(); db.execute("UPDATE api_keys SET active = 1 WHERE key = ?", (key,))
    db.commit(); db.close()

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

    print("\n--- key management in the admin ---")
    t = main.templates.get_template("admin.html")
    class _Req:
        query_params = {}
    html = t.render(request=_Req(), user={"name": "Me", "is_admin": 0}, surveys=[],
                    public_url="https://survey.example",
                    api_keys=[{"id": 1, "name": "Claude Code", "key": "svy_visible",
                               "active": 1, "created_at": "now", "last_used_at": None},
                              {"id": 2, "name": "Old", "key": "svy_hidden",
                               "active": 0, "created_at": "then", "last_used_at": "then"}])
    ok("svy_visible" in html, "an active key is shown so it can be copied")
    ok("svy_hidden" not in html, "a revoked key's secret is not printed back")
    ok("https://survey.example/mcp" in html, "the endpoint is spelled out for the client")
    ok('action="/admin/keys"' in html, "and there is a form to mint one")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
