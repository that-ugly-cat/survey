"""
The model-facing surface of Survey.

A questionnaire is edited far more often than it is filled in, and every edit
raises the same three questions: what does it look like now, what would a
participant in each arm actually walk through, and what did the last change
break. Answering those through the admin UI means clicking; answering them
through the browser means clicking a questionnaire. Both work and both are slow,
and neither can answer for an arm you did not happen to be assigned.

So: read the schema, walk every arm at once, validate, and write back — from
inside the conversation where the work is happening.

Access. Every call runs as the human who owns the API key, and every survey
lookup goes through the same ownership check the web app applies, so the surface
has exactly the reach of its owner. A survey the caller cannot manage reports
"not found" rather than "forbidden", so the model cannot enumerate what it
cannot see.

Writes are guarded rather than free. Editing the schema of a survey that already
holds responses is refused unless the caller says `force`, because the answers
already collected were given to the old wording and silently reinterpreting them
is the one mistake that cannot be undone from a backup. Deleting surveys and
deleting responses are not exposed at all: they stay in the web admin, where a
human is holding the mouse.

Errors are returned as {"error": ...} rather than raised: a message the model can
read lets it correct course, where a stack trace only gives it something to
hallucinate around.
"""
import json
import re

from mcp.server.mcpserver import MCPServer

import auth
import flow
import main

mcp = MCPServer(
    name="survey",
    instructions=(
        "Questionnaires on survey.borant.eu: structure, randomization arms, and "
        "collected responses. Start with list_surveys. preview_flow answers what "
        "a participant in each arm walks through, and validate_survey reports what "
        "is broken — both read the schema, so neither needs the questionnaire to be "
        "open in a browser. Reads are free; confirm with the user before any write, "
        "and note that saving a pool resets its balance counters."
    ),
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _fail(msg: str) -> dict:
    return {"error": msg}


def _db():
    return main.get_db()


def _owned(db, slug: str):
    """The survey row the caller may manage, or None. Deliberately does not
    distinguish absent from forbidden."""
    return main._owned_survey(db, slug, auth.current_caller())


def _pools(db, survey_id: int) -> list:
    rows = db.execute(
        "SELECT id, pool_name, pool_order, pool_pages, show_count, condition_var, "
        "condition_map, page_order FROM rand_pools WHERE survey_id = ? ORDER BY pool_order",
        (survey_id,),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "pool_name": r["pool_name"],
            "pool_pages": json.loads(r["pool_pages"] or "[]"),
            "show_count": r["show_count"],
            "condition_var": r["condition_var"],
            "condition_map": json.loads(r["condition_map"]) if r["condition_map"] else None,
            "page_order": json.loads(r["page_order"]) if r["page_order"] else None,
        })
    return out


def _schema(row) -> dict:
    return json.loads(row["schema_json"])


def _response_count(db, survey_id: int) -> int:
    return db.execute("SELECT COUNT(*) c FROM responses WHERE survey_id = ?",
                      (survey_id,)).fetchone()["c"]


# --- reading ---

@mcp.tool()
def list_surveys() -> dict:
    """Every survey the caller can manage: slug, title, whether it is open, and
    how many responses it holds."""
    db = _db()
    try:
        user = auth.current_caller()
        where = "" if user["is_admin"] else "WHERE s.owner_id = ?"
        args = () if user["is_admin"] else (user["id"],)
        rows = db.execute(f"""
            SELECT s.slug, s.title, s.active, s.created_at,
                   (SELECT COUNT(*) FROM responses r WHERE r.survey_id = s.id) AS responses,
                   (SELECT COUNT(*) FROM rand_pools p WHERE p.survey_id = s.id) AS pools
            FROM surveys s {where} ORDER BY s.created_at DESC""", args).fetchall()
        return {"surveys": [dict(r) for r in rows]}
    finally:
        db.close()


@mcp.tool()
def get_survey(slug: str) -> dict:
    """Everything about one survey except the schema itself: counts, pools, and
    the public URL. Use get_schema for the questionnaire JSON."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        schema = _schema(row)
        return {
            "slug": row["slug"], "title": row["title"], "active": bool(row["active"]),
            "created_at": row["created_at"],
            "responses": _response_count(db, row["id"]),
            "url": f"/s/{row['slug']}",
            "summary": {k: v for k, v in flow.summarise(schema).items()
                        if k != "question_names"},
            "pools": _pools(db, row["id"]),
        }
    finally:
        db.close()


@mcp.tool()
def get_schema(slug: str) -> dict:
    """The questionnaire JSON, exactly as stored."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        return {"slug": slug, "schema": _schema(row)}
    finally:
        db.close()


