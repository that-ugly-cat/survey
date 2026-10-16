"""Version pages generated from a template page and a table (docs/item-banks-ui-spec.md,
phase 3).

A bank needs one page per item and version. Written by hand that is dozens of pages; the
generator takes one template page whose texts carry placeholders in double braces
({{text}}, {{source}}) and a table with one row per page: the columns `item` and
`version`, and one column per placeholder. Each row becomes a page named
<item>_<version>, its questions renamed <page>_<role> as the long export expects.

This is generation at authoring time: the pages are written into the schema once and are
ordinary pages from then on. Runtime templating was set aside for good reasons
(item-banks-spec section 2) and is not revisited here.

Plain functions over a schema dict; the admin route calls them, the tests too.
"""
import copy
import csv
import io
import re

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")  # no underscore: it splits page names
MAX_ROWS = 2000


def parse_table(text: str) -> tuple:
    """(columns, rows, errors) from pasted spreadsheet cells (tabs) or CSV (commas or
    semicolons). The first line is the header; names are trimmed and lower-cased for
    `item` and `version` only, since placeholders are case-sensitive."""
    text = (text or "").lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], [], [("empty_table", "the table is empty")]
    head = lines[0]
    delimiter = "\t" if "\t" in head else (";" if head.count(";") > head.count(",") else ",")
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    raw = list(reader)
    columns = [c.strip() for c in raw[0]]
    columns = [c.lower() if c.lower() in ("item", "version") else c for c in columns]
    errors = []
    for need in ("item", "version"):
        if need not in columns:
            errors.append(("missing_column", f"the table needs a column called {need}"))
    if len(set(columns)) != len(columns):
        errors.append(("duplicate_column", "two columns have the same name"))
    rows = []
    for n, values in enumerate(raw[1:], start=2):
        if len(values) > len(columns):
            errors.append(("row_too_long", f"line {n} has more cells than the header"))
            continue
        values = values + [""] * (len(columns) - len(values))
        rows.append({c: v.strip() for c, v in zip(columns, values)})
    if len(rows) > MAX_ROWS:
        errors.append(("too_many_rows", f"{len(rows)} rows, above the limit of {MAX_ROWS}"))
    return columns, rows, errors


def placeholders(page: dict) -> list:
    """The placeholder names a page uses, in order of appearance."""
    found = []
    def walk(v):
        if isinstance(v, str):
            for name in PLACEHOLDER.findall(v):
                if name not in found:
                    found.append(name)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(page)
    return found


def _questions(elements):
    for el in elements or []:
        if el.get("type") == "panel":
            yield el
            yield from _questions(el.get("elements"))
        elif el.get("name"):
            yield el


def _role(question_name: str, template_name: str) -> str:
    """The part of a template question's name that every version shares: without the
    template page's name in front, if it is there."""
    prefix = template_name + "_"
    return question_name[len(prefix):] if question_name.startswith(prefix) else question_name


def _fill(value, row: dict, renames: dict):
    if isinstance(value, str):
        out = PLACEHOLDER.sub(lambda m: row.get(m.group(1), m.group(0)), value)
        for old, new in renames.items():
            out = out.replace("{" + old + "}", "{" + new + "}")  # conditions inside the page
        return out
    if isinstance(value, dict):
        return {k: _fill(v, row, renames) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, row, renames) for v in value]
    return value


def make_page(template: dict, row: dict) -> dict:
    """One version page from the template and a table row."""
    name = f"{row['item']}_{row['version']}"
    renames = {}
    page = copy.deepcopy(template)
    for el in _questions(page.get("elements")):
        renames[el["name"]] = f"{name}_{_role(el['name'], template['name'])}"
    page = _fill(page, row, renames)
    page["name"] = name
    for el in _questions(page.get("elements")):
        el["name"] = renames.get(el["name"], el["name"])
    return page


def plan(schema: dict, template_name: str, table_text: str, taken_pages: dict = None) -> dict:
    """Everything the generator would do, without doing it: the pages, the items they
    form, and the problems that stop it (errors) or that it should say (notes)."""
    taken_pages = taken_pages or {}
    pages = schema.get("pages", [])
    template = next((p for p in pages if p.get("name") == template_name), None)
    errors, notes = [], []
    if template is None:
        return {"errors": [("unknown_template", f"{template_name!r} is not a page of the questionnaire")],
                "notes": [], "pages": [], "items": [], "placeholders": []}
    if template_name in taken_pages:
        errors.append(("template_in_use", f"the template page belongs to {taken_pages[template_name]}"))
    used = placeholders(template)
    columns, rows, table_errors = parse_table(table_text)
    errors += table_errors
    extra = [c for c in columns if c not in ("item", "version") and c not in used]
    missing = [p for p in used if p not in columns]
    if missing:
        errors.append(("missing_placeholder", "no column for " + ", ".join("{{" + m + "}}" for m in missing)))
    if extra:
        notes.append("columns not used by the template: " + ", ".join(extra))
    if not used:
        notes.append("the template has no {{placeholders}}: every page will have the same text")

    existing_pages = {p.get("name") for p in pages}
    existing_questions = {q.get("name") for p in pages for q in _questions(p.get("elements"))}
    seen, out_pages, items, index = set(), [], [], {}
    if not any(code in ("missing_column", "empty_table") for code, _ in table_errors):
        for n, row in enumerate(rows, start=2):
            item, version = row.get("item", ""), row.get("version", "")
            if not ITEM_RE.match(item):
                errors.append(("bad_item", f"line {n}: item {item!r} must be letters, digits, - or _"))
                continue
            if not VERSION_RE.match(version):
                errors.append(("bad_version", f"line {n}: version {version!r} must be letters, digits or - (no _)"))
                continue
            if (item, version) in seen:
                errors.append(("duplicate_row", f"line {n}: {item}/{version} appears twice"))
                continue
            seen.add((item, version))
            page = make_page(template, row)
            if page["name"] in existing_pages and page["name"] != template_name:
                errors.append(("page_exists", f"line {n}: a page called {page['name']} already exists"))
                continue
            clash = [q["name"] for q in _questions(page.get("elements")) if q["name"] in existing_questions]
            if clash:
                errors.append(("question_exists", f"line {n}: question {clash[0]} already exists"))
                continue
            out_pages.append(page)
            if item not in index:
                index[item] = {"item": item, "versions": {}}
                items.append(index[item])
            index[item]["versions"][version] = page["name"]
    return {"errors": errors, "notes": notes, "pages": out_pages, "items": items, "placeholders": used}


def apply(schema: dict, template_name: str, pages: list, remove_template: bool) -> dict:
    """A new schema with the generated pages after the template, and the template
    itself removed when asked. The schema passed in is not changed."""
    new = copy.deepcopy(schema)
    old = new.get("pages", [])
    at = next(i for i, p in enumerate(old) if p.get("name") == template_name)
    new["pages"] = old[:at] + ([] if remove_template else [old[at]]) + copy.deepcopy(pages) + old[at + 1:]
    return new
