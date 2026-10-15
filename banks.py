"""Item banks: per participant, k items drawn from a bank, one version of each, in an
order, with the share of versions set by the participant's arm.

See docs/item-banks-spec.md. Everything here is plain functions over a config dict and
a database connection: allocation, validation and the long export are tested without a
server, and main.py only wires them into the page load, the submit and the admin.

A version is a page of the schema, so the browser shows, hides and reorders it with the
code pools already use; a bank only decides which pages and in which order.
"""
import json
import random
import re

# Limits (section 11 of the spec). Above them a save is refused with the reason.
MAX_ITEMS = 1000
MAX_VERSIONS = 8
MAX_SHOW = 50
MAX_ARMS = 12

# A pool enumerates every combination of its pages at each page load. Above this many
# the enumeration alone blocks the server, so such a pool is refused at save, and
# skipped at load if one was saved before the guard existed.
POOL_COMBINATION_CAP = 10_000

DEFAULT_ITEM_REGEX = r"^(?P<item>.+)_(?P<version>[^_]+)$"
ORDERS = ("random", "fixed")


# --- configuration -------------------------------------------------------------------

def normalise(raw) -> dict:
    """A config as stored, from a dict or JSON text. Missing optional fields get their
    defaults; nothing is validated here."""
    cfg = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {
        "name": str(cfg.get("name") or "bank").strip(),
        "items": [{"item": str(i.get("item")), "versions": {str(k): str(v) for k, v in
                                                              (i.get("versions") or {}).items()}}
                  for i in (cfg.get("items") or []) if isinstance(i, dict)],
        "show_count": int(cfg.get("show_count") or 1),
        "arm_var": (str(cfg.get("arm_var") or "").strip() or None),
        # The arm can come from the answers instead of the balancing: the name of a
        # question or calculated value whose value is an arm name (section 15 of the spec).
        "arm_from": (str(cfg.get("arm_from") or "").strip() or None),
        "arms": [{"name": str(a.get("name")), "quota": {str(k): int(v) for k, v in
                                                        (a.get("quota") or {}).items()}}
                 for a in (cfg.get("arms") or []) if isinstance(a, dict)],
        "order": cfg.get("order") if cfg.get("order") in ORDERS else "random",
    }


def bank_pages(cfg: dict) -> list:
    return [page for it in cfg["items"] for page in it["versions"].values()]


def propose_items(page_names: list, pattern: str = DEFAULT_ITEM_REGEX) -> tuple[list, list]:
    """Items read off the page names with a regex that has `item` and `version` groups.
    Returns (items, unmatched page names). Order follows the schema."""
    rx = re.compile(pattern)
    items, unmatched, index = [], [], {}
    for name in page_names:
        m = rx.match(name or "")
        if not m or "item" not in m.groupdict() or "version" not in m.groupdict():
            unmatched.append(name)
            continue
        key = m.group("item")
        if key not in index:
            index[key] = {"item": key, "versions": {}}
            items.append(index[key])
        index[key]["versions"][m.group("version")] = name
    return items, unmatched


def pool_combinations(n: int, k: int) -> int:
    from math import comb
    return comb(n, k) if 0 <= k <= n else 0


# --- validation ----------------------------------------------------------------------

