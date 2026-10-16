"""Checks for review_export.py: that every question type the platform offers
reaches the review document, and that the four table-shaped ones say what they
actually ask.

No database and no server. This renders the bytes and reads them back with
python-docx, so each assertion is made against the document a reviewer opens
rather than against the code that wrote it.
"""
import io, os, sys

sys.path.insert(0, os.path.abspath("."))
from docx import Document

import review_export

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))

# 1x1 transparent SVG, small enough to keep the schema readable
PIXEL = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="


def render(elements, pools=()):
    """Render one page of elements and return (paragraphs, tables) as text."""
    schema = {"title": "T", "pages": [{"name": "p1", "title": "Page", "elements": elements}]}
    doc = Document(io.BytesIO(review_export.build_review_docx(schema, list(pools), "T")))
    paras = [p.text for p in doc.paragraphs]
    tables = [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables]
    return paras, tables


def has(paras, needle):
    return any(needle in p for p in paras)


def show(paras, label, keep):
    """Print the lines an assertion is about, so a silent pass is visible as a
    pass over something rather than over nothing."""
    found = [p for p in paras if any(k in p for k in keep)]
    print(f"    [{label}] " + (" | ".join(found) if found else "nothing matched"))
    return found


print("\n--- ranking ---")
paras, _ = render([{
    "type": "ranking", "name": "priorita", "title": "Put these in order",
    "choices": [{"value": "costo", "text": "Cost"},
                {"value": "privacy", "text": "Privacy"},
                {"value": "export", "text": "Export"}],
}])
show(paras, "ranking", ["Answer:", "↕", "export stores"])
ok(has(paras, "drag every option into order of preference"), "says how the answer is given")
ok(all(has(paras, f"↕  {t}") for t in ("Cost", "Privacy", "Export")),
   "lists every option with the drag marker")
ok(has(paras, "the order the participant left them"), "says what the export stores")

paras, _ = render([{
    "type": "ranking", "name": "priorita", "title": "Rank the ones that matter",
    "selectToRankEnabled": True,
    "choices": ["Cost", "Privacy"],
}])
ok(has(paras, "the rest stay unranked"),
   "select-to-rank is a different instruction, not the same one")
ok(not has(paras, "drag every option into order"), "and not both at once")

print("\n--- imagepicker ---")
paras, _ = render([{
    "type": "imagepicker", "name": "figura", "title": "Pick one",
    "choices": [
        {"value": "a", "text": "Diagram A", "imageLink": "/uploads/demo/a.png"},
        {"value": "b", "text": "Diagram B", "imageLink": PIXEL},
        {"value": "c", "text": "Diagram C"},
    ],
}])
show(paras, "imagepicker", ["Answer:", "▫"])
ok(has(paras, "choose one image."), "single choice by default")
ok(has(paras, "/uploads/demo/a.png"), "an uploaded file is named, so a dead link is reviewable")
ok(has(paras, "inline image (data URI)") and not has(paras, "PHN2ZyB4bWxu"),
   "a data URI is reported as one instead of dumping the base64")
ok(has(paras, "no image set"), "an option with no image says so rather than looking fine")

paras, _ = render([{
    "type": "imagepicker", "name": "figure", "title": "Pick some",
    "multiSelect": True, "contentMode": "video",
    "choices": [{"value": "a", "text": "Clip A", "imageLink": "/uploads/demo/a.mp4"}],
}])
ok(has(paras, "choose one or more videos."), "multi-select and video are both read from the schema")

print("\n--- matrixdropdown ---")
paras, tables = render([{
    "type": "matrixdropdown", "name": "valutazione", "title": "Rate each tool",
    "choices": [{"value": 1, "text": "Poor"}, {"value": 2, "text": "Good"}],
    "columns": [
        {"name": "qualita", "title": "Quality", "cellType": "rating", "isRequired": True},
        {"name": "uso", "title": "Would use", "cellType": "radiogroup"},
        {"name": "nota", "title": "Note", "cellType": "comment"},
    ],
    "rows": [{"value": "survey", "text": "Survey"}, {"value": "autocode", "text": "AutoCode"}],
}])
grid = tables[0]
print(f"    [grid] {grid}")
show(paras, "legend", ["Quality —", "Would use —", "Note —"])
ok(grid[0] == ["", "Quality", "Would use", "Note"], "column titles head the grid")
ok([r[0] for r in grid[1:]] == ["Survey", "AutoCode"], "rows are the statements, in order")
ok(grid[1][1] == "1–5" and grid[1][2] == "○" and grid[1][3] == "▭",
   "each cell carries the shape of its own column, not one shape for the table")