@mcp.tool()
def preview_flow(slug: str, answers: dict = None) -> dict:
    """What a participant in each randomization arm walks through: the pages in
    the order they are shown, and the questions on each.

    This is computed from the schema, so it covers every arm at once and needs
    nothing rendered. Visibility that depends on an answer cannot be decided in
    advance, so those questions come back marked `conditional` with the
    expression that governs them — pass `answers` to pin values (for example
    {"consent_agree": ["agree"]}, or a rating that opens a follow-up) and see
    what they open.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        return {"slug": slug,
                "arms": flow.preview(_schema(row), _pools(db, row["id"]), answers or {})}
    finally:
        db.close()


@mcp.tool()
def validate_survey(slug: str) -> dict:
    """Structural problems in the schema and its pools, worst first.

    Catches duplicate question names, conditions that read a field nothing
    defines or that is answered later, placeholder choice values that make an
    export unreadable, pool pages that do not exist, and arms that reach no
    questions. Advisory: it refuses nothing and changes nothing.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        findings = flow.validate(_schema(row), _pools(db, row["id"]))
        return {"slug": slug, "findings": findings,
                "errors": sum(1 for f in findings if f["severity"] == "error"),
                "warnings": sum(1 for f in findings if f["severity"] == "warning")}
    finally:
        db.close()


@mcp.tool()
def randomization_status(slug: str) -> dict:
    """Per pool and per arm: responses completed, and assignments issued but not
    yet submitted. The pending figure counts only the last hour, so a burst of
    abandoned starts stops distorting the picture once it ages out."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        out = []
        for pool in _pools(db, row["id"]):
            completed, pending = main._pool_counts(db, pool["id"])
            keys = sorted(set(completed) | set(pending))
            out.append({
                "pool_id": pool["id"], "pool_name": pool["pool_name"],
                "condition_var": pool["condition_var"],
                "arms": [{"key": k,
                          "value": (pool["condition_map"] or {}).get(k, k),
                          "completed": completed.get(k, 0),
                          "pending": pending.get(k, 0)} for k in keys],
                "total_completed": sum(completed.values()),
            })
        return {"slug": slug, "pools": out}
    finally:
        db.close()


@mcp.tool()
def get_responses(slug: str, limit: int = 50, offset: int = 0) -> dict:
    """Collected responses, newest first, with their full payloads.

    Each carries the answers plus the platform's own keys: `_conditions` (the
    randomization arm), `_assignment` (the pool pages drawn) and `_timing` (
    seconds per page). Responses are anonymous; nothing here identifies a
    participant.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        limit = max(1, min(int(limit), 200))
        rows = db.execute(
            "SELECT id, submitted_at, response_json FROM responses WHERE survey_id = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (row["id"], limit, max(0, int(offset))),
        ).fetchall()
        total = _response_count(db, row["id"])
        return {
            "slug": slug, "total": total, "returned": len(rows), "offset": offset,
            "responses": [{"id": r["id"], "submitted_at": r["submitted_at"],
                           "data": json.loads(r["response_json"])} for r in rows],
        }
    finally:
        db.close()


