"""Aggregation of collected answers, one shape per question type.

This is the only module allowed to decide what a question type means once the
answers are in. The report renderer, the MCP surface and anything later all read
its output; none of them looks at `el["type"]` themselves. There are already two
per-type dispatches in this codebase (SurveyJS draws them, review_export.py
describes them) and a third one that could drift from the other two is how a
type gets forgotten.

Pure module: no FastAPI, no database. The caller hands in the parsed schema, the
pool rows and the parsed responses.

Two things every aggregate carries, because a percentage without them misleads:
`exposed` (how many people reached the question at all, after randomization and
branching) and `missing` (how many reached it and left it blank). A conditional
question answered by three people out of two hundred is not a 100% anything.

Labels are deliberately absent. The aggregate holds stored *values*, so the same
numbers render in any language the schema carries, and the cache does not need a
copy per locale.
"""

import math
import statistics
from collections import Counter, defaultdict

import flow

# Audiences, widest first. `for_audience()` is what actually removes things, so
# that a template bug cannot publish what a level is not entitled to.
OWNER, RESPONDENT, PUBLIC = "owner", "respondent", "public"
AUDIENCES = (OWNER, RESPONDENT, PUBLIC)

# Below this many responses in a cell, anything but the owner sees "<5".
THRESHOLD = 5

# Metadata the platform adds to every response. None of it is a question, and
# the last three never reach a rendered page at any level.
META_PREFIX = "_"
NEVER_RENDERED = ("_panel_token", "_assignment", "_assignment_ids")


# --- reading a response ---

def answers(data: dict) -> dict:
    """The participant's answers, without the platform's own fields."""
    return {k: v for k, v in data.items() if not k.startswith(META_PREFIX)}


def _answered(value) -> bool:
    """SurveyJS omits unanswered questions, but an empty string or an empty list
    can arrive from a cleared field, and counting those as answers inflates n."""
    if value is None or value == "" or value == [] or value == {}:
        return False
    return True


def _every_question_name(schema: dict) -> list:
    """Every name a condition could read, including questions nested in panels."""
    names = []

    def walk(elements):
        for el in elements or []:
            if el.get("type") == "html":
                continue
            if el.get("name"):
                names.append(el["name"])
            walk(el.get("elements") or el.get("templateElements"))

    for page in schema.get("pages", []):
        walk(page.get("elements"))
    return names


def values_of(schema: dict, data: dict) -> dict:
    """The values a condition is evaluated against for one submitted response.

    Every question in the schema starts empty and is then overwritten by what
    was actually answered. This matters: a response is complete by definition,
    so a question missing from it was left blank, and `{ruolo} = 'c'` on a blank
    `ruolo` is false the way SurveyJS made it false on the day. Evaluating
    against the bare payload instead leaves the reference unresolved, and an
    unresolved condition counts somebody as having reached a question that was
    never on their screen — which shows up as a skip they never made.
    """
    values = {name: "" for name in _every_question_name(schema)}
    values.update(answers(data))
    values.update(data.get("_conditions") or {})
    return values


def pages_shown(schema: dict, pools, data: dict) -> set:
    """Page names this respondent actually walked through.

    Two things hide a page: randomization (a pool page that was not assigned)
    and a page-level condition. Without this, a question on an unassigned arm
    counts as skipped by everyone in the other arm, and every conditional page
    looks like mass abandonment.
    """
    values = values_of(schema, data)
    assigned = set(data.get("_assignment") or [])
    pooled = {name for p in pools for name in (p.get("pool_pages") or [])}

    shown = set()
    for page in schema.get("pages", []):
        name = page.get("name")
        if name in pooled and assigned and name not in assigned:
            continue
        if page.get("visibleIf") and flow.evaluate(page["visibleIf"], values) is flow.FALSE:
            continue
        shown.add(name)
    return shown


def elements_of(schema: dict) -> list:
    """(page, element, guards) for everything that holds an answer.

    A `panel` is a box, not a question: it stores nothing itself and its
    children store their answers at the top level, exactly as if the box were
    not there. So the box is walked through and its children are listed, with
    its own `visibleIf` carried down as a guard — hiding a panel hides what is
    inside it, and a child counted as exposed when its panel was not would
    report skips nobody made.

    A `paneldynamic` is not walked: it holds a list of rows, and `_repeating`
    aggregates the whole thing.
    """
    out = []

    def walk(page_name, elements, guards):
        for el in elements or []:
            kind = el.get("type")
            if kind == "html":
                continue
            if kind == "panel":
                inner = guards + ((el["visibleIf"],) if el.get("visibleIf") else ())
                walk(page_name, el.get("elements"), inner)
                continue
            out.append((page_name, el, guards))

    for page in schema.get("pages", []):
        walk(page.get("name"), page.get("elements"), ())
    return out


