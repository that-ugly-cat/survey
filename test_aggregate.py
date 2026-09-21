"""Checks for aggregate.py: what each question type turns into once the answers
are in, who is allowed to see it, and whether the denominators are honest.

No database and no server. The fixture is one schema with every shape in it and
eight hand-written responses, so every number below can be counted by hand from
the fixture rather than taken on trust.
"""
import os, sys

sys.path.insert(0, os.path.abspath("."))
import aggregate

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def show(label, value):
    print(f"    [{label}] {value}")
    return value


SCHEMA = {
    "title": "T",
    "pages": [
        {"name": "base", "elements": [
            {"type": "html", "name": "intro", "html": "<p>hi</p>"},
            {"type": "radiogroup", "name": "ruolo", "choices": ["a", "b", "c"]},
            {"type": "checkbox", "name": "tools", "choices": ["x", "y", "z"],
             "showOtherItem": True},
            {"type": "boolean", "name": "flag"},
            {"type": "rating", "name": "score", "rateMin": 1, "rateMax": 5},
            {"type": "text", "name": "age", "inputType": "number"},
            {"type": "text", "name": "when", "inputType": "date"},
            {"type": "text", "name": "nick"},
            {"type": "comment", "name": "note"},
        ]},
        {"name": "grids", "elements": [
            {"type": "matrix", "name": "agree", "columns": [1, 2, 3],
             "rows": ["r1", "r2"]},
            {"type": "matrixdropdown", "name": "cmp",
             "columns": [{"name": "know", "cellType": "radiogroup",
                          "choices": ["si", "no"]},
                         {"name": "rate", "cellType": "rating"},
                         {"name": "nota", "cellType": "comment"}],
             "rows": ["m1", "m2"]},
            {"type": "matrixdynamic", "name": "people", "cellType": "text",
             "columns": [{"name": "pname"},
                         {"name": "prole", "cellType": "dropdown",
                          "choices": ["pi", "an"]}]},
            {"type": "ranking", "name": "rank", "choices": ["k1", "k2", "k3"]},
        ]},
        {"name": "branch", "elements": [
            {"type": "comment", "name": "why", "visibleIf": "{ruolo} = 'c'"},
        ]},
        {"name": "arm_a", "elements": [{"type": "radiogroup", "name": "qa",
                                        "choices": ["s", "n"]}]},
        {"name": "arm_b", "elements": [{"type": "radiogroup", "name": "qb",
                                        "choices": ["s", "n"]}]},
        {"name": "sign", "elements": [{"type": "signaturepad", "name": "firma"}]},
    ],
}

POOLS = [{"pool_name": "P", "pool_pages": ["arm_a", "arm_b"], "show_count": 1,
          "condition_var": "cond", "condition_map": {"arm_a": "A", "arm_b": "B"}}]


def arm(n):
    """Odd responses land on arm A, even ones on arm B."""
    page = "arm_a" if n % 2 else "arm_b"
    return {"_assignment": [page], "_conditions": {"cond": "A" if n % 2 else "B"},
            "_panel_token": f"tok{n}"}


R = [
    {**arm(1), "ruolo": "a", "tools": ["x", "y"], "flag": True, "score": 5,
     "age": 30, "when": "2026-09-01", "nick": "spit", "note": "a longer remark",
     "agree": {"r1": 1, "r2": 3},
     "cmp": {"m1": {"know": "si", "rate": 4, "nota": "un commento libero"},
             "m2": {"know": "no", "rate": 2}},
     "people": [{"pname": "a", "prole": "pi"}, {"pname": "b", "prole": "an"}],
     "rank": ["k1", "k2", "k3"], "qa": "s"},
    {**arm(2), "ruolo": "a", "tools": ["x"], "flag": False, "score": 4,
     "age": 40, "when": "2026-09-01", "note": "ok",
     "agree": {"r1": 2},
     "cmp": {"m1": {"know": "si", "rate": 5}},
     "people": [{"pname": "c", "prole": "pi"}],
     "rank": ["k2", "k1", "k3"], "qb": "n"},
    {**arm(3), "ruolo": "b", "tools": ["other"], "tools-Comment": "paper",
     "flag": True, "score": 4, "age": 35, "when": "2026-09-02",
     "rank": ["k1", "k3", "k2"], "qa": "s"},
    {**arm(4), "ruolo": "c", "score": 3, "why": "nothing works", "qb": "s"},
    {**arm(5), "ruolo": "c", "score": 5, "qa": "n"},
    {**arm(6), "qb": "s"},
    {**arm(7), "ruolo": "a", "qa": "s"},
    {**arm(8), "ruolo": "b", "qb": "n"},
]