@mcp.tool()
def response_stats(slug: str) -> dict:
    """Shape of the collected data without the payloads: how many responses per
    arm, how long they take, and how often each question was answered.

    The per-question counts are the quick way to see a question nobody reaches:
    a conditional item at zero means its condition never fires.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        rows = db.execute("SELECT response_json FROM responses WHERE survey_id = ?",
                          (row["id"],)).fetchall()
        by_arm, per_question, durations = {}, {}, []
        for r in rows:
            data = json.loads(r["response_json"])
            arm = json.dumps(data.get("_conditions") or {}, sort_keys=True)
            by_arm[arm] = by_arm.get(arm, 0) + 1
            seconds = (data.get("_timing") or {}).get("total_seconds")
            if isinstance(seconds, (int, float)):
                durations.append(seconds)
            for key in data:
                if not key.startswith("_"):
                    per_question[key] = per_question.get(key, 0) + 1
        schema_names = flow.summarise(_schema(row))["question_names"]
        durations.sort()
        return {
            "slug": slug, "responses": len(rows),
            "by_arm": {k: v for k, v in sorted(by_arm.items())},
            "median_seconds": durations[len(durations) // 2] if durations else None,
            "answered": {name: per_question.get(name, 0) for name in schema_names},
            "never_answered": [n for n in schema_names if not per_question.get(n)],
        }
    finally:
        db.close()


# --- writing ---

def _validated(schema, pools, force: bool, ignore=()):
    """(findings, refusal). A schema with structural errors is refused unless the
    caller has said force, because the alternative is fielding it.

    `ignore` exempts kinds that cannot be judged yet. At creation there are no
    pools, so a condition reading `{condition}` is neither right nor wrong — the
    variable it names comes into being when the pool does. Refusing there would
    make a counterbalanced questionnaire impossible to create in the order the
    platform requires.
    """
    findings = flow.validate(schema, pools)
    errors = [f for f in findings
              if f["severity"] == "error" and f["kind"] not in ignore]
    if errors and not force:
        return findings, _fail(
            "schema has structural errors; fix them or pass force=true. "
            + "; ".join(f["message"] for f in errors[:5]))
    return findings, None


@mcp.tool()
def create_survey(slug: str, title: str, schema: dict, active: bool = False) -> dict:
    """Create a survey, owned by the caller. Validates the schema first and
    refuses one with structural errors. Created closed unless `active` is set, so
    a half-built questionnaire is not reachable while it is being finished."""
    if not SLUG_RE.match(slug or ""):
        return _fail("slug must be lowercase letters, digits and hyphens")
    if not isinstance(schema, dict) or not schema.get("pages"):
        return _fail("schema must be an object with a 'pages' array")
    findings, refusal = _validated(schema, [], force=False, ignore={"dangling_reference"})
    if refusal:
        return refusal
    db = _db()
    try:
        if db.execute("SELECT 1 FROM surveys WHERE slug = ?", (slug,)).fetchone():
            return _fail(f"slug '{slug}' is taken")
        db.execute(
            "INSERT INTO surveys (slug, title, schema_json, owner_id, active) VALUES (?,?,?,?,?)",
            (slug, title, json.dumps(schema), auth.current_caller()["id"], 1 if active else 0),
        )
        db.commit()
        return {"slug": slug, "active": bool(active), "url": f"/s/{slug}",
                "pending": [f for f in findings
                            if f["kind"] == "dangling_reference"],
                "warnings": [f for f in findings if f["severity"] == "warning"]}
    finally:
        db.close()


@mcp.tool()
def update_schema(slug: str, schema: dict, force: bool = False) -> dict:
    """Replace the questionnaire JSON.

    Refused when the survey already holds responses, unless `force`: answers
    already collected were given to the old wording, and changing it underneath
    them silently changes what the data means. When you do force it, say so in
    the write-up — nothing in the export records that the instrument moved.
    """
    if not isinstance(schema, dict) or not schema.get("pages"):
        return _fail("schema must be an object with a 'pages' array")
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        held = _response_count(db, row["id"])
        if held and not force:
            return _fail(
                f"'{slug}' already holds {held} response(s); editing the schema would "
                f"change what they mean. Pass force=true if that is intended.")
        findings, refusal = _validated(schema, _pools(db, row["id"]), force)
        if refusal:
            return refusal
        db.execute("UPDATE surveys SET schema_json = ? WHERE id = ?",
                   (json.dumps(schema), row["id"]))
        db.commit()
        return {"slug": slug, "responses_held": held,
                "summary": {k: v for k, v in flow.summarise(schema).items()
                            if k != "question_names"},
                "findings": findings}
    finally:
        db.close()


@mcp.tool()
def set_pool(slug: str, pool_pages: list, pool_name: str = "Pool", show_count: int = 1,
             condition_var: str = None, condition_map: dict = None,
             page_order: dict = None, pool_id: int = None) -> dict:
    """Create a randomization pool, or replace one by id.

    `condition_map` turns the drawn page into a readable condition value; both it
    and `page_order` need show_count = 1 and a condition_var, and are dropped
    otherwise. `page_order` maps a condition value to a sequence of pages, which
    are put back into the slots they collectively occupy — that is how an arm is
    counterbalanced without duplicating pages.

    Saving resets this pool's balance counters, exactly as the web admin does.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        names = [p.get("name") for p in _schema(row).get("pages", [])]
        missing = [p for p in pool_pages if p not in names]
        if missing:
            return _fail(f"these pages are not in the schema: {missing}")
        show = max(1, int(show_count))
        if show > len(pool_pages):
            return _fail(f"cannot show {show} of {len(pool_pages)} pages")
        var = (condition_var or "").strip() or None
        cmap = json.dumps(condition_map) if (condition_map and show == 1 and var) else None
        porder = None
        if page_order and show == 1 and var:
            if not all(isinstance(v, list) for v in page_order.values()):
                return _fail("page_order values must be lists of page names")
            stray = [n for seq in page_order.values() for n in seq if n not in names]
            if stray:
                return _fail(f"page_order names pages not in the schema: {stray}")
            porder = json.dumps(page_order)

        if pool_id is not None:
            owned = db.execute("SELECT id FROM rand_pools WHERE id = ? AND survey_id = ?",
                               (pool_id, row["id"])).fetchone()
            if not owned:
                return _fail(f"no pool {pool_id} on '{slug}'")
            db.execute(
                "UPDATE rand_pools SET pool_name=?, pool_pages=?, show_count=?, "
                "condition_var=?, condition_map=?, page_order=? WHERE id=?",
                (pool_name, json.dumps(pool_pages), show, var, cmap, porder, pool_id))
            db.execute("DELETE FROM assignments WHERE pool_id = ?", (pool_id,))
            db.execute("DELETE FROM assignment_counts WHERE pool_id = ?", (pool_id,))
        else:
            order = db.execute(
                "SELECT COALESCE(MAX(pool_order)+1, 0) FROM rand_pools WHERE survey_id = ?",
                (row["id"],)).fetchone()[0]
            cur = db.execute(
                "INSERT INTO rand_pools (survey_id, pool_name, pool_order, pool_pages, "
                "show_count, condition_var, condition_map, page_order) VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], pool_name, order, json.dumps(pool_pages), show, var, cmap, porder))
            pool_id = cur.lastrowid
        db.commit()
        return {"slug": slug, "pool_id": pool_id, "counters_reset": True,
                "pools": _pools(db, row["id"]),
                "findings": flow.validate(_schema(row), _pools(db, row["id"]))}
    finally:
        db.close()


@mcp.tool()
def delete_pool(slug: str, pool_id: int) -> dict:
    """Remove a randomization pool and its counters. Configuration only — no
    response is touched, and the pages themselves stay in the schema."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        owned = db.execute("SELECT id FROM rand_pools WHERE id = ? AND survey_id = ?",
                           (pool_id, row["id"])).fetchone()
        if not owned:
            return _fail(f"no pool {pool_id} on '{slug}'")
        db.execute("DELETE FROM rand_pools WHERE id = ?", (pool_id,))
        db.commit()
        return {"slug": slug, "deleted": pool_id, "pools": _pools(db, row["id"])}
    finally:
        db.close()


@mcp.tool()
def reset_counters(slug: str, pool_id: int = None) -> dict:
    """Clear balance counters — one pool, or all of them.

    Do this after testing and before fielding, so the arms start level. It
    discards assignment history only; responses are untouched.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        ids = [p["id"] for p in _pools(db, row["id"])
               if pool_id is None or p["id"] == pool_id]
        if pool_id is not None and not ids:
            return _fail(f"no pool {pool_id} on '{slug}'")
        for pid in ids:
            db.execute("DELETE FROM assignments WHERE pool_id = ?", (pid,))
            db.execute("DELETE FROM assignment_counts WHERE pool_id = ?", (pid,))
        db.commit()
        return {"slug": slug, "pools_reset": ids}
    finally:
        db.close()


@mcp.tool()
def set_active(slug: str, active: bool) -> dict:
    """Open or close a survey. A closed survey shows the closed page instead of
    the questionnaire; nothing already collected is affected."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        db.execute("UPDATE surveys SET active = ? WHERE id = ?",
                   (1 if active else 0, row["id"]))
        db.commit()
        return {"slug": slug, "active": bool(active)}
    finally:
        db.close()
