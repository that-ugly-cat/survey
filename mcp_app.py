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

Data. The model never sees a response. No tool returns a payload, an open
answer or the answers split by randomization arm: what the results surface
hands over is what a published page would serve, cells under five masked, plus
the counts the balance needs (how many per arm, how often each question was
answered). Everything a model reads travels to whoever runs it, under
their terms and not this server's, and the participant notice tells people that
individual answers are seen only by the people running the study. The owner has
the export for the rest, and the export does not go through a conversation.

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

import aggregate
import auth
import banks
import flow
import main
import report as report_model

mcp = MCPServer(
    name="survey",
    instructions=(
        "Questionnaires on survey.borant.eu: structure, randomization arms, and "
        "what came back, in aggregate. Start with list_surveys. preview_flow "
        "answers what a participant in each arm walks through, and validate_survey "
        "reports what is broken — both read the schema, so neither needs the "
        "questionnaire to be open in a browser. question_summary gives what people "
        "answered as a published results page would show it, masked below five and "
        "with the denominators kept apart. Individual responses and open answers "
        "are not reachable from here by design: the export is the owner's. Reads "
        "are free; confirm with the user before any write, and note that saving a "
        "pool resets its balance counters, and that publishing a report while the "
        "survey is still collecting anchors whoever reads it before answering."
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


def _bank_cfgs(db, survey_id: int) -> list:
    return [cfg for _, cfg in main._bank_rows(db, survey_id)]


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
                "arms": flow.preview(_schema(row), _pools(db, row["id"]), answers or {},
                                     bank_cfgs=_bank_cfgs(db, row["id"]))}
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
        findings = flow.validate(_schema(row), _pools(db, row["id"]), _bank_cfgs(db, row["id"]))
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
        if held:
            # The web form logs the same line when a person confirms this. Two
            # doors onto the one change a backup does not undo, and only one of
            # them leaving a trace is the asymmetry the guard was added to end.
            # The caller is a sqlite3.Row, which subscripts and has no .get().
            caller = auth.current_caller()
            main.log.warning(
                "schema of survey %r replaced over %d collected response(s) "
                "by user %s <%s> (forced over MCP)",
                slug, held, caller["id"], caller["email"],
            )
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
        n_combos = banks.pool_combinations(len(pool_pages), show)
        if n_combos > banks.POOL_COMBINATION_CAP:
            return _fail(f"{n_combos} combinations: a pool enumerates them all at every page "
                         f"load, and above {banks.POOL_COMBINATION_CAP} that blocks the server. "
                         f"To sample items from a large set, use set_bank.")
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
def set_bank(slug: str, config: dict, bank_id: int = None) -> dict:
    """Create an item bank, or replace one by id: each participant gets `show_count`
    items from it, one version of each, in an order, with the share of versions set
    by the participant's arm.

    config: {"name", "items": [{"item": "P001", "versions": {"H": "P001_H", ...}}],
    "show_count", "arm_var", "arms": [{"name": "LLM30", "quota": {"H": 7, "L": 3}}],
    "order": "random" | "fixed"}. Each version is a page of the schema; a page belongs
    to one bank at most and never to a pool. Each arm's quota sums to show_count.

    Refused with the reasons when it would not work; saved with warnings when it would
    work badly. Saving resets this bank's draws, as saving a pool resets its counters.
    See docs/item-banks-spec.md.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        if bank_id is not None and not db.execute(
                "SELECT 1 FROM item_banks WHERE id = ? AND survey_id = ?",
                (bank_id, row["id"])).fetchone():
            return _fail(f"no bank {bank_id} on '{slug}'")
        try:
            cfg = banks.normalise(config)
        except (TypeError, ValueError, AttributeError) as exc:
            return _fail(f"the configuration could not be read: {exc}")
        errors, warnings = main.bank_save_errors(db, row, bank_id, cfg)
        if errors:
            return {"error": "the bank was not saved", "errors": errors, "warnings": warnings}
        bank_id = main.store_bank(db, row["id"], bank_id, cfg)
        return {"slug": slug, "bank_id": bank_id, "draws_reset": True, "warnings": warnings,
                "example": banks.example_draw(cfg)[:3]}
    finally:
        db.close()


@mcp.tool()
def delete_bank(slug: str, bank_id: int) -> dict:
    """Delete an item bank and its draws. Responses keep the record of what they were
    shown, so the data survive it."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        cur = db.execute("DELETE FROM item_banks WHERE id = ? AND survey_id = ?",
                         (bank_id, row["id"]))
        db.commit()
        if not cur.rowcount:
            return _fail(f"no bank {bank_id} on '{slug}'")
        return {"slug": slug, "deleted": bank_id}
    finally:
        db.close()


@mcp.tool()
def bank_status(slug: str) -> dict:
    """Per item bank: completed draws per arm and in flight, how often each item has
    been shown and in which version, and the largest gap between an item's versions.
    Counts only: no response content."""
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        out = []
        for bank_id, cfg in main._bank_rows(db, row["id"]):
            st = banks.status(db, bank_id, cfg, main.PENDING_ASSIGNMENT_MINUTES)
            out.append({"bank_id": bank_id, "name": cfg["name"], "show_count": cfg["show_count"],
                        "items": len(cfg["items"]), "arm_var": cfg["arm_var"],
                        "arms": [a["name"] for a in cfg["arms"]], **st})
        return {"slug": slug, "banks": out}
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


# --- results ---

def _report_of(row) -> dict:
    return report_model.normalise(row["report_json"], _schema(row))