def _exposed_to(el: dict, page_name: str, shown: set, values: dict,
                guards: tuple = ()) -> bool:
    if page_name not in shown:
        return False
    for cond in tuple(guards) + ((el["visibleIf"],) if el.get("visibleIf") else ()):
        if cond and flow.evaluate(cond, values) is flow.FALSE:
            return False
    return True


# --- per-type aggregation ---

def _choice_values(el: dict) -> list:
    out = []
    for c in el.get("choices") or []:
        out.append(c.get("value") if isinstance(c, dict) else c)
    return out


def _counts(values, order=()) -> list:
    """Counted cells, options that exist in the schema first and in schema order,
    then anything stored that the schema no longer offers. A value nobody can
    choose any more is still in the data, and dropping it would quietly change
    the total."""
    tally = Counter(values)
    cells = [{"value": v, "n": tally.pop(v, 0)} for v in order]
    cells += [{"value": v, "n": n} for v, n in tally.items()]
    return cells


def _summary(nums) -> dict:
    nums = sorted(n for n in nums if isinstance(n, (int, float)) and not isinstance(n, bool))
    if not nums:
        return {"n": 0}
    out = {
        "n": len(nums), "min": nums[0], "max": nums[-1],
        "mean": round(statistics.fmean(nums), 3),
        "median": statistics.median(nums),
    }
    if len(nums) > 1:
        out["sd"] = round(statistics.stdev(nums), 3)
        out["q1"] = round(statistics.quantiles(nums, n=4)[0], 3)
        out["q3"] = round(statistics.quantiles(nums, n=4)[2], 3)
    return out


def _bins(nums, limit=12) -> list:
    """Binned counts, so a level that may not see the raw numbers still gets a
    distribution. Few distinct values are reported exactly rather than binned:
    an age asked in decades should not be smeared across twelve bins."""
    nums = [n for n in nums if isinstance(n, (int, float)) and not isinstance(n, bool)]
    if not nums:
        return []
    distinct = sorted(set(nums))
    if len(distinct) <= limit:
        tally = Counter(nums)
        return [{"from": v, "to": v, "n": tally[v]} for v in distinct]
    lo, hi = distinct[0], distinct[-1]
    width = (hi - lo) / limit
    edges = [lo + width * i for i in range(limit + 1)]
    out = []
    for i in range(limit):
        a, b = edges[i], edges[i + 1]
        last = i == limit - 1
        n = sum(1 for v in nums if (a <= v <= b) if last or v < b)
        out.append({"from": round(a, 3), "to": round(b, 3), "n": n})
    return out


def _other_split(el: dict, name: str, rows: list) -> dict:
    """`showOtherItem` is two fields: the value "other", and the free text in a
    separate `<name>-Comment`. Kept together here so a chart does not show a
    mute "other" slice, and kept apart from the counts because that text is an
    open answer and obeys the disclosure rules."""
    if not el.get("showOtherItem"):
        return {}
    texts = [d.get(f"{name}-Comment") for d in rows]
    return {"other_texts": [t for t in texts if _answered(t)]}


def _categorical(el, name, values, rows):
    return {"shape": "categorical",
            "cells": _counts(values, _choice_values(el)),
            **_other_split(el, name, rows)}


def _multi(el, name, values, rows):
    """Multi-select. The denominator is respondents, never selections: the
    percentages sum past 100 and every reader takes them for shares of a whole
    unless the number they are shares of is the number of people."""
    flat = [v for answer in values for v in (answer if isinstance(answer, list) else [answer])]
    return {"shape": "multi", "respondents": len(values),
            "selections": len(flat),
            "cells": _counts(flat, _choice_values(el)),
            **_other_split(el, name, rows)}


def _binary(el, name, values, rows):
    return {"shape": "binary",
            "cells": [{"value": True, "n": sum(1 for v in values if v is True)},
                      {"value": False, "n": sum(1 for v in values if v is False)}]}


