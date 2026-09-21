"""The report: an ordered document of blocks, one per survey.

A report says what a survey's results page contains and how far each part of it
may travel. It holds no numbers — those come from `aggregate.py` at render time
— and no layout, only an order.

Everything that arrives from a browser goes through `normalise()` first. The
editor is JavaScript posting a JSON payload, which means the payload is whatever
the browser felt like sending: unknown block kinds, an audience nobody defined,
a chart type that does not exist for that question. None of it reaches the
database, because a stored report is read later by a page served to strangers.

Pure module: no FastAPI, no database.
"""

import json

import aggregate
from review_export import LANGS, locales_in

# Ordered from the narrowest audience to the widest. A block's audience says how
# far it may reach; the report's says how far anything in it may reach.
RANK = {aggregate.OWNER: 0, aggregate.RESPONDENT: 1, aggregate.PUBLIC: 2}

KINDS = ("text", "question", "all_questions")

# Charts each shape may be drawn as, default first. The editor offers these and
# nothing else, so a saved report cannot ask for a pie of a dynamic matrix.
CHARTS = {
    "categorical": ["bar", "pie", "column"],
    "multi": ["bar", "column"],
    "binary": ["segmented", "pie", "bar"],
    "scale": ["column", "bar", "segmented"],
    "grid": ["stacked", "grouped"],
    "grid_of_questions": ["grouped"],
    "repeating": ["bar"],
    "ranking": ["stacked", "bar"],
    "numeric": ["histogram", "column"],
    "temporal": ["line", "column"],
    "open": ["list"],
    "unsupported": ["table"],
}

VALUES = ("count", "percent")
SORTS = ("schema", "frequency")
OTHERS = ("show", "hide")

EMPTY = {"audience": aggregate.OWNER, "blocks": []}


def _one_of(value, allowed, default):
    return value if value in allowed else default


def _localised(md) -> dict:
    """Markdown per locale. A plain string is read as the default locale, which
    is what a report written before anyone thought about translation looks
    like."""
    if isinstance(md, str):
        return {"default": md}
    if not isinstance(md, dict):
        return {}
    out = {}
    for key, text in md.items():
        if (key == "default" or key in LANGS) and isinstance(text, str) and text.strip():
            out[key] = text
    return out


def parse(raw) -> dict:
    """The stored report, or an empty one. A column that never got written, or
    got written badly, is an empty report rather than an error: the editor has
    to open on something."""
    if not raw:
        return dict(EMPTY, blocks=[])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return dict(EMPTY, blocks=[])
    if not isinstance(raw, dict):
        return dict(EMPTY, blocks=[])
    return {
        "audience": _one_of(raw.get("audience"), RANK, aggregate.OWNER),
        "blocks": [b for b in (raw.get("blocks") or []) if isinstance(b, dict)],
        "updated_at": raw.get("updated_at"),
    }


def normalise(raw, schema: dict) -> dict:
    """A report safe to store: known kinds, known audiences, known options, and
    nothing else carried along."""
    data = parse(raw)
    by_name = {el.get("name"): el for _, el in _elements(schema)}

    blocks = []
    for block in data["blocks"]:
        kind = block.get("kind")
        if kind not in KINDS:
            continue
        out = {"kind": kind}
        audience = block.get("audience")
        if audience in RANK:
            out["audience"] = audience

        if kind == "text":
            md = _localised(block.get("md"))
            if not md:
                continue          # an empty text block is a blank line, not content
            out["md"] = md
        elif kind == "question":
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            out["name"] = name
            el = by_name.get(name)
            # An orphan keeps whatever options it had: the question may come
            # back under the same name, and silently rewriting them would lose
            # work over a typo in a schema edit.
            shape = aggregate.shape_of(el) if el else None
            charts = CHARTS.get(shape or "", [])
            if charts:
                out["chart"] = _one_of(block.get("chart"), charts, charts[0])
            elif block.get("chart"):
                out["chart"] = block["chart"]
            out["value"] = _one_of(block.get("value"), VALUES, "count")
            out["sort"] = _one_of(block.get("sort"), SORTS, "schema")
            out["other"] = _one_of(block.get("other"), OTHERS, "show")
        blocks.append(out)

    return {"audience": data["audience"], "blocks": blocks}