@mcp.tool()
def question_summary(slug: str, name: str = None, audience: str = "public") -> dict:
    """What people answered, as one typed aggregate per question, at the level a
    published results page would show it.

    This is the same computation the results page draws, handed over as numbers.
    Every aggregate carries three counts that differ under branching and
    randomization and are wrong to conflate: `exposed` (reached the question at
    all), `n` (answered it) and `missing` (reached it and left it blank). A
    percentage quoted without the denominator it belongs to is the error this
    surface exists to make hard.

    `name` restricts to one question. `audience` is "public" or "respondent",
    which today filter alike: cells under five masked, a question under five
    answers reported as below threshold, no open answers, no split by arm. It is
    also how to check what a published report exposes without opening it in
    another browser.

    The owner's view is not offered here. Unmasked cells, open text and the
    breakdown by condition stay on the owner's results page and in the export,
    which are read by a person and do not travel to a model provider.
    """
    if audience == aggregate.OWNER:
        return _fail("the owner's view is not available to a model: open answers, "
                     "unmasked cells and the split by arm stay on the results page "
                     "and in the export. Use audience 'public' or 'respondent'.")
    if audience not in aggregate.AUDIENCES:
        return _fail("audience must be 'public' or 'respondent'")
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        schema = _schema(row)
        if name and name not in report_model.question_names(schema):
            return _fail(f"no question '{name}' in '{slug}'")
        pools = main._pool_rows(db, row["id"])
        responses = [json.loads(r["response_json"]) for r in db.execute(
            "SELECT response_json FROM responses WHERE survey_id = ?", (row["id"],))]
        out = aggregate.summarise(schema, pools, responses,
                                  only=[name] if name else None)
        questions = [aggregate.for_audience(q, audience) for q in out["questions"]]
        return {"slug": slug, "responses": out["responses"],
                "audience": audience, "questions": questions}
    finally:
        db.close()


@mcp.tool()
def get_report(slug: str) -> dict:
    """The results report: which blocks the page carries, in order, and how far
    each of them may travel.

    `all_questions` is one block standing for every question in schema order,
    resolved when the page is drawn rather than when it was saved, so it keeps
    up with the questionnaire. `findings` reports blocks naming a question that
    no longer exists, the same question twice, and text written in only some of
    the languages the questionnaire speaks.
    """
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        schema = _schema(row)
        stored = _report_of(row)
        published = stored["audience"] == aggregate.PUBLIC
        return {
            "slug": slug,
            "audience": stored["audience"],
            "explore": stored["explore"],
            "blocks": stored["blocks"],
            "url": f"/s/{slug}/results" if published else None,
            "findings": report_model.validate(stored, schema),
        }
    finally:
        db.close()


@mcp.tool()
def set_report(slug: str, blocks: list, audience: str = "owner",
               publish_on_open: bool = False, explore: bool = None) -> dict:
    """Replace the results report.

    `blocks` is an ordered list. Three kinds and no others:
      {"kind": "text", "md": {"en": "## Heading", "it": "## Titolo"}}
      {"kind": "question", "name": "…", "chart": "bar", "value": "count"}
      {"kind": "all_questions"}
    Each may carry "audience" to travel less far than the report does; the
    report's own level is the ceiling, and anything unknown — a block kind, an
    audience, a chart a question's shape cannot take — is dropped rather than
    stored, because what is stored here is served to strangers.

    Publishing beyond the owner while the survey is still **open** is refused
    unless `publish_on_open` is set. Results visible during fielding change what
    later respondents answer: whoever sees the distribution before answering is
    anchored by it. That is a decision somebody may legitimately take, and this
    makes them take it rather than discover it. It is written to the application
    log either way.

    `explore` lets readers cross two published questions themselves, which is
    what a citizen-science page wants and what a study about abuse does not.
    Leaving it out keeps whatever is set rather than switching it off.

    Open answers never reach a published page whatever this says, and cells
    under five answers are masked. Use question_summary(audience="public") to
    see what a reader outside would actually get.
    """
    if audience not in report_model.RANK:
        return _fail(f"audience must be one of {', '.join(report_model.RANK)}")
    db = _db()
    try:
        row = _owned(db, slug)
        if not row:
            return _fail(f"no survey '{slug}'")
        schema = _schema(row)
        # `explore` left out means leave it as it is. Defaulting it to false
        # would make any write from here silently switch off something the
        # owner turned on in the editor, which is the worst kind of surprise:
        # a setting that unsets itself when somebody edits something else.
        current = report_model.normalise(row["report_json"], schema)
        wanted = current["explore"] if explore is None else bool(explore)
        clean = report_model.normalise(
            {"audience": audience, "blocks": blocks, "explore": wanted}, schema)
        reaches_out = clean["audience"] != aggregate.OWNER

        if reaches_out and row["active"] and not publish_on_open:
            return _fail(
                f"'{slug}' is open and still collecting. Publishing its results now "
                f"anchors whoever sees them before answering. Pass publish_on_open "
                f"to do it anyway, or close the survey first with set_active."
            )
        if reaches_out:
            main.log.info("report published (%s) on survey %s by user %s via mcp",
                          clean["audience"], slug, auth.current_caller()["id"])
        db.execute("UPDATE surveys SET report_json = ? WHERE id = ?",
                   (json.dumps(clean, ensure_ascii=False), row["id"]))
        db.commit()
        main._invalidate_results(row["id"])
        dropped = len(blocks) - len(clean["blocks"])
        return {
            "slug": slug,
            "audience": clean["audience"],
            "explore": clean["explore"],
            "blocks": clean["blocks"],
            "dropped": dropped,
            "url": f"/s/{slug}/results" if clean["audience"] == aggregate.PUBLIC else None,
            "findings": report_model.validate(clean, schema),
        }
    finally:
        db.close()