ok(has(paras, "Quality — rating scale") and has(paras, "(required)"),
   "the legend names the format and the required column")
ok(has(paras, "Would use — one answer: Poor; Good"),
   "a column with no choices of its own inherits the question's, and the legend shows them")
ok(not has(paras, "Note — one answer"), "a text column is not described as a choice")

paras, _ = render([{
    "type": "matrixdropdown", "name": "m", "title": "M",
    "columns": [{"name": "c", "title": "C", "choices": [{"value": 1, "text": "Own choice"}]}],
    "choices": [{"value": 9, "text": "Inherited"}],
    "rows": ["r1"],
}])
ok(has(paras, "C — drop-down: Own choice") and not has(paras, "Inherited"),
   "a column's own choices win over the question's")

print("\n--- matrixdynamic ---")
paras, tables = render([{
    "type": "matrixdynamic", "name": "pubblicazioni", "title": "List your papers",
    "rowCount": 3, "maxRowCount": 5, "cellType": "text",
    "columns": [{"name": "titolo", "title": "Title"},
                {"name": "anno", "title": "Year", "cellType": "text"}],
}])
print(f"    [grid] {tables[0]}")
show(paras, "matrixdynamic", ["Answer:"])
ok(has(paras, "starting with 3 rows and capped at 5"),
   "says how many rows there are to start and where the ceiling is")
ok(tables[0][0] == ["", "Title", "Year"], "column titles head the grid")
ok(len(tables[0]) == 4, "three specimen rows under the header")
ok(has(paras, "Title — short text field"), "cellType falls through from the question")

paras, _ = render([{
    "type": "matrixdynamic", "name": "m", "title": "M", "rowCount": 1,
    "columns": [{"name": "c", "title": "C"}],
}])
ok(has(paras, "starting with 1 row.") and not has(paras, "capped"),
   "one row is singular, and no ceiling is not reported as one")

print("\n--- nothing falls through ---")
every = [
    {"type": "html", "name": "h", "html": "<p>Read this.</p>"},
    {"type": "text", "name": "t", "title": "T"},
    {"type": "comment", "name": "c", "title": "C"},
    {"type": "radiogroup", "name": "r", "title": "R", "choices": ["a"]},
    {"type": "checkbox", "name": "ch", "title": "Ch", "choices": ["a"]},
    {"type": "dropdown", "name": "d", "title": "D", "choices": ["a"]},
    {"type": "tagbox", "name": "tb", "title": "Tb", "choices": ["a"]},
    {"type": "boolean", "name": "b", "title": "B"},
    {"type": "rating", "name": "ra", "title": "Ra"},
    {"type": "matrix", "name": "mx", "title": "Mx", "columns": ["1"], "rows": ["r"]},
    {"type": "matrixdropdown", "name": "md", "title": "Md",
     "columns": [{"name": "c", "title": "C"}], "rows": ["r"], "choices": ["a"]},
    {"type": "matrixdynamic", "name": "mdy", "title": "Mdy",
     "columns": [{"name": "c", "title": "C"}]},
    {"type": "ranking", "name": "rk", "title": "Rk", "choices": ["a", "b"]},
    {"type": "imagepicker", "name": "ip", "title": "Ip",
     "choices": [{"value": "a", "imageLink": "/uploads/x/a.png"}]},
    {"type": "expression", "name": "e", "title": "E", "expression": "1 + 1"},
    {"type": "panel", "name": "pn", "title": "Pn",
     "elements": [{"type": "ranking", "name": "inner", "title": "Inner", "choices": ["a"]}]},
    {"type": "paneldynamic", "name": "pd", "title": "Pd",
     "templateElements": [{"type": "imagepicker", "name": "inner2", "title": "Inner2",
                           "choices": [{"value": "a", "imageLink": PIXEL}]}]},
]
paras, _ = render(every)
fallthrough = [p for p in paras if "not rendered here" in p]
print(f"    [fallthrough] {fallthrough or 'none'}")
ok(not fallthrough, "every type this platform offers is rendered, none hits the fallback")
ok(has(paras, "drag every option into order of preference")
   and has(paras, "inline image (data URI)"),
   "including the ones nested inside a panel and a repeating panel")

