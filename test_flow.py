"""Checks for flow.py: expression evaluation, arm preview, and validation.

No database and no browser — this is the module that exists so that neither is
needed to answer what a participant in a given arm would see.
"""
import json, os, sys

sys.path.insert(0, os.path.abspath("."))
import flow

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

print("\n--- expressions ---")
V = {"condition": "C", "vorder": "GGE_FIRST", "consent_agree": ["agree"], "sev": 4}
cases = [
    ("{condition} = 'C'", flow.TRUE),
    ("{condition} = 'A'", flow.FALSE),
    ("{condition} = 'A' or {condition} = 'C'", flow.TRUE),
    ("{condition} = 'A' or {condition} = 'B'", flow.FALSE),
    ("{consent_agree} contains 'agree' and ({condition} = 'A' or {condition} = 'C')", flow.TRUE),
    ("{consent_agree} notcontains 'agree'", flow.FALSE),
    ("{sev} >= 2", flow.TRUE),
    ("{sev} <= 3", flow.FALSE),
    ("{pgt_q2} >= 2", flow.UNKNOWN),
    ("{pgt_q2} >= 2 and {condition} = 'A'", flow.FALSE),      # false half decides
    ("{pgt_q2} >= 2 and {condition} = 'C'", flow.UNKNOWN),
    ("{pgt_q2} >= 2 or {condition} = 'C'", flow.TRUE),        # true half decides
    ("not {condition} = 'A'", flow.TRUE),
    ("", flow.TRUE),
    ("{condition} =", flow.UNKNOWN),                          # truncated
    ("this is not an expression at all !!", flow.UNKNOWN),
]
for expr, expected in cases:
    got = flow.evaluate(expr, V)
    ok(got is expected, f"{expr!r} -> {got}" + ("" if got is expected else f" (expected {expected})"))

ok(flow.refs("{consent_agree} contains 'agree' and {condition} = 'C'") == ["consent_agree", "condition"],
   "refs lists every variable read, in order")

print("\n--- page order ---")
pages = [{"name": n} for n in ["a", "pgt", "filler", "gge", "z"]]
ok([p["name"] for p in flow.apply_page_order(pages, ["gge", "pgt"])] ==
   ["a", "gge", "filler", "pgt", "z"], "named pages take the slots they occupied")
ok([p["name"] for p in flow.apply_page_order(pages, ["nope", "gge"])] ==
   ["a", "pgt", "filler", "gge", "z"], "an unresolvable name leaves the order alone")

print("\n--- preview over the real schema ---")
POOLS = [
    {"pool_name": "Study information / version",
     "pool_pages": ["info_a", "info_b", "info_c"], "show_count": 1,
     "condition_var": "condition",
     "condition_map": {"info_a": "A", "info_b": "B", "info_c": "C"}, "page_order": None},
    {"pool_name": "Vignette order",
     "pool_pages": ["order_pgt_first", "order_gge_first"], "show_count": 1,
     "condition_var": "vorder",
     "condition_map": {"order_pgt_first": "PGT_FIRST", "order_gge_first": "GGE_FIRST"},
     "page_order": {"GGE_FIRST": ["vignette_gge", "vignette_pgt"]}},
]

def page(name, visible_if=None, elements=()):
    p = {"name": name, "elements": list(elements)}
    if visible_if:
        p["visibleIf"] = visible_if
    return p

def q(name, required=True, visible_if=None, **extra):
    el = {"type": "rating", "name": name, "isRequired": required, **extra}
    if visible_if:
        el["visibleIf"] = visible_if
    return el

CONSENT = "{consent_agree} contains 'agree'"
schema = {"title": "T", "pages": [
    page("consent", None, [{"type": "checkbox", "name": "consent_agree", "isRequired": True,
                            "choices": [{"value": "agree", "text": "ok"}]}]),
    page("consent_reject", "{consent_agree} notcontains 'agree'", []),
    page("sociodem", CONSENT, [q("a1_age")]),
    page("info_a"), page("info_b"), page("info_c"),
    page("order_pgt_first"), page("order_gge_first"),
    page("vignette_postnatal", CONSENT, [q("postnatal_q1")]),
    page("vignette_pgt", CONSENT + " and ({condition} = 'A' or {condition} = 'C')",
         [q("pgt_q2"), q("pgt_q2a", visible_if="{pgt_q2} >= 2", type="dropdown",
                        choices=[{"value": "greater", "text": "Greater"}])]),
    page("vignette_gge", CONSENT + " and ({condition} = 'B' or {condition} = 'C')",
         [q("gge_q2")]),
    page("comparison", CONSENT + " and {condition} = 'C'", [q("comparison_d1")]),
]}