def _scale(el, name, values, rows):
    order = [v.get("value") if isinstance(v, dict) else v for v in el.get("rateValues") or []]
    if not order:
        order = list(range(el.get("rateMin", 1), el.get("rateMax", 5) + 1))
    return {"shape": "scale", "cells": _counts(values, order), "summary": _summary(values)}


def _grid(el, name, values, rows):
    """Plain matrix: one scale, many statements. A distribution per row."""
    columns = [c.get("value") if isinstance(c, dict) else c for c in el.get("columns") or []]
    per_row = defaultdict(list)
    for answer in values:
        if isinstance(answer, dict):
            for row, col in answer.items():
                per_row[row].append(col)
    order = [r.get("value") if isinstance(r, dict) else r for r in el.get("rows") or []]
    for row in order:
        per_row.setdefault(row, [])
    return {"shape": "grid", "columns": columns,
            "rows": [{"value": r, "n": len(per_row[r]), "cells": _counts(per_row[r], columns)}
                     for r in order] +
                    [{"value": r, "n": len(v), "cells": _counts(v, columns)}
                     for r, v in per_row.items() if r not in order]}


def _cell_type(el, col) -> str:
    default = el.get("cellType", "dropdown")
    return col.get("cellType", default) if isinstance(col, dict) else default


def _cell_choices(el, col) -> list:
    own = col.get("choices") if isinstance(col, dict) else None
    if own:
        return [c.get("value") if isinstance(c, dict) else c for c in own]
    return _choice_values(el)


def _grid_of_questions(el, name, values, rows):
    """matrixdropdown: every column is a question of its own, asked about every
    row. Aggregated per column so the rows can be compared, which is the only
    reason to have used this type instead of separate questions."""
    row_order = [r.get("value") if isinstance(r, dict) else r for r in el.get("rows") or []]
    out = []
    for col in el.get("columns") or []:
        cname = col.get("name") if isinstance(col, dict) else col
        kind = _cell_type(el, col)
        choices = _cell_choices(el, col)
        per_row = defaultdict(list)
        for answer in values:
            if not isinstance(answer, dict):
                continue
            for row, cells in answer.items():
                if isinstance(cells, dict) and _answered(cells.get(cname)):
                    per_row[row].append(cells[cname])
        numeric = kind == "rating"
        out.append({
            "column": cname, "cell_type": kind,
            "rows": [{"value": r, "n": len(per_row.get(r, [])),
                      "cells": None if numeric else _counts(per_row.get(r, []), choices),
                      "summary": _summary(per_row.get(r, [])) if numeric else None}
                     for r in row_order or sorted(per_row)],
        })
    return {"shape": "grid_of_questions", "columns": out}


def _repeating(el, name, values, rows):
    """matrixdynamic and paneldynamic: the respondent decides how many rows.
    How many they chose to add is itself a finding, so it is counted."""
    columns = el.get("columns") or el.get("templateElements") or []
    sizes = [len(a) for a in values if isinstance(a, list)]
    out = []
    for col in columns:
        cname = col.get("name") if isinstance(col, dict) else col
        kind = _cell_type(el, col) if el.get("columns") else col.get("type", "text")
        pooled = [entry.get(cname) for a in values if isinstance(a, list)
                  for entry in a if isinstance(entry, dict) and _answered(entry.get(cname))]
        numeric = kind in ("rating", "expression")
        out.append({"column": cname, "cell_type": kind, "n": len(pooled),
                    "cells": None if numeric else _counts(pooled, _cell_choices(el, col)),
                    "summary": _summary(pooled) if numeric else None})
    return {"shape": "repeating", "columns": out,
            "rows_per_respondent": _counts(sizes, sorted(set(sizes)))}


def _ranking(el, name, values, rows):
    """Where each item landed, not just its average. A mean rank alone hides the
    item half the sample puts first and half puts last, which is usually the
    item worth writing about."""
    order = _choice_values(el)
    depth = max((len(a) for a in values if isinstance(a, list)), default=0)
    positions = {v: [0] * depth for v in order}
    totals = defaultdict(list)
    for answer in values:
        if not isinstance(answer, list):
            continue
        for i, item in enumerate(answer):
            if item not in positions:
                positions[item] = [0] * depth
            positions[item][i] += 1
            totals[item].append(i + 1)
    items = [{"value": v, "positions": positions[v],
              "mean_rank": round(statistics.fmean(totals[v]), 3) if totals.get(v) else None,
              "n": len(totals.get(v, []))}
             for v in positions]
    items.sort(key=lambda i: (i["mean_rank"] is None, i["mean_rank"]))
    return {"shape": "ranking", "depth": depth, "items": items}