paras, _ = render([{"type": "signaturepad", "name": "s", "title": "Sign"}])
ok(has(paras, "Question type 'signaturepad' not rendered here"),
   "and a type nobody taught it still says so, so the check above means something")

print("\n--- item banks ---")


def render_pages(pages, pools=(), banks=()):
    """Render several pages, with pools and banks beside the schema."""
    schema = {"title": "T", "pages": pages}
    doc = Document(io.BytesIO(review_export.build_review_docx(
        schema, list(pools), "T", list(banks))))
    return [p.text for p in doc.paragraphs]


import banks as bank_lib

pages = [{"name": "intro", "elements": [{"type": "html", "name": "h", "html": "Hi"}]}] + [
    {"name": f"P{i}_{v}", "elements": [{"type": "text", "name": f"P{i}_{v}_q", "title": "Q"}]}
    for i in (1, 2, 3) for v in ("H", "L")]
two_versions = bank_lib.normalise({
    "name": "passages", "show_count": 2, "order": "random", "arm_var": "arm",
    "items": [{"item": f"P{i}", "versions": {"H": f"P{i}_H", "L": f"P{i}_L"}} for i in (1, 2, 3)],
    "arms": [{"name": "mostlyH", "quota": {"H": 2}}, {"name": "even", "quota": {"H": 1, "L": 1}}],
})
paras = render_pages(pages, banks=[two_versions])
show(paras, "bank", ["Item bank", "Randomization"])
ok("Randomization" in paras, "a bank alone opens the Randomization section")
ok(has(paras, "Item bank “passages”: shows 2 of 3 items, in a fresh random order"),
   "the section says how many items of how many, and the order")
ok(has(paras, "versions “H”, “L”") and has(paras, "“even” (1 H, 1 L)")
   and has(paras, "stored in the variable “arm”"),
   "and the versions, the arms with their quotas, and the arm variable")
ok(has(paras, "▸ Item bank “passages”, item “P2”, version “L”: shown only if drawn. "
              "Each participant sees 2 of the 3 items"),
   "each bank page says which item and version it is, and that it is drawn")
ok(sum("▸ Item bank" in p for p in paras) == 6, "every bank page gets a note, the intro none")

one_version = bank_lib.normalise({
    "name": "coasters", "show_count": 1, "order": "fixed",
    "items": [{"item": f"P{i}_H", "versions": {"v": f"P{i}_H"}} for i in (1, 2, 3)],
})
paras = render_pages(pages, banks=[one_version])
show(paras, "single version", ["Item bank"])
ok(has(paras, "shows 1 of 3 items, in the order they have in this document")
   and not has(paras, "comes in versions"),
   "a bank without versions does not talk about versions")
ok(has(paras, "▸ Item bank “coasters”, item “P1_H”: shown only if drawn. Each participant "
              "sees 1 of the 3 items in this bank.") and not has(paras, "version “v”"),
   "and its page notes name the item only, with no claim about position")

follows = bank_lib.normalise({**two_versions, "arm_from": "form"})
paras = render_pages(pages, banks=[follows])
ok(has(paras, "The arm is not drawn: it follows the answer to “form”"),
   "an arm taken from an answer is described as such, not as balanced")

paras = render_pages(pages)
ok("Randomization" not in paras and not has(paras, "Item bank"),
   "with no pools and no banks there is still no Randomization section")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