def validate(cfg: dict, page_names: list, other_taken: dict, question_names: set,
             pool_vars: set, pages_by_name: dict = None, calculated: dict = None) -> tuple[list, list]:
    """(errors, warnings) for one bank against its survey.

    other_taken: page name -> where it already belongs ("pool X", "bank Y").
    pages_by_name: the schema's pages, for the warnings that look inside them.
    calculated: the schema's calculated values, name -> whether it is saved with the answers.
    """
    errors, warnings = [], []
    names = set(page_names)
    items = cfg["items"]
    k = cfg["show_count"]
    labels = {v for it in items for v in it["versions"]}

    if not cfg["name"]:
        errors.append("the bank needs a name")
    if not items:
        errors.append("the bank has no items")
    if len(items) > MAX_ITEMS:
        errors.append(f"{len(items)} items, above the limit of {MAX_ITEMS}")
    if not 1 <= k <= MAX_SHOW:
        errors.append(f"show_count must be between 1 and {MAX_SHOW}, not {k}")
    if len(cfg["arms"]) > MAX_ARMS:
        errors.append(f"{len(cfg['arms'])} arms, above the limit of {MAX_ARMS}")

    seen_items, seen_pages = set(), {}
    for it in items:
        if it["item"] in seen_items:
            errors.append(f"item {it['item']!r} appears twice")
        seen_items.add(it["item"])
        if not it["versions"]:
            errors.append(f"item {it['item']!r} has no versions")
        if len(it["versions"]) > MAX_VERSIONS:
            errors.append(f"item {it['item']!r} has {len(it['versions'])} versions, above {MAX_VERSIONS}")
        for label, page in it["versions"].items():
            if page not in names:
                errors.append(f"page {page!r} (item {it['item']}, version {label}) is not in the schema")
            if page in seen_pages:
                errors.append(f"page {page!r} is used by two versions ({seen_pages[page]} and {it['item']}/{label})")
            seen_pages[page] = f"{it['item']}/{label}"
            if page in other_taken:
                errors.append(f"page {page!r} already belongs to {other_taken[page]}")

    if items and k > len(items):
        errors.append(f"show_count {k} is larger than the {len(items)} items in the bank")

    if cfg["arms"]:
        if not cfg["arm_var"]:
            errors.append("arms are set but arm_var is not: the drawn arm would have no name to travel under")
        elif cfg["arm_var"] in question_names:
            errors.append(f"arm_var {cfg['arm_var']!r} is also a question name")
        elif cfg["arm_var"] in pool_vars:
            errors.append(f"arm_var {cfg['arm_var']!r} is already a pool's condition variable")
        arm_names = set()
        for arm in cfg["arms"]:
            if arm["name"] in arm_names:
                errors.append(f"arm {arm['name']!r} appears twice")
            arm_names.add(arm["name"])
            total = sum(arm["quota"].values())
            if total != k:
                errors.append(f"arm {arm['name']!r}: the quota sums to {total}, not to show_count {k}")
            for label, n in arm["quota"].items():
                if n < 0:
                    errors.append(f"arm {arm['name']!r}: negative quota for {label!r}")
                if label not in labels:
                    errors.append(f"arm {arm['name']!r} asks for version {label!r}, which no item has")
                have = sum(1 for it in items if label in it["versions"])
                if n > have:
                    errors.append(f"arm {arm['name']!r} needs {n} items in version {label!r}, the bank has {have}")
        used = {label for arm in cfg["arms"] for label, n in arm["quota"].items() if n > 0}
        short = [it["item"] for it in items if not used <= set(it["versions"])]
        if short:
            warnings.append(f"{len(short)} item(s) lack a version the arms use and will be drawn less often: "
                            + ", ".join(short[:10]) + ("…" if len(short) > 10 else ""))

    if cfg.get("arm_from"):
        src = cfg["arm_from"]
        calculated = calculated or {}
        if not cfg["arms"]:
            errors.append("arm_from is set but the bank has no arms to choose from")
        if src == cfg["arm_var"]:
            errors.append(f"arm_from and arm_var are both {src!r}: the arm needs a variable of its own")
        if src in calculated:
            if not calculated[src]:
                errors.append(f"calculated value {src!r} is not saved with the answers "
                              f"(includeIntoResult): the server could not tell which arm was shown")
        elif src not in question_names:
            errors.append(f"arm_from {src!r} is neither a question nor a calculated value of the schema")
        elif pages_by_name is not None:
            first_bank = min((i for i, p in enumerate(page_names) if p in seen_pages), default=None)
            where = _asking_page(pages_by_name, page_names, src)
            if first_bank is not None and where is not None and where >= first_bank:
                errors.append(f"arm_from {src!r} is answered on or after the first page of the bank: "
                              f"the arm would be decided after its pages are due")
        warnings.append("the arm comes from the answers (arm_from), so arms are not balanced: "
                        "their sizes follow the answers")

    if pages_by_name:
        for page in seen_pages:
            p = pages_by_name.get(page) or {}
            if p.get("visibleIf"):
                warnings.append(f"bank page {page!r} has a visibleIf: a drawn page it hides shortens "
                                f"that participant's sample without anyone noticing")
            for el in _questions(p):
                qn = el.get("name") or ""
                if not qn.startswith(page + "_"):
                    warnings.append(f"question {qn!r} on bank page {page!r} does not start with "
                                    f"'{page}_': the long export cannot line it up with the other versions")
    return errors, warnings