BY_NAME = {q["name"]: q for q in aggregate.summarise(SCHEMA, POOLS, R)["questions"]}


def cells(name):
    return {c["value"]: c["n"] for c in BY_NAME[name]["cells"]}


print("\n--- denominators ---")
show("ruolo", {k: BY_NAME["ruolo"][k] for k in ("exposed", "n", "missing")})
ok(BY_NAME["ruolo"]["exposed"] == 8, "everyone reached an unconditional question")
ok(BY_NAME["ruolo"]["n"] == 7 and BY_NAME["ruolo"]["missing"] == 1,
   "one person skipped it, and the skip is counted rather than dropped")

show("why (visibleIf ruolo=c)", {k: BY_NAME["why"][k] for k in ("exposed", "n", "missing")})
ok(BY_NAME["why"]["exposed"] == 2,
   "a branched question is exposed only to those whose answer opens it")
ok(BY_NAME["why"]["n"] == 1 and BY_NAME["why"]["missing"] == 1,
   "and its denominator is those two, not the eight")
ok(BY_NAME["why"]["exposed"] == 2,
   "the person who left 'ruolo' blank never saw it either: on a submitted response a "
   "missing answer is an empty one, not an unknown one")

show("qa / qb", {n: BY_NAME[n]["exposed"] for n in ("qa", "qb")})
ok(BY_NAME["qa"]["exposed"] == 4 and BY_NAME["qb"]["exposed"] == 4,
   "a pool page counts only for the arm it was assigned to")
ok("intro" not in BY_NAME, "an html element is not a question and produces nothing")

PANELLED = {"pages": [{"name": "p", "elements": [
    {"type": "radiogroup", "name": "gate", "choices": ["si", "no"]},
    {"type": "panel", "name": "box", "visibleIf": "{gate} = 'si'", "elements": [
        {"type": "text", "name": "inside"}]},
]}]}
PR = [{"gate": "si", "inside": "a"}, {"gate": "si"}, {"gate": "no", "inside": "leftover"}]
panelled = {q["name"]: q for q in aggregate.summarise(PANELLED, [], PR)["questions"]}
show("panelled", {k: (v["exposed"], v["n"]) for k, v in panelled.items()})
ok("inside" in panelled and "box" not in panelled,
   "a panel is a box, so its children are the questions and the box is not one")
ok(panelled["inside"]["exposed"] == 2,
   "and the panel's own condition guards them: hiding the box hides what is in it")

print("\n--- choices ---")
show("ruolo", cells("ruolo"))
ok(cells("ruolo") == {"a": 3, "b": 2, "c": 2}, "counts per choice")
ok([c["value"] for c in BY_NAME["ruolo"]["cells"]] == ["a", "b", "c"],
   "cells come back in schema order, not by frequency")

t = BY_NAME["tools"]
show("tools", {"respondents": t["respondents"], "selections": t["selections"],
               "cells": cells("tools"), "other": t.get("other_texts")})
ok(t["respondents"] == 3 and t["selections"] == 4,
   "multi-select separates people from selections, so a percentage can pick one")
ok(cells("tools")["x"] == 2 and cells("tools")["other"] == 1, "including the other bucket")
ok(t["other_texts"] == ["paper"], "the free text behind 'other' is kept, and kept apart")

ok(cells("flag") == {True: 2, False: 1}, "boolean counts both sides")

print("\n--- scales and numbers ---")
show("score", {"cells": cells("score"), "summary": BY_NAME["score"]["summary"]})
ok(cells("score") == {1: 0, 2: 0, 3: 1, 4: 2, 5: 2}, "a rating keeps its empty points")
ok(BY_NAME["score"]["summary"]["mean"] == 4.2, "and carries a summary")
show("age", BY_NAME["age"]["summary"])
ok(BY_NAME["age"]["shape"] == "numeric" and BY_NAME["age"]["summary"]["median"] == 35,
   "a numeric text field is numeric, not text")
ok(len(BY_NAME["age"]["bins"]) == 3,
   "three distinct values are reported exactly rather than smeared over twelve bins")
show("when", cells("when"))
ok(BY_NAME["when"]["shape"] == "temporal" and cells("when")["2026-09-01"] == 2,
   "a date field is counted per day")
ok(BY_NAME["nick"]["shape"] == "open" and BY_NAME["note"]["shape"] == "open",
   "short text and long text are both open answers")

