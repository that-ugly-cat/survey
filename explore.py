"""Two variables at a time, and whether they move together.

Exploratory only, and the design says so in three ways: the test is chosen from
the shape of the data rather than by the person asking, every answer carries an
effect size beside its p-value, and the caveats come back with the result
instead of living in a footnote nobody reads.

What it will not do is decide anything. With forty answers and a handful of
questions there are dozens of pairs to try, and a tool that makes trying them
cheap is a tool that manufactures p-values below 0.05 at exactly the rate the
arithmetic predicts. That warning is returned with every result that has
company.

Readers can be let in, one survey at a time, because a citizen-science page is
worth more when the people who contributed can turn the data over themselves.
What they get is the association and not the people behind it: the statistic
and the effect size are computed on everything, while the cells of any table
come back masked under the same rule the charts use, the floor rises to twenty
complete pairs, and they may only cross questions the report already publishes.
Off unless the owner turns it on, and never on by default: the reason to open
this door for a bird count is not a reason to open it for a study about
abuse.

Pure module: no FastAPI, no database.
"""

import aggregate
import stats
from results import loc

# What a question can be treated as when two of them are compared. Anything not
# in here — a grid, a ranking, a repeating matrix, open text — is left out of
# the picker rather than coerced into a shape it does not have.
KIND_OF_SHAPE = {
    "categorical": "nominal",
    "binary": "nominal",
    "scale": "ordinal",
    "numeric": "numeric",
}

RANKABLE = ("ordinal", "numeric")

# The conventional reading of Cramér's V and of a rank correlation. Bands, not
# thresholds: they are there to stop a V of 0.11 being described as a finding.
def _band(value: float) -> str:
    v = abs(value)
    if v < 0.1:
        return "negligible"
    if v < 0.3:
        return "small"
    if v < 0.5:
        return "moderate"
    return "large"


def variables(schema: dict, locale: str = "en") -> list:
    """The questions that can go on either axis, one entry per variable.

    A checkbox is not one variable but one per option, each of them a yes/no:
    "did this person tick 'violence'". Folding it into a single nominal would
    mean inventing a category for every combination anybody happened to pick.
    """
    out = []
    for _, el, _ in aggregate.elements_of(schema):
        shape = aggregate.shape_of(el)
        title = loc(el.get("title"), locale) or el.get("name")
        if shape == "multi":
            for choice in el.get("choices") or []:
                value = choice.get("value") if isinstance(choice, dict) else choice
                text = (loc(choice.get("text", value), locale)
                        if isinstance(choice, dict) else str(choice))
                out.append({"id": f"{el['name']}:{value}", "question": el["name"],
                            "option": value, "kind": "nominal",
                            "label": f"{title} — {text}"})
            continue
        kind = KIND_OF_SHAPE.get(shape)
        if kind:
            out.append({"id": el["name"], "question": el["name"], "option": None,
                        "kind": kind, "label": title})
    return out


def _find(schema: dict, var_id: str):
    for v in variables(schema):
        if v["id"] == var_id:
            return v
    return None


def _element(schema: dict, name: str):
    for _, el, _ in aggregate.elements_of(schema):
        if el.get("name") == name:
            return el
    return {}


def series(schema: dict, responses: list, var: dict) -> list:
    """One value per response, or None where there is nothing to use.

    An unanswered question is None and drops out of the pair. For a checkbox
    option that matters: somebody who skipped the question did not say "no",
    and counting them as a no would invent an answer.
    """
    out = []
    for data in responses:
        answers = aggregate.answers(data)
        raw = answers.get(var["question"])
        if not aggregate._answered(raw):
            out.append(None)
            continue
        if var["option"] is not None:
            picked = raw if isinstance(raw, list) else [raw]
            out.append(var["option"] in picked)
            continue
        if var["kind"] == "numeric":
            try:
                out.append(float(raw))
            except (TypeError, ValueError):
                out.append(None)
            continue
        if var["kind"] == "ordinal":
            out.append(raw if isinstance(raw, (int, float))
                       and not isinstance(raw, bool) else None)
            continue
        out.append(raw)
    return out