arms = flow.preview(schema, POOLS, answers={"consent_agree": ["agree"]})
ok(len(arms) == 6, f"three versions x two orders = {len(arms)} arms")

def arm(cond, order):
    return next(a for a in arms
                if a["condition"] == {"condition": cond, "vorder": order})

def names(a):
    return [p["page"] for p in a["pages"]]

ok(names(arm("A", "PGT_FIRST")) ==
   ["consent", "sociodem", "info_a", "order_pgt_first", "vignette_postnatal", "vignette_pgt"],
   "A: one info page, one divider, postnatal + PGT, no comparison")
ok(names(arm("B", "PGT_FIRST"))[-1] == "vignette_gge", "B ends on the GGE vignette")
ok(names(arm("C", "PGT_FIRST"))[-3:] == ["vignette_pgt", "vignette_gge", "comparison"],
   "C in schema order: PGT then GGE")
ok(names(arm("C", "GGE_FIRST"))[-3:] == ["vignette_gge", "vignette_pgt", "comparison"],
   "C counterbalanced: GGE then PGT")
ok("consent_reject" not in names(arm("A", "PGT_FIRST")),
   "the rejection page is not part of any arm's walk")

pgt = next(p for p in arm("A", "PGT_FIRST")["pages"] if p["page"] == "vignette_pgt")
by_name = {x["name"]: x for x in pgt["questions"]}
ok(by_name["pgt_q2"]["shown"] == "always", "an unconditional question is reported as always shown")
ok(by_name["pgt_q2a"]["shown"] == "conditional"
   and by_name["pgt_q2a"]["condition"] == "{pgt_q2} >= 2",
   "a question gated on an answer is reported with its condition, not guessed at")

print("\n--- validation ---")
ok(flow.validate(schema, POOLS) == [], "the good schema raises nothing")

def kinds(sch, pools=POOLS):
    return {f["kind"] for f in flow.validate(sch, pools)}

import copy
bad = copy.deepcopy(schema)
bad["pages"][2]["elements"].append(q("a1_age"))
ok("duplicate_question" in kinds(bad), "two questions with the same name")

bad = copy.deepcopy(schema)
bad["pages"][9]["elements"][1]["visibleIf"] = "{nonexistent} >= 2"
ok("dangling_reference" in kinds(bad), "visibleIf reading a name nothing defines")

bad = copy.deepcopy(schema)
bad["pages"][8]["elements"][0]["visibleIf"] = "{comparison_d1} = 1"
ok("forward_reference" in kinds(bad), "visibleIf reading an answer given on a later page")

bad = copy.deepcopy(schema)
bad["pages"][9]["elements"][1]["choices"] = [
    {"value": "Item 1", "text": "Greater than"}, {"value": "Item 2", "text": "Equal to"}]
found = [f for f in flow.validate(bad, POOLS) if f["kind"] == "placeholder_choice_value"]
ok(found and "pgt_q2a" in found[0]["message"],
   "placeholder choice values are caught — the v3 bug, found by reading alone")

bad = copy.deepcopy(POOLS)
bad[0]["pool_pages"] = ["info_a", "info_b", "info_gone"]
ok("pool_page_missing" in kinds(schema, bad), "a pool listing a page the schema lacks")

bad = copy.deepcopy(POOLS)
bad[1]["page_order"] = {"GGE_FIRST": ["vignette_gge", "vignette_nope"]}
ok("page_order_missing" in kinds(schema, bad), "a page order naming a page the schema lacks")

bad = copy.deepcopy(POOLS)
bad[1]["page_order"] = {"NEVER_ASSIGNED": ["vignette_gge", "vignette_pgt"]}
ok("page_order_stray" in kinds(schema, bad), "a page order keyed on a condition never assigned")

bad = copy.deepcopy(POOLS)
bad[0]["pool_pages"] = ["info_a", "info_b", "info_c", "order_pgt_first"]
ok("page_in_two_pools" in kinds(schema, bad), "a page claimed by two pools")

bad = copy.deepcopy(schema)
bad["pages"][11]["visibleIf"] = CONSENT + " and {condition} = 'NOPE'"
ok("empty_arm" not in kinds(bad), "a condition value no arm produces only silences that page")

print("\n--- summary ---")
s = flow.summarise(schema)
ok(s["pages"] == 12 and s["questions"] == 7, f"counts pages and questions ({s['pages']}, {s['questions']})")
ok(s["conditional"] == 1, "counts questions gated by a condition")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