def _open(el, name, values, rows):
    lengths = [len(str(v)) for v in values]
    return {"shape": "open", "texts": [str(v) for v in values],
            "median_length": statistics.median(lengths) if lengths else None}


def _numeric(el, name, values, rows):
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    return {"shape": "numeric", "values": nums, "bins": _bins(nums), "summary": _summary(nums)}


def _temporal(el, name, values, rows):
    return {"shape": "temporal", "cells": _counts([str(v)[:10] for v in values],
                                                  sorted({str(v)[:10] for v in values}))}


def _text(el, name, values, rows):
    kind = el.get("inputType")
    if kind == "number":
        return _numeric(el, name, values, rows)
    if kind in ("date", "datetime-local", "month"):
        return _temporal(el, name, values, rows)
    return _open(el, name, values, rows)


# What each type becomes, without needing any answers. The editor asks this to
# know which charts it may offer for a question that has not been answered yet.
# `test_aggregate.py` checks it against what the aggregators actually return, so
# the two cannot drift apart in silence.
SHAPES = {
    "radiogroup": "categorical", "dropdown": "categorical", "imagepicker": "categorical",
    "checkbox": "multi", "tagbox": "multi",
    "boolean": "binary", "rating": "scale",
    "matrix": "grid", "matrixdropdown": "grid_of_questions",
    "matrixdynamic": "repeating", "paneldynamic": "repeating",
    "ranking": "ranking", "comment": "open", "expression": "numeric",
}

_TEXT_SHAPES = {"number": "numeric", "date": "temporal",
                "datetime-local": "temporal", "month": "temporal"}


def shape_of(el: dict) -> str:
    kind = el.get("type")
    if kind == "text":
        return _TEXT_SHAPES.get(el.get("inputType"), "open")
    return SHAPES.get(kind, "unsupported")


_DISPATCH = {
    "radiogroup": _categorical, "dropdown": _categorical, "imagepicker": _categorical,
    "checkbox": _multi, "tagbox": _multi,
    "boolean": _binary,
    "rating": _scale,
    "matrix": _grid,
    "matrixdropdown": _grid_of_questions,
    "matrixdynamic": _repeating, "paneldynamic": _repeating,
    "ranking": _ranking,
    "comment": _open,
    "text": _text,
    "expression": _numeric,
}


# --- entry points ---

def prepare(schema: dict, pools, responses: list) -> list:
    """(values, shown pages) per response, computed once.

    Which pages somebody saw does not depend on the question being aggregated,
    and working it out again for every question turns a cheap page into a scan
    of the whole table per chart.
    """
    out = []
    for data in responses:
        out.append((values_of(schema, data), pages_shown(schema, pools, data)))
    return out


def question(el: dict, page_name: str, schema: dict, pools, responses: list,
             prepared: list = None, guards: tuple = ()) -> dict:
    """One question's aggregate over `responses` (parsed response dicts)."""
    name = el.get("name")
    kind = el.get("type")
    if prepared is None:
        prepared = prepare(schema, pools, responses)
    exposed, rows, values = 0, [], []
    for vals, shown in prepared:
        if not _exposed_to(el, page_name, shown, vals, guards):
            continue
        exposed += 1
        if _answered(vals.get(name)):
            rows.append(vals)
            values.append(vals[name])

    base = {"name": name, "type": kind, "page": page_name,
            "exposed": exposed, "n": len(values), "missing": exposed - len(values)}
    fn = _DISPATCH.get(kind)
    if fn is None:
        # A type nobody taught this module still reports its counts. Silence
        # here would read as a question nobody answered.
        return {**base, "shape": "unsupported"}
    return {**base, **fn(el, name, values, rows)}


