"""Rotation of parallel forms across waves (docs/item-banks-ui-spec.md, phase 2).

A longitudinal study with k parallel forms gives each participant a different form at
each wave. With a bank whose arm comes from an answer (arm_from, item-banks-spec
section 15), the rotation is a calculated value of the schema:

    form = ({question} + wave) % k  ->  0 = first group, 1 = second, ...

where `question` is something every wave asks and every participant answers the same
way (a day of birth in a self-generated code). At the next wave the same person gets the
next group. Plain functions, no database: the admin route and the tests call them, and
static/bank-editor.js mirrors build/parse/shares for the preview.
"""
import re

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
ARM_RE = re.compile(r"^[^'\\{}]{1,64}$")  # an arm name goes inside single quotes in the expression


def build_expression(question: str, wave: int, arms: list) -> str:
    """The SurveyJS expression for wave `wave` of a rotation over `arms`, in order."""
    k = len(arms)
    r = f"({{{question}}} + {int(wave)}) % {k}"
    if k == 1:
        return f"'{arms[0]}'"
    expr = f"'{arms[-1]}'"
    for i in range(k - 2, -1, -1):
        expr = f"iif({r} = {i}, '{arms[i]}', {expr})"
    return expr


_PARSE = re.compile(r"^iif\(\(\{(?P<q>[A-Za-z_][A-Za-z0-9_]*)\} \+ (?P<w>\d+)\) % (?P<k>\d+) = 0, ")


def parse_expression(expr: str):
    """{question, wave, arms} when `expr` is exactly what build_expression writes, else None.
    A hand-written expression that happens to rotate is not recognised: the editor then
    shows it as an ordinary answer source rather than guess."""
    m = _PARSE.match(expr or "")
    if not m:
        return None
    k = int(m.group("k"))
    arms = re.findall(r"'([^']*)'", expr)
    if len(arms) != k:
        return None
    parsed = {"question": m.group("q"), "wave": int(m.group("w")), "arms": arms}
    return parsed if build_expression(parsed["question"], parsed["wave"], arms) == expr else None


def _day_weights():
    """How often each day of the month occurs in a year: the 31st in seven months, the
    30th in eleven, the 29th in eleven and one leap year in four."""
    w = {d: 12.0 for d in range(1, 29)}
    w[29], w[30], w[31] = 11.25, 11.0, 7.0
    return w


def expected_shares(question_meta: dict, wave: int, arms: list):
    """Expected share of participants per arm, when the answer's range is known: a day of
    the month (a number from 1 to 31) is weighted by the calendar, any other bounded
    integer is taken as uniform. None when nothing can be said."""
    lo, hi = question_meta.get("min"), question_meta.get("max")
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return None
    if hi < lo or hi - lo > 10000:
        return None
    weights = _day_weights() if (lo, hi) == (1, 31) else {v: 1.0 for v in range(lo, hi + 1)}
    k, total = len(arms), sum(weights.values())
    shares = {a: 0.0 for a in arms}
    for v, w in weights.items():
        shares[arms[(v + int(wave)) % k]] += w
    return {a: round(100 * s / total, 1) for a, s in shares.items()}


def validate(params: dict, schema: dict) -> list:
    """Reasons the rotation cannot be added, as short codes with a message; empty when it can."""
    errors = []
    name, question = params.get("name") or "", params.get("question") or ""
    arms = params.get("arms") or []
    try:
        wave = int(params.get("wave"))
    except (TypeError, ValueError):
        wave = 0
    questions = {q.get("name") for p in schema.get("pages", []) for q in p.get("elements", [])
                 if q.get("type") != "html"}
    if not NAME_RE.match(name):
        errors.append(("bad_name", "the value needs a name made of letters, digits and underscores"))
    elif name in questions:
        errors.append(("name_taken", f"{name!r} is already a question name"))
    if question not in questions:
        errors.append(("unknown_question", f"{question!r} is not a question of the questionnaire"))
    if wave < 1:
        errors.append(("bad_wave", "the wave is a whole number from 1"))
    if len(arms) < 2:
        errors.append(("few_arms", "a rotation needs at least two groups"))
    elif len(set(arms)) != len(arms) or not all(isinstance(a, str) and ARM_RE.match(a) for a in arms):
        errors.append(("bad_arms", "group names must be distinct, and cannot contain quotes or braces"))
    return errors


def apply(schema: dict, params: dict) -> tuple:
    """(new schema, expression, replaced): the calculated value added, or replaced if one
    with that name exists, saved with the answers so the server can read the arm."""
    expr = build_expression(params["question"], int(params["wave"]), params["arms"])
    new = dict(schema)
    values = [dict(c) for c in (schema.get("calculatedValues") or [])]
    replaced = False
    for c in values:
        if c.get("name") == params["name"]:
            c.update({"expression": expr, "includeIntoResult": True})
            replaced = True
    if not replaced:
        values.append({"name": params["name"], "expression": expr, "includeIntoResult": True})
    new["calculatedValues"] = values
    return new, expr, replaced