def _labels_for(schema: dict, var: dict, locale: str) -> dict:
    """Value → the word a reader knows it by."""
    if var["option"] is not None:
        return {True: "sì" if locale == "it" else "yes",
                False: "no"}
    el = _element(schema, var["question"])
    out = {}
    for choice in el.get("choices") or []:
        if isinstance(choice, dict):
            out[choice.get("value")] = loc(choice.get("text", choice.get("value")), locale)
        else:
            out[choice] = str(choice)
    for rate in el.get("rateValues") or []:
        if isinstance(rate, dict):
            out[rate.get("value")] = loc(rate.get("text", rate.get("value")), locale)
    if el.get("type") == "boolean":
        out[True] = loc(el.get("labelTrue", "Yes"), locale)
        out[False] = loc(el.get("labelFalse", "No"), locale)
    return out


def _levels(values: list, labels: dict) -> list:
    """Distinct values, in the order the schema offers them where it says, and
    then whatever else the data holds."""
    seen = list(labels)
    extra = [v for v in dict.fromkeys(values) if v not in seen]
    return [v for v in seen if v in values] + extra


def _mask_matrix(counts: list, threshold: int = aggregate.THRESHOLD) -> list:
    """Hide the small cells of a contingency table, and enough of the rest.

    The same rule the charts use, for the same reason: one hidden cell in a
    table whose margins are visible is a subtraction away from being read. The
    statistic above the table is computed on everything — it is an aggregate,
    not a cell — and it is the number a reader is here for.
    """
    flat = [(i, j, counts[i][j]) for i in range(len(counts))
            for j in range(len(counts[i]))]
    hidden = {(i, j) for i, j, n in flat if 0 < n < threshold}
    if hidden:
        order = sorted((c for c in flat if c[2]), key=lambda c: c[2])
        total = sum(n for i, j, n in flat if (i, j) in hidden)
        for i, j, n in order:
            if total >= threshold and len(hidden) > 1:
                break
            if (i, j) not in hidden:
                hidden.add((i, j))
                total += n
    return [[None if (i, j) in hidden else counts[i][j]
             for j in range(len(counts[i]))] for i in range(len(counts))]


