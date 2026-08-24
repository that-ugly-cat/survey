"""
What a participant in a given arm would actually walk through — computed from
the schema, without a browser.

This exists because the alternative is opening the questionnaire and clicking.
That works, but it answers one arm at a time, it needs a rendered page, and it
cannot tell you what it did not happen to hit. Reading the schema answers every
arm at once, and answers it the same way twice.

The honest limit is visibility that depends on answers. `{condition} = 'C'` is
decidable before anyone opens the survey; `{pgt_q2} >= 2` is not. So expressions
are evaluated in three states — true, false, and unknown — and a question whose
visibility turns on an answer is reported as conditional, with the expression
that governs it, rather than guessed at. Nothing here pretends to know more than
the schema does.
"""
import json
import re

TRUE, FALSE, UNKNOWN = True, False, None

_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<var>\{[^}]*\})
      | (?P<str>'[^']*'|"[^"]*")
      | (?P<num>-?\d+(?:\.\d+)?)
      | (?P<op><>|!=|==|>=|<=|=|>|<)
      | (?P<lpar>\()
      | (?P<rpar>\))
      | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
    )
""", re.VERBOSE)

_WORD_OPS = {"contains", "notcontains", "anyof", "allof", "empty", "notempty"}


def _tokenize(expr: str) -> list:
    tokens, pos = [], 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            if expr[pos].isspace():
                pos += 1
                continue
            # An unparseable tail makes the whole expression undecidable, which
            # is the correct answer — not a crash, and not a guess.
            return []
        pos = m.end()
        kind = m.lastgroup
        value = m.group(kind)
        if kind == "var":
            tokens.append(("var", value[1:-1].strip()))
        elif kind == "str":
            tokens.append(("lit", value[1:-1]))
        elif kind == "num":
            tokens.append(("lit", float(value) if "." in value else int(value)))
        elif kind == "word":
            low = value.lower()
            if low in ("and", "or", "not"):
                tokens.append((low, low))
            elif low in _WORD_OPS:
                tokens.append(("op", low))
            elif low in ("true", "false"):
                tokens.append(("lit", low == "true"))
            else:
                tokens.append(("var", value))
        else:
            tokens.append((kind, value))
    return tokens


def refs(expr: str) -> list:
    """Every {name} the expression reads, in order of first appearance."""
    seen, out = set(), []
    for kind, value in _tokenize(expr or ""):
        if kind == "var" and value not in seen:
            seen.add(value)
            out.append(value)
    return out


class _Parser:
    """Recursive descent over the SurveyJS subset the schemas here actually use.

    Anything outside that subset evaluates to UNKNOWN rather than raising: the
    caller wants "cannot tell" as an answer, not an exception.
    """

    def __init__(self, tokens, values):
        self.t, self.i, self.values = tokens, 0, values

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        value = self.parse_or()
        return value if self.i >= len(self.t) else UNKNOWN

    def parse_or(self):
        left = self.parse_and()
        while self.peek()[0] == "or":
            self.take()
            right = self.parse_and()
            if left is TRUE or right is TRUE:
                left = TRUE
            elif left is FALSE and right is FALSE:
                left = FALSE
            else:
                left = UNKNOWN
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek()[0] == "and":
            self.take()
            right = self.parse_not()
            if left is FALSE or right is FALSE:
                left = FALSE
            elif left is TRUE and right is TRUE:
                left = TRUE
            else:
                left = UNKNOWN
        return left

    def parse_not(self):
        if self.peek()[0] == "not":
            self.take()
            inner = self.parse_not()
            return UNKNOWN if inner is UNKNOWN else (not inner)
        return self.parse_atom()

    def parse_atom(self):
        kind, value = self.peek()
        if kind == "lpar":
            self.take()
            inner = self.parse_or()
            if self.peek()[0] == "rpar":
                self.take()
            else:
                return UNKNOWN
            return inner
        return self.parse_comparison()

    def _operand(self):
        kind, value = self.take()
        if kind == "var":
            return ("known", self.values[value]) if value in self.values else ("unknown", None)
        if kind == "lit":
            return ("known", value)
        return ("bad", None)

    def parse_comparison(self):
        left_kind, left = self._operand()
        if left_kind == "bad":
            return UNKNOWN
        kind, op = self.peek()
        if kind != "op":
            # A bare {flag} is truthy-tested by SurveyJS.
            return UNKNOWN if left_kind == "unknown" else bool(left)
        self.take()
        if op in ("empty", "notempty"):
            if left_kind == "unknown":
                return UNKNOWN
            is_empty = left in (None, "", [], {})
            return is_empty if op == "empty" else not is_empty
        right_kind, right = self._operand()
        if left_kind != "known" or right_kind != "known":
            return UNKNOWN
        return _compare(left, op, right)


def _compare(left, op, right):
    try:
        if op in ("=", "=="):
            return _loose_eq(left, right)
        if op in ("<>", "!="):
            return not _loose_eq(left, right)
        if op == "contains":
            return right in left if hasattr(left, "__contains__") else UNKNOWN
        if op == "notcontains":
            return right not in left if hasattr(left, "__contains__") else UNKNOWN
        if op == "anyof":
            return any(x in left for x in (right if isinstance(right, list) else [right]))
        if op == "allof":
            return all(x in left for x in (right if isinstance(right, list) else [right]))
        left_n, right_n = float(left), float(right)
        return {">": left_n > right_n, ">=": left_n >= right_n,
                "<": left_n < right_n, "<=": left_n <= right_n}[op]
    except (TypeError, ValueError, KeyError):
        return UNKNOWN


def _loose_eq(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def evaluate(expr, values: dict):
    """TRUE / FALSE / UNKNOWN for a SurveyJS visibleIf, given known values."""
    if not expr:
        return TRUE
    tokens = _tokenize(expr)
    if not tokens:
        return UNKNOWN
    return _Parser(tokens, values).parse()


# --- schema walking ---

def questions_of(page: dict) -> list:
    """Data-bearing elements of a page, in order. `html` elements carry no
    answer and are skipped: they are layout, not measurement."""
    out = []
    for el in page.get("elements", []):
        if el.get("type") == "html":
            continue
        out.append(el)
    return out


def all_questions(schema: dict) -> list:
    return [(p, q) for p in schema.get("pages", []) for q in questions_of(p)]


def apply_page_order(pages: list, sequence: list) -> list:
    """Put the named pages into the slots they collectively occupy, in the order
    given — the same rule the browser applies, so a preview and a live run agree.
    """
    idx = [i for i in (next((j for j, p in enumerate(pages) if p.get("name") == name), -1)
                       for name in sequence) if i >= 0]
    if len(idx) < 2:
        return pages
    out = list(pages)
    moved = [pages[i] for i in idx]
    for slot, page in zip(sorted(idx), moved):
        out[slot] = page
    return out


def arms(pools: list) -> list:
    """Every combination of condition values the pools can produce.

    One entry per arm: the variable values, and the pool pages drawn to get
    there. Pools with no condition variable contribute nothing to the arm
    identity — they still hide pages, but they do not name a condition.
    """
    combos = [{"values": {}, "pages": []}]
    for pool in pools:
        var = pool.get("condition_var")
        pages = pool.get("pool_pages") or []
        if not var or pool.get("show_count", 1) != 1 or not pages:
            continue
        cmap = pool.get("condition_map") or {}
        expanded = []
        for combo in combos:
            for page in pages:
                expanded.append({
                    "values": {**combo["values"], var: cmap.get(page, page)},
                    "pages": combo["pages"] + [page],
                })
        combos = expanded
    return combos


def preview(schema: dict, pools: list, answers: dict = None) -> list:
    """One entry per arm: the pages a participant walks through, in order, and
    the questions on each — marked `always` or `conditional`.

    Pool pages that were not drawn are dropped, exactly as the browser drops
    them; page-order maps are applied before anything else, so the sequence
    reported here is the sequence the participant sees.

    `answers` pins values that would otherwise be undecidable — pass the consent
    to see the walk of someone who consented, or pin a rating to see which
    follow-ups it opens. Anything left unpinned stays reported as conditional
    rather than assumed either way.
    """
    pooled = {name for pool in pools for name in (pool.get("pool_pages") or [])}
    out = []
    for arm in arms(pools):
        values = {**(answers or {}), **arm["values"]}
        pages = list(schema.get("pages", []))
        for pool in pools:
            order = (pool.get("page_order") or {})
            var = pool.get("condition_var")
            sequence = order.get(values.get(var)) if var else None
            if isinstance(sequence, list):
                pages = apply_page_order(pages, sequence)

        walk = []
        for page in pages:
            name = page.get("name")
            if name in pooled and name not in arm["pages"]:
                continue
            state = evaluate(page.get("visibleIf"), values)
            if state is FALSE:
                continue
            questions = []
            for q in questions_of(page):
                q_state = evaluate(q.get("visibleIf"), values)
                if q_state is FALSE:
                    continue
                questions.append({
                    "name": q.get("name"),
                    "type": q.get("type"),
                    "required": bool(q.get("isRequired")),
                    "shown": "always" if q_state is TRUE else "conditional",
                    "condition": q.get("visibleIf") if q_state is UNKNOWN else None,
                })
            walk.append({
                "page": name,
                "title": page.get("title"),
                "shown": "always" if state is TRUE else "conditional",
                "condition": page.get("visibleIf") if state is UNKNOWN else None,
                "questions": questions,
            })
        out.append({"condition": arm["values"], "assigned_pages": arm["pages"], "pages": walk})
    return out


# --- validation ---

PLACEHOLDER_CHOICE = re.compile(r"^Item\s*\d+$", re.I)


def _choice_values(el: dict) -> list:
    out = []
    for choice in el.get("choices") or []:
        out.append(choice.get("value") if isinstance(choice, dict) else choice)
    return out


def validate(schema: dict, pools: list = ()) -> list:
    """Problems worth a human look, most structural first.

    Every finding names the thing it is about, so a caller can act on it without
    re-reading the schema. Findings are advisory: this refuses nothing.
    """
    findings = []
    pages = schema.get("pages", [])
    page_names = [p.get("name") for p in pages]
    q_pairs = all_questions(schema)
    q_names = [q.get("name") for _, q in q_pairs]

    def add(severity, kind, message, where=None):
        findings.append({"severity": severity, "kind": kind,
                         "message": message, "where": where})

    for name in {n for n in page_names if page_names.count(n) > 1}:
        add("error", "duplicate_page", f"page name '{name}' is used more than once", name)
    for name in {n for n in q_names if q_names.count(n) > 1}:
        add("error", "duplicate_question",
            f"question name '{name}' is used more than once — answers would collide", name)
    for i, name in enumerate(page_names):
        if not name:
            add("error", "unnamed_page", f"page {i} has no name; pools and page order cannot reach it")

    # position of each question, so a condition on a later answer is catchable
    position = {}
    for page_index, page in enumerate(pages):
        for q in questions_of(page):
            position.setdefault(q.get("name"), page_index)

    known = set(q_names) | {p.get("condition_var") for p in pools if p.get("condition_var")}
    known |= {"panelToken"}
    for page_index, page in enumerate(pages):
        targets = [(page.get("visibleIf"), page.get("name"), page_index)]
        for q in questions_of(page):
            targets.append((q.get("visibleIf"), q.get("name"), page_index))
        for expr, where, at in targets:
            if not expr:
                continue
            if not _tokenize(expr):
                add("error", "unparseable_condition",
                    f"visibleIf on '{where}' cannot be parsed: {expr}", where)
                continue
            for ref in refs(expr):
                if ref not in known:
                    add("error", "dangling_reference",
                        f"visibleIf on '{where}' reads '{ref}', which is neither a question "
                        f"in this schema nor a condition variable", where)
                elif ref in position and position[ref] > at:
                    add("error", "forward_reference",
                        f"visibleIf on '{where}' reads '{ref}', which is answered on a later "
                        f"page — it can never be true when this is shown", where)

    for _, q in q_pairs:
        values = _choice_values(q)
        placeholders = [v for v in values if isinstance(v, str) and PLACEHOLDER_CHOICE.match(v)]
        if placeholders:
            add("warning", "placeholder_choice_value",
                f"'{q.get('name')}' stores placeholder values {placeholders} — the export will "
                f"not say what the participant chose, and reordering the options silently "
                f"changes what past answers mean", q.get("name"))
        if len(values) != len(set(map(str, values))) and values:
            add("error", "duplicate_choice_value",
                f"'{q.get('name')}' has two choices with the same value", q.get("name"))

    # pools
    seen_pages = {}
    for pool in pools:
        label = pool.get("pool_name") or "pool"
        pool_pages = pool.get("pool_pages") or []
        if not pool_pages:
            add("warning", "empty_pool", f"pool '{label}' has no pages and does nothing", label)
        for name in pool_pages:
            if name not in page_names:
                add("error", "pool_page_missing",
                    f"pool '{label}' lists page '{name}', which is not in the schema", label)
            if name in seen_pages and seen_pages[name] != label:
                add("error", "page_in_two_pools",
                    f"page '{name}' is in both '{seen_pages[name]}' and '{label}'", name)
            seen_pages[name] = label
        show = pool.get("show_count", 1)
        if pool_pages and not 0 < show <= len(pool_pages):
            add("error", "bad_show_count",
                f"pool '{label}' shows {show} of {len(pool_pages)} pages", label)
        cmap = pool.get("condition_map") or {}
        for key in cmap:
            if key not in pool_pages:
                add("warning", "condition_map_stray",
                    f"pool '{label}' maps '{key}', which is not one of its pages", label)
        if cmap and pool.get("condition_var") and show == 1:
            missing = [p for p in pool_pages if p not in cmap]
            if missing:
                add("warning", "condition_map_incomplete",
                    f"pool '{label}' has no value for {missing}; the page name will be used "
                    f"as the condition value", label)
        order = pool.get("page_order") or {}
        for value, sequence in order.items():
            if cmap and value not in set(cmap.values()):
                add("warning", "page_order_stray",
                    f"pool '{label}' orders pages for condition '{value}', which it never "
                    f"assigns", label)
            for name in sequence or []:
                if name not in page_names:
                    add("error", "page_order_missing",
                        f"pool '{label}' orders page '{name}', which is not in the schema", label)

    # arms that reach nothing
    for arm in preview(schema, pools):
        reachable = sum(len(p["questions"]) for p in arm["pages"])
        if not reachable:
            add("error", "empty_arm",
                f"condition {arm['condition']} reaches no questions at all", None)

    order = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: order.get(f["severity"], 2))
    return findings


def summarise(schema: dict) -> dict:
    pages = schema.get("pages", [])
    qs = [q for _, q in all_questions(schema)]
    return {
        "title": schema.get("title"),
        "pages": len(pages),
        "questions": len(qs),
        "required": sum(1 for q in qs if q.get("isRequired")),
        "conditional": sum(1 for q in qs if q.get("visibleIf")),
        "question_names": [q.get("name") for q in qs],
    }


def parse_schema(raw) -> dict:
    return raw if isinstance(raw, dict) else json.loads(raw)