def summarise(schema: dict, pools, responses: list, only=None, by: str = None) -> dict:
    """Every question's aggregate, optionally split by a condition variable.

    `only` restricts to a list of question names, in that order, which is what a
    report of hand-picked blocks asks for. `by` groups by the value of a
    condition variable (`_conditions`), which is the owner's view of an
    experiment and goes no further than the owner.
    """
    elements = elements_of(schema)
    index = {el.get("name"): (page, el, guards) for page, el, guards in elements}

    if only is None:
        wanted = list(elements)
    else:
        wanted = []
        for name in only:
            if name in index:
                wanted.append(index[name])
            else:
                # An orphan block: the report names a question the schema no
                # longer has. Reported, never dropped — a silently shorter page
                # is worse than a visible hole.
                wanted.append((None, {"name": name, "type": None, "orphan": True}, ()))

    prepared = prepare(schema, pools, responses)
    groups = defaultdict(list)
    if by:
        for data, row in zip(responses, prepared):
            value = (data.get("_conditions") or {}).get(by)
            if value is not None:
                groups[str(value)].append(row)

    out = []
    for page_name, el, guards in wanted:
        if el.get("orphan"):
            out.append({"name": el["name"], "orphan": True, "shape": "orphan",
                        "exposed": 0, "n": 0, "missing": 0})
            continue
        agg = question(el, page_name, schema, pools, responses, prepared, guards)
        if by:
            agg["by"] = by
            agg["groups"] = {
                k: question(el, page_name, schema, pools, None, rows, guards)
                for k, rows in sorted(groups.items())
            }
        out.append(agg)
    return {"responses": len(responses), "questions": out}


# --- disclosure ---

def _mask_cells(cells, threshold: int = THRESHOLD):
    """Mask small cells, and then mask enough of the rest that the masking holds.

    Hiding a single cell hides nothing: with the total published, one masked
    cell out of `1:0 2:0 3:0 4:? 5:5, n=6` is a subtraction away from being
    read. So the hidden group grows — smallest first — until it holds at least
    two cells summing to at least the threshold, which is the point at which
    the split stops being recoverable. Cells at zero stay visible: they reveal
    nobody and would add nothing to the group.
    """
    cells = [dict(c) for c in (cells or [])]
    hidden = {i for i, c in enumerate(cells) if 0 < (c.get("n") or 0) < threshold}
    if hidden:
        candidates = sorted((i for i, c in enumerate(cells) if c.get("n")),
                            key=lambda i: cells[i]["n"])
        total = sum(cells[i]["n"] for i in hidden)
        for i in candidates:
            if total >= threshold and len(hidden) > 1:
                break
            if i not in hidden:
                hidden.add(i)
                total += cells[i]["n"]
    return [{**c, "n": None, "suppressed": True} if i in hidden else c
            for i, c in enumerate(cells)]


def for_audience(agg: dict, audience: str, threshold: int = THRESHOLD) -> dict:
    """The aggregate as one audience may see it.

    Filtering happens here, on the numbers, rather than in a template: a page
    that forgets a condition then shows less than it should, instead of more.
    """
    if audience == OWNER:
        return agg
    out = {k: v for k, v in agg.items() if k not in ("other_texts", "texts", "values")}

    # Open text never leaves the owner's view. Its counts may.
    if out.get("shape") == "open":
        out["shape"] = "open_counts"

    if out.get("n", 0) < threshold:
        # Not a chart yet. The total is still reported, because a page that says
        # nothing reads as broken rather than as early.
        return {**out, "below_threshold": True,
                **{k: None for k in ("cells", "items", "columns", "rows", "bins", "summary")
                   if k in out}}

    if "cells" in out:
        out["cells"] = _mask_cells(out["cells"])
    if "rows" in out and isinstance(out.get("rows"), list):
        out["rows"] = [{**r, "cells": _mask_cells(r.get("cells"))} for r in out["rows"]]
    if "columns" in out and isinstance(out.get("columns"), list):
        cols = []
        for col in out["columns"]:
            # A plain matrix lists its columns as bare values, a matrix of
            # questions as dicts. Both arrive here under the same key.
            if not isinstance(col, dict):
                cols.append(col)
                continue
            col = dict(col)
            if isinstance(col.get("cells"), list):
                col["cells"] = _mask_cells(col["cells"])
            if isinstance(col.get("rows"), list):
                col["rows"] = [{**r, "cells": _mask_cells(r.get("cells"))} for r in col["rows"]]
            cols.append(col)
        out["columns"] = cols
    # The arm breakdown is the experimental manipulation, and stays with the owner.
    out.pop("groups", None)
    out.pop("by", None)
    return out