def _elements(schema: dict):
    """Everything a block can point at, panels walked through rather than
    listed: a panel holds no answer of its own, and listing it would offer the
    box while hiding the questions in it."""
    return [(page, el) for page, el, _ in aggregate.elements_of(schema)]


def question_names(schema: dict) -> list:
    return [el.get("name") for _, el in _elements(schema)]


def locales(schema: dict) -> list:
    """The locales a text block should offer: those the questionnaire already
    speaks. A results page in one language under a questionnaire in two is a
    page that half the respondents cannot read.

    A questionnaire written in one language has no locale objects to scan, so
    there is nothing to find and the page would default to English over Italian
    questions. SurveyJS lets a schema declare its own `locale`, and that
    declaration is taken at its word when there is nothing else to go on.
    """
    found = locales_in(schema)
    spoken = [l for l in LANGS if l in found]
    if spoken:
        return spoken
    declared = str(schema.get("locale") or "").strip().lower()
    return [declared] if declared in LANGS else ["en"]


def effective_audience(report: dict, block: dict) -> str:
    """How far this block actually reaches: its own level, capped by the
    report's. Raising the report's ceiling never raises a block that set its
    own lower."""
    block_level = block.get("audience") or report.get("audience", aggregate.OWNER)
    return min(block_level, report.get("audience", aggregate.OWNER), key=lambda a: RANK[a])


def visible_to(report: dict, block: dict, viewer: str) -> bool:
    return RANK[viewer] <= RANK[effective_audience(report, block)]


def resolve(report: dict, schema: dict, viewer: str = aggregate.OWNER) -> list:
    """The blocks to render for one viewer, with `all_questions` expanded.

    Expansion happens here and not at save time on purpose: the block means
    *every question there is*, so a question added to the questionnaire next
    week appears by itself instead of leaving a report that looks complete and
    is not.
    """
    names = question_names(schema)
    out = []
    for block in report.get("blocks", []):
        if not visible_to(report, block, viewer):
            continue
        audience = effective_audience(report, block)
        if block["kind"] == "all_questions":
            for name in names:
                out.append({"kind": "question", "name": name, "audience": audience,
                            "chart": None, "value": "count", "sort": "schema",
                            "other": "show", "from_all": True})
        else:
            out.append({**block, "audience": audience})
    return out


def validate(report: dict, schema: dict) -> list:
    """What is worth telling the person editing. Advisory: refuses nothing."""
    findings = []
    names = set(question_names(schema))
    locs = locales(schema)
    seen = set()

    def add(severity, kind, message, where=None):
        findings.append({"severity": severity, "kind": kind,
                         "message": message, "where": where})

    for i, block in enumerate(report.get("blocks", [])):
        kind = block.get("kind")
        if kind == "question":
            name = block.get("name")
            if name not in names:
                add("error", "orphan_block",
                    f"block {i + 1} shows '{name}', which the questionnaire no longer has",
                    name)
            elif name in seen:
                add("warning", "duplicate_block",
                    f"'{name}' appears more than once in this report", name)
            seen.add(name)
        elif kind == "text":
            missing = [l for l in locs if l not in block.get("md", {})
                       and not (l == "en" and "default" in block.get("md", {}))]
            if missing:
                add("warning", "untranslated_text",
                    f"block {i + 1} has no text in {', '.join(m.upper() for m in missing)}; "
                    f"those readers see the default language", None)

    published = [b for b in report.get("blocks", [])
                 if RANK[effective_audience(report, b)] >= RANK[aggregate.RESPONDENT]]
    if report.get("audience") != aggregate.OWNER and not published:
        add("warning", "nothing_published",
            "this report is shared, but every block in it is owner-only")
    return findings