def associate(schema: dict, responses: list, x_id: str, y_id: str,
              locale: str = "en", minimum: int = 10,
              audience: str = aggregate.OWNER, allowed: set = None) -> dict:
    """Whether two variables move together, with the test chosen from shapes.

    `audience` other than the owner masks the cells of whatever table comes
    back and raises the floor: a reader gets the association and not the people
    behind it. `allowed` bounds which variables they may reach at all.
    """
    if audience != aggregate.OWNER:
        minimum = max(minimum, 20)
    x, y = _find(schema, x_id), _find(schema, y_id)
    if allowed is not None and x and y and not (
            x["question"] in allowed and y["question"] in allowed):
        return {"error": "not_published"}
    if not x or not y:
        return {"error": "unknown_variable",
                "detail": x_id if not x else y_id}
    if x_id == y_id:
        return {"error": "same_variable"}

    xs, ys = series(schema, responses, x), series(schema, responses, y)
    pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
    n = len(pairs)
    base = {"x": x, "y": y, "n": n, "caveats": []}
    if n < minimum:
        return {**base, "error": "too_few", "minimum": minimum}

    xv = [p[0] for p in pairs]
    yv = [p[1] for p in pairs]
    xl, yl = _labels_for(schema, x, locale), _labels_for(schema, y, locale)

    # Two rankable variables: a rank correlation, which is the honest default
    # on Likert answers — it asks about order and claims nothing about spacing.
    if x["kind"] in RANKABLE and y["kind"] in RANKABLE:
        r = stats.spearman(xv, yv)
        if r["rho"] is None:
            return {**base, "error": "no_variation"}
        out = {**base, "test": "spearman", "p": r["p"],
               "statistic": {"rho": round(r["rho"], 4)},
               "effect": {"name": "rho", "value": round(r["rho"], 3),
                          "band": _band(r["rho"])},
               "points": n}
        return _with_caveats(out)

    # One nominal, one rankable: compare the distributions between groups.
    if x["kind"] in RANKABLE or y["kind"] in RANKABLE:
        group_var, value_var = (y, x) if x["kind"] in RANKABLE else (x, y)
        gv = yv if x["kind"] in RANKABLE else xv
        vv = xv if x["kind"] in RANKABLE else yv
        labels = yl if x["kind"] in RANKABLE else xl
        buckets = {}
        for g, v in zip(gv, vv):
            buckets.setdefault(g, []).append(v)
        buckets = {g: vs for g, vs in buckets.items() if len(vs) >= 2}
        if len(buckets) < 2:
            return {**base, "error": "no_variation"}
        keys = _levels(list(buckets), labels)
        groups = [{"value": k, "label": labels.get(k, str(k)), "n": len(buckets[k]),
                   "median": sorted(buckets[k])[len(buckets[k]) // 2],
                   "mean": round(sum(buckets[k]) / len(buckets[k]), 2)}
                  for k in keys]
        if len(keys) == 2:
            r = stats.mann_whitney(buckets[keys[0]], buckets[keys[1]])
            # Rank-biserial: the share of cross-pairs going one way minus the
            # other, which reads as "how often one group is above the other".
            n1, n2 = len(buckets[keys[0]]), len(buckets[keys[1]])
            rb = 1 - 2 * r["u"] / (n1 * n2) if n1 and n2 else None
            out = {**base, "test": "mannwhitney", "p": r["p"],
                   "statistic": {"u": r["u"], "z": round(r["z"], 3)},
                   "effect": {"name": "rank_biserial", "value": round(rb, 3),
                              "band": _band(rb)},
                   "groups": groups, "group_of": group_var["id"]}
        else:
            r = stats.kruskal([buckets[k] for k in keys])
            # Epsilon squared: H scaled to the 0–1 it can actually reach.
            eps = (r["h"] - len(keys) + 1) / (n - len(keys)) if n > len(keys) else 0
            eps = max(0.0, eps)
            out = {**base, "test": "kruskal", "p": r["p"],
                   "statistic": {"h": round(r["h"], 3), "df": r["df"]},
                   "effect": {"name": "epsilon_squared", "value": round(eps, 3),
                              "band": _band(eps ** 0.5)},
                   "groups": groups, "group_of": group_var["id"]}
        if any(g["n"] < 5 for g in groups):
            out["caveats"].append({"kind": "small_group"})
        if audience != aggregate.OWNER:
            # A group of three is three people described by their median.
            kept = [g for g in out["groups"] if g["n"] >= aggregate.THRESHOLD]
            if len(kept) < len(out["groups"]):
                out["groups"] = kept
                out["caveats"].append({"kind": "groups_hidden"})
        return _with_caveats(out)

    # Both nominal: a contingency table.
    rows = _levels(xv, xl)
    cols = _levels(yv, yl)
    if len(rows) < 2 or len(cols) < 2:
        return {**base, "error": "no_variation"}
    table = [[sum(1 for a, b in pairs if a == r and b == c) for c in cols] for r in rows]
    chi = stats.chi_square(table)
    out = {**base,
           "table": {"rows": [{"value": r, "label": xl.get(r, str(r))} for r in rows],
                     "cols": [{"value": c, "label": yl.get(c, str(c))} for c in cols],
                     "counts": table},
           "effect": {"name": "cramers_v", "value": round(chi["v"], 3),
                      "band": _band(chi["v"])}}
    # Fisher where it is exact and chi-square where it is not trustworthy: on a
    # 2x2 the exact answer costs nothing, and small expected counts are exactly
    # the case the approximation was never meant for.
    if audience != aggregate.OWNER:
        masked = _mask_matrix(table)
        out["table"]["counts"] = masked
        if any(c is None for row in masked for c in row):
            out["caveats"].append({"kind": "cells_hidden"})
    if len(rows) == 2 and len(cols) == 2:
        p = stats.fisher_exact_2x2(table[0][0], table[0][1], table[1][0], table[1][1])
        out.update({"test": "fisher", "p": p, "statistic": {}})
    else:
        out.update({"test": "chi2", "p": chi["p"],
                    "statistic": {"chi2": round(chi["chi2"], 3), "df": chi["df"]}})
        if chi["expected_below_5"]:
            out["caveats"].append({"kind": "expected_small",
                                   "cells": chi["expected_below_5"],
                                   "of": chi["cells"]})
    return _with_caveats(out)


def _with_caveats(out: dict) -> dict:
    """The warnings that belong to every result, rather than to some of them."""
    out["caveats"].append({"kind": "exploratory"})
    if out.get("p") is not None and out["p"] < 0.05:
        out["caveats"].append({"kind": "multiple"})
    if out["n"] < 30:
        out["caveats"].append({"kind": "small_n", "n": out["n"]})
    return out


def pair_count(schema: dict) -> int:
    """How many pairs this questionnaire offers, which is how many chances
    there are for one of them to look interesting by accident."""
    k = len(variables(schema))
    return k * (k - 1) // 2