print("\n--- grids ---")
rows = {r["value"]: r for r in BY_NAME["agree"]["rows"]}
show("agree", {k: {c["value"]: c["n"] for c in v["cells"]} for k, v in rows.items()})
ok(rows["r1"]["n"] == 2 and rows["r2"]["n"] == 1,
   "each matrix row has its own n: a row left blank is not an answer")

cols = {c["column"]: c for c in BY_NAME["cmp"]["columns"]}
show("cmp/know", {r["value"]: {c["value"]: c["n"] for c in r["cells"]}
                  for r in cols["know"]["rows"]})
show("cmp/rate", {r["value"]: r["summary"] for r in cols["rate"]["rows"]})
ok(cols["know"]["cell_type"] == "radiogroup" and cols["rate"]["cell_type"] == "rating",
   "every column of a matrix of questions keeps its own type")
ok({r["value"]: r["n"] for r in cols["know"]["rows"]} == {"m1": 2, "m2": 1},
   "and its own per-row counts")
ok(cols["rate"]["rows"][0]["summary"]["mean"] == 4.5,
   "a rating column is summarised, not counted as a category")

p = BY_NAME["people"]
show("people", {c["column"]: (c["n"], c["cells"]) for c in p["columns"]})
show("rows per respondent", {c["value"]: c["n"] for c in p["rows_per_respondent"]})
ok({c["column"]: c["n"] for c in p["columns"]} == {"pname": 3, "prole": 3},
   "a dynamic matrix pools every row the respondents added")
ok({c["value"]: c["n"] for c in p["rows_per_respondent"]} == {1: 1, 2: 1},
   "and how many rows each of them chose to add is itself counted")

print("\n--- text typed into a cell is an open answer ---")
# Found on a live page: the free-text column of a dynamic matrix was counted as
# if its values were choices, so what people typed became chart labels and
# table rows on a public page, with only the counts masked.
names_col = next(c for c in BY_NAME["people"]["columns"] if c["column"] == "pname")
show("pname", {k: names_col.get(k) for k in ("cell_type", "open", "n", "cells", "texts")})
ok(names_col.get("open") and names_col["cells"] is None,
   "a text column is not tabulated into categories")
ok(names_col["texts"] == ["a", "b", "c"], "its values are kept as open answers")
# Above the threshold, so the strip itself is what is being tested rather than
# the whole aggregate being withheld for being too small.
BIG = {"name": "people", "type": "matrixdynamic", "shape": "repeating",
       "exposed": 20, "n": 20, "missing": 0,
       "columns": [{"column": "pname", "cell_type": "text", "open": True, "n": 20,
                    "cells": None, "summary": None,
                    "texts": ["Anna", "Ugo", "Nora"]},
                   {"column": "prole", "cell_type": "dropdown", "n": 20,
                    "cells": [{"value": "pi", "n": 12}, {"value": "an", "n": 8}]}],
       "rows_per_respondent": [{"value": 1, "n": 20}]}
for audience in (aggregate.RESPONDENT, aggregate.PUBLIC):
    shown = aggregate.for_audience(BIG, audience)
    leaked = [c for c in (shown.get("columns") or []) if c.get("texts")]
    show(f"columns for {audience}", [(c["column"], sorted(c)) for c in shown["columns"]])
    ok(not leaked, f"and they do not reach {audience}")
ok(aggregate.for_audience(BIG, aggregate.OWNER)["columns"][0]["texts"],
   "while the owner keeps them")

grid_note = next(c for c in BY_NAME["cmp"]["columns"] if c["column"] == "nota")
ok(grid_note.get("open") and grid_note["rows"][0]["cells"] is None,
   "the same holds for a comment column of a matrix of questions")
BIG_GRID = {**BY_NAME["cmp"], "n": 20, "exposed": 20}
ok(not any(c.get("texts") for c in
           (aggregate.for_audience(BIG_GRID, aggregate.PUBLIC)["columns"] or [])),
   "and there too they stay with the owner")
ok(any(c.get("texts") for c in
       aggregate.for_audience(BIG_GRID, aggregate.OWNER)["columns"]),
   "who still reads them")

print("\n--- ranking ---")
items = {i["value"]: i for i in BY_NAME["rank"]["items"]}
show("positions", {k: v["positions"] for k, v in items.items()})
show("mean rank", {k: v["mean_rank"] for k, v in items.items()})
ok(items["k1"]["positions"] == [2, 1, 0], "where each item landed, not just its average")
ok(items["k1"]["mean_rank"] < items["k2"]["mean_rank"] < items["k3"]["mean_rank"],
   "items come back best-ranked first")