def _asking_page(pages_by_name: dict, page_names: list, name: str):
    """Index of the page that asks question `name`, or None."""
    for i, page in enumerate(page_names):
        if any(q.get("name") == name for q in _questions(pages_by_name.get(page) or {})):
            return i
    return None


def _questions(page: dict) -> list:
    out = []
    def walk(elements):
        for el in elements or []:
            if el.get("type") == "panel":
                walk(el.get("elements"))
            elif el.get("type") not in ("html", "image", "expression") and el.get("name"):
                out.append(el)
    walk(page.get("elements"))
    return out


# --- loads ---------------------------------------------------------------------------

def loads(db, bank_id: int, pending_minutes: int) -> tuple[dict, dict, dict]:
    """(arm load, item load, (item, version) load): completed draws plus those issued
    within the pending window and not completed — what pools count, for the same reason:
    simultaneous arrivals spread out, and an abandoned start stops counting."""
    rows = db.execute(
        f"""SELECT arm, draw_json FROM bank_draws
            WHERE bank_id = ? AND (completed = 1
                  OR issued_at > datetime('now', '-{int(pending_minutes)} minutes'))""",
        (bank_id,)).fetchall()
    arm_load, item_load, iv_load = {}, {}, {}
    for r in rows:
        draw = json.loads(r["draw_json"])
        if isinstance(draw, dict):
            continue  # a deferred draw not yet resolved: which arm it showed is unknown
        if r["arm"] is not None:
            arm_load[r["arm"]] = arm_load.get(r["arm"], 0) + 1
        for d in draw:
            item_load[d["item"]] = item_load.get(d["item"], 0) + 1
            key = (d["item"], d["version"])
            iv_load[key] = iv_load.get(key, 0) + 1
    return arm_load, item_load, iv_load


# --- allocation ----------------------------------------------------------------------

INF = float("inf")