print("\n--- a type nobody taught it ---")
show("firma", BY_NAME["firma"])
ok(BY_NAME["firma"]["shape"] == "unsupported" and BY_NAME["firma"]["exposed"] == 8,
   "an unknown type still reports its denominators instead of vanishing")

print("\n--- splitting by arm ---")
split = {q["name"]: q for q in
         aggregate.summarise(SCHEMA, POOLS, R, by="cond")["questions"]}
show("ruolo by cond", {k: v["n"] for k, v in split["ruolo"]["groups"].items()})
ok(set(split["ruolo"]["groups"]) == {"A", "B"}, "one group per condition value")
ok(split["ruolo"]["groups"]["A"]["exposed"] == 4
   and split["ruolo"]["groups"]["B"]["exposed"] == 4,
   "and each group's denominator is its own arm")

print("\n--- a report of hand-picked blocks ---")
picked = aggregate.summarise(SCHEMA, POOLS, R, only=["score", "ruolo", "ghost"])
names = [q["name"] for q in picked["questions"]]
show("picked", names)
ok(names == ["score", "ruolo", "ghost"], "blocks come back in the order the report asks")
ok(picked["questions"][2]["orphan"] is True,
   "a block naming a question the schema lost is reported, not dropped")

print("\n--- disclosure ---")
pub = aggregate.for_audience(BY_NAME["note"], aggregate.PUBLIC)
show("note (public)", pub)
ok("texts" not in pub, "open text does not reach the public view")

pub = aggregate.for_audience(BY_NAME["age"], aggregate.PUBLIC)
ok("values" not in pub, "nor do raw numeric values")

pub = aggregate.for_audience(BY_NAME["tools"], aggregate.PUBLIC)
ok("other_texts" not in pub, "nor the free text behind 'other'")

pub = aggregate.for_audience(BY_NAME["ruolo"], aggregate.PUBLIC)
show("ruolo (public)", pub["cells"])
ok(all(c.get("suppressed") for c in pub["cells"] if c["value"] in "abc"),
   "cells below the threshold are masked rather than published")
ok(aggregate.for_audience(BY_NAME["ruolo"], aggregate.OWNER)["cells"][0]["n"] == 3,
   "and the owner still sees them")

one_small = {"name": "q", "type": "rating", "shape": "scale", "exposed": 6, "n": 6,
             "missing": 0, "cells": [{"value": 1, "n": 0}, {"value": 2, "n": 0},
                                     {"value": 3, "n": 0}, {"value": 4, "n": 1},
                                     {"value": 5, "n": 5}]}
pub = aggregate.for_audience(one_small, aggregate.PUBLIC)
show("one small cell (public)", [(c["value"], c.get("n"), c.get("suppressed")) for c in pub["cells"]])
ok(sum(1 for c in pub["cells"] if c.get("suppressed")) >= 2,
   "masking one cell while publishing the total is a subtraction away from being undone, "
   "so the hidden group grows until the split cannot be recovered")
ok([c["value"] for c in pub["cells"] if not c.get("suppressed")] == [1, 2, 3],
   "cells at zero stay visible: they name nobody")

pub = aggregate.for_audience(BY_NAME["why"], aggregate.PUBLIC)
show("why (public)", pub)
ok(pub.get("below_threshold") and pub["n"] == 1,
   "a question below the threshold says so, with its total, instead of going blank")

pub = aggregate.for_audience(split["ruolo"], aggregate.PUBLIC)
ok("groups" not in pub and "by" not in pub,
   "the arm breakdown is the manipulation, and stays with the owner")

resp = aggregate.for_audience(BY_NAME["note"], aggregate.RESPONDENT)
ok("texts" not in resp, "the respondent does not see other people's open answers either")

# Every shape through every audience. The first version of this module crashed
# on a plain matrix here, because its columns are bare values while a matrix of
# questions has dicts, and only the second kind had ever been filtered.
broken = []
for name, agg in BY_NAME.items():
    for audience in aggregate.AUDIENCES:
        try:
            aggregate.for_audience(agg, audience)
        except Exception as exc:
            broken.append(f"{name}/{audience}: {exc}")
show("shapes filtered", sorted({a["shape"] for a in BY_NAME.values()}))
ok(not broken, "every shape survives every audience filter" + (f" — {broken}" if broken else ""))

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