def hungarian(cost: list) -> list:
    """Minimum-cost perfect assignment for a square matrix: returns col[i] for each row.
    The O(n^3) potentials version; n <= MAX_SHOW, so it costs nothing."""
    n = len(cost)
    big = 1e9
    c = [[(big if x == INF else x) for x in row] for row in cost]
    u, v = [0.0] * (n + 1), [0.0] * (n + 1)
    p, way = [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = c[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    col = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            col[p[j] - 1] = j - 1
    return col


def allocate(cfg: dict, arm_load: dict, item_load: dict, iv_load: dict,
             rng: random.Random = random) -> tuple:
    """One participant's draw: (arm name or None, [{"item", "version", "page"}] in order).

    1. arm: the least-loaded, ties at random;
    2. items: the k least-exposed among those that can serve the arm, ties at random;
    3. versions: a minimum-cost assignment of those items to the quota's slots, the cost
       of (item, version) being its load plus a jitter in (0, 1) that breaks ties;
    4. order: a random permutation, or schema order.
    """
    k = cfg["show_count"]
    arm = None
    quota = None
    if cfg["arms"]:
        low = min(arm_load.get(a["name"], 0) for a in cfg["arms"])
        arm_cfg = rng.choice([a for a in cfg["arms"] if arm_load.get(a["name"], 0) == low])
        arm, quota = arm_cfg["name"], {lb: n for lb, n in arm_cfg["quota"].items() if n > 0}

    items = cfg["items"]
    if quota:
        able = [it for it in items if set(quota) <= set(it["versions"])]
        # an item with only some of the quota's versions can still fill those slots
        partial = [it for it in items if it not in able and set(quota) & set(it["versions"])]
    else:
        able, partial = list(items), []
    keyed = lambda pool: sorted(pool, key=lambda it: (item_load.get(it["item"], 0), rng.random()))
    chosen = keyed(able)[:k]
    if len(chosen) < k:
        chosen += keyed(partial)[: k - len(chosen)]

    if quota:
        slots = [lb for lb, n in sorted(quota.items()) for _ in range(n)]
        cost = [[(iv_load.get((it["item"], lb), 0) + rng.random()) if lb in it["versions"] else INF
                 for lb in slots] for it in chosen]
        col = hungarian(cost)
        if any(cost[i][col[i]] == INF for i in range(len(chosen))):
            # the least-exposed items cannot cover the quota (some lack a version):
            # fill each version's slots from the items that have it, least loaded first
            draw, taken = [], set()
            for lb in sorted(quota, key=lambda x: sum(1 for it in items if x in it["versions"])):
                pool = keyed([it for it in items if lb in it["versions"] and it["item"] not in taken])
                for it in pool[: quota[lb]]:
                    taken.add(it["item"])
                    draw.append({"item": it["item"], "version": lb, "page": it["versions"][lb]})
        else:
            draw = [{"item": it["item"], "version": slots[col[i]],
                     "page": it["versions"][slots[col[i]]]} for i, it in enumerate(chosen)]
    else:
        draw = []
        for it in chosen:
            lb = min(it["versions"], key=lambda x: (iv_load.get((it["item"], x), 0), rng.random()))
            draw.append({"item": it["item"], "version": lb, "page": it["versions"][lb]})

    if cfg["order"] == "random":
        rng.shuffle(draw)
    else:
        position = {page: n for n, page in enumerate(bank_pages(cfg))}
        draw.sort(key=lambda d: position[d["page"]])
    return arm, draw


def allocate_deferred(cfg: dict, arm_load: dict, item_load: dict, iv_load: dict,
                      rng: random.Random = random) -> dict:
    """For a bank whose arm comes from the answers: one draw per arm, {arm: draw}. The
    participant has not answered yet, so every arm gets its draw now and the answer picks
    one of them; nothing outside these draws can ever be shown."""
    out = {}
    for arm in cfg["arms"]:
        _, draw = allocate(dict(cfg, arms=[arm]), {}, item_load, iv_load, rng=rng)
        out[arm["name"]] = draw
    return out


def assign(db, bank_id: int, cfg: dict, pending_minutes: int, rng: random.Random = random) -> dict:
    """Draw for one participant and record it as pending. One IMMEDIATE transaction, so
    two arrivals cannot read the same loads and take the same least-used items.

    With arm_from the draw is one per arm ({"by_arm": ...}) and the arm stays empty until
    the submit resolves it from the answers (resolve, below)."""
    db.commit()  # close any implicit transaction before taking the write lock
    db.execute("BEGIN IMMEDIATE")
    try:
        if cfg.get("arm_from"):
            arm, draw = None, None
            by_arm = allocate_deferred(cfg, *loads(db, bank_id, pending_minutes), rng=rng)
            stored = {"by_arm": by_arm}
        else:
            arm, draw = allocate(cfg, *loads(db, bank_id, pending_minutes), rng=rng)
            by_arm, stored = None, draw
        cur = db.execute("INSERT INTO bank_draws (bank_id, arm, draw_json) VALUES (?, ?, ?)",
                         (bank_id, arm, json.dumps(stored)))
        # housekeeping, as for pools: a pending draw older than a week will never complete
        db.execute("DELETE FROM bank_draws WHERE bank_id = ? AND completed = 0 "
                   "AND issued_at < datetime('now', '-7 days')", (bank_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"draw_id": cur.lastrowid, "arm": arm, "draw": draw, "by_arm": by_arm}


def resolve(db, draw_id: int, arm) -> bool:
    """Fix a deferred draw to the arm the answers chose: the draw keeps only that arm's
    pages, as if it had been drawn alone. An arm the draw does not have resolves nothing,
    so the response records no bank rather than one it was not shown. Returns whether the
    draw is now resolved (a draw that never was deferred counts as resolved)."""
    row = db.execute("SELECT draw_json FROM bank_draws WHERE id = ?", (draw_id,)).fetchone()
    if not row:
        return False
    stored = json.loads(row["draw_json"])
    if not isinstance(stored, dict):
        return True
    draw = (stored.get("by_arm") or {}).get(str(arm)) if arm is not None else None
    if draw is None:
        return False
    db.execute("UPDATE bank_draws SET arm = ?, draw_json = ? WHERE id = ?",
               (str(arm), json.dumps(draw), draw_id))
    return True


def example_draw(cfg: dict) -> dict:
    """A draw from an empty history with a fixed seed, for previews: it says what a draw
    looks like, not what the next participant will get."""
    rng = random.Random(0)
    out = []
    for arm in (cfg["arms"] or [None]):
        one = dict(cfg)
        if arm:
            one["arms"] = [arm]
        a, d = allocate(one, {}, {}, {}, rng=rng)
        out.append({"arm": a, "draw": d})
    return out


# --- what a response keeps -----------------------------------------------------------

def response_record(name: str, draw_id: int, arm, draw: list) -> dict:
    """The record a response carries of what it was shown. Parallel lists rather than a
    list of objects, so the flat export turns each into one readable column."""
    return {"arm": arm, "draw_id": draw_id,
            "items": [d["item"] for d in draw],
            "versions": [d["version"] for d in draw],
            "pages": [d["page"] for d in draw]}


# --- status --------------------------------------------------------------------------

def status(db, bank_id: int, cfg: dict, pending_minutes: int) -> dict:
    """Balance in numbers, completed and in flight, without any response content."""
    rows = db.execute("SELECT arm, draw_json, completed, issued_at > datetime('now', ?) AS live "
                      "FROM bank_draws WHERE bank_id = ?",
                      (f"-{int(pending_minutes)} minutes", bank_id)).fetchall()
    arms = {a["name"]: {"completed": 0, "in_progress": 0} for a in cfg["arms"]}
    exposure = {it["item"]: 0 for it in cfg["items"]}
    iv = {(it["item"], lb): 0 for it in cfg["items"] for lb in it["versions"]}
    completed = 0
    for r in rows:
        if isinstance(json.loads(r["draw_json"]), dict):
            continue  # deferred and never resolved: nothing is on record as shown
        if r["completed"]:
            completed += 1
            if r["arm"] in arms:
                arms[r["arm"]]["completed"] += 1
            for d in json.loads(r["draw_json"]):
                exposure[d["item"]] = exposure.get(d["item"], 0) + 1
                iv[(d["item"], d["version"])] = iv.get((d["item"], d["version"]), 0) + 1
        elif r["live"] and r["arm"] in arms:
            arms[r["arm"]]["in_progress"] += 1
    spread = lambda xs: (max(xs) - min(xs)) if xs else 0
    worst = 0
    for it in cfg["items"]:
        counts = [iv[(it["item"], lb)] for lb in it["versions"]]
        worst = max(worst, spread(counts))
    return {
        "completed": completed,
        "arms": arms,
        "exposure_min": min(exposure.values(), default=0),
        "exposure_max": max(exposure.values(), default=0),
        "version_imbalance_max": worst,
        "per_item": [{"item": it["item"], "shown": exposure[it["item"]],
                      "versions": {lb: iv[(it["item"], lb)] for lb in it["versions"]}}
                     for it in cfg["items"]],
    }


# --- long export ---------------------------------------------------------------------

def long_rows(responses: list) -> tuple[list, list]:
    """(columns, rows): one row per response x drawn item, with the answers on that
    item's page under their role (the question name without the page prefix)."""
    rows, roles, seen = [], [], set()
    for n, r in enumerate(responses, 1):
        data = json.loads(r["response_json"])
        for bank, rec in (data.get("_banks") or {}).items():
            pages = rec.get("pages") or []
            for pos, page in enumerate(pages):
                row = {"response": r["id"] if "id" in r.keys() else n,
                       "submitted_at": r["submitted_at"], "bank": bank, "arm": rec.get("arm"),
                       "position": pos + 1, "item": (rec.get("items") or [None] * len(pages))[pos],
                       "version": (rec.get("versions") or [None] * len(pages))[pos], "page": page}
                prefix = page + "_"
                for key, value in data.items():
                    if key.startswith(prefix):
                        role = key[len(prefix):]
                        row[role] = ";".join(map(str, value)) if isinstance(value, list) else value
                        if role not in seen:
                            seen.add(role)
                            roles.append(role)
                rows.append(row)
    cols = ["response", "submitted_at", "bank", "arm", "position", "item", "version", "page"] + roles
    return cols, rows
