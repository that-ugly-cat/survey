# Item banks — a visual editor

*16 October 2026. Status: design, not implemented. Mockup: `docs/mockups/item-banks-ui.html` (open it
in a browser; it is static, with just enough script to switch tabs and check sums).*

## 1. The problem

The engine is complete (`docs/item-banks-spec.md`), and the admin page exposes it, but in a form only
an author who writes JSON can use:

- **Items and arms are JSON text** in two textareas. A typo is caught at save, with a line number, and
  nothing guides the shape.
- **Version pages exist only in the schema**, which is edited as JSON. A bank of 48 posts in one version
  each, or 80 passages in two, means generating the pages with a script.
- **The arm-from-an-answer source** (`arm_from`) usually reads a calculated value, and calculated values
  live only in the schema.

Two studies have used banks so far (the AI coding Turing test and PROMETEO); both were built by a script
and an assistant. The aim is that a colleague can build the same design from the browser, without JSON
and without breaking what the engine guarantees.

## 2. Principles

1. **The JSON stays the truth.** The editor reads a config and writes a config; nothing new is stored.
   An *Edit as JSON* switch shows the same two textareas as today, and switching back re-reads them.
2. **The server judges.** Every check the engine already makes (`flow.bank_findings`) is shown in place,
   from the server, while editing. The browser adds only the arithmetic it can see (quota sums).
3. **Show what a participant gets**, not only the configuration: an example draw per arm is always on
   screen.
4. **Writing the schema is a separate act.** The bank editor never changes the questionnaire silently.
   The two features that do (generating pages, adding a calculated value) say what they will add, and are
   refused once responses exist, as the schema edit already is.
5. **No new dependencies.** Plain HTML, the admin's CSS, a few hundred lines of script, as in the rest
   of the admin.

## 3. Layout

The bank card keeps its place on the Randomization page. Inside it, four tabs and a side panel.

```
┌ Item bank: Post ─────────────────────────── [Edit as JSON] ┐
│ ① Content  ② Draw  ③ Groups  ④ Check & save   │ Example draw │
│                                                │ per arm      │
│   (tab body)                                   │ (always      │
│                                                │  visible)    │
└────────────────────────────────────────────────┴──────────────┘
```

The tab labels carry a state: a check mark when the tab has no error, a count of errors otherwise.

### ① Content: items and versions

A **grid**: one row per item, one column per version label, each cell the page that renders that item
in that version.

- **Cells** show the page name as a chip; an empty cell is a dash. Clicking a cell opens a list of the
  schema's pages not yet used by a bank or a pool, filterable by typing. A page used elsewhere appears
  greyed, with where it belongs.
- **Columns** are version labels. *Add version* adds a column; the header is editable in place; a column
  that is empty everywhere can be removed.
- **Rows** can be added, removed, reordered by drag (order matters only for `order: fixed`).
- **Fill from page names** is today's *Propose from page names*: it fills the grid from a naming pattern.
  The pattern is preset (`ITEM_VERSION`) with a plain-language choice of separator; the regex is visible
  under *Advanced*.
- **Missing versions** are shaded, with the warning the engine gives ("drawn less often") next to the
  row count, so an incomplete item is visible before save, not only after.
- **Search** filters rows; a counter says *48 items · 3 versions · 2 cells empty*.

**Generate pages from a table** (button above the grid) covers the case where the pages do not exist
yet, which is the case every time so far. It is a three-step dialog:

1. **Template page**: any page of the schema. Text inside it can carry placeholders in double braces
   (`{{text}}`, `{{source}}`) in titles, descriptions and HTML.
2. **Table**: paste from a spreadsheet or upload a CSV. Required columns `item` and `version`; every
   other column fills the placeholder of the same name. Unknown placeholders and unused columns are
   listed.
3. **Preview and add**: three generated pages rendered as the participant sees them; the names that will
   be created (`<item>_<version>`, questions `<page>_<role>`, the convention the long export expects); the
   count; where they will be inserted (after the template, by default). *Add N pages* writes the schema and
   fills the grid. The template page itself is left untouched, so it can be removed or kept as a preview.

This is generation at authoring time, not templating at runtime: section 2 of the engine spec set the
latter aside for good reasons, and nothing here revisits them. Each version is still a page.

### ② Draw

- **Items per participant** (`show_count`): a stepper, with *of 48* beside it.
- **Order**: *random for each participant* or *as in the questionnaire*.
- One line of consequence: *each participant answers 20 of 48 items; with 300 participants each item is
  shown about 125 times.* The sample size is a field the author can change; it is used only for this line.

### ③ Groups (arms)

A switch at the top: **No groups** (versions balanced item by item) or **Groups with quotas**.

With groups, a **quota table**: one row per arm, one column per version label (the labels of tab ①), a
number in each cell, and a last column with the row's total against *Items per participant*, green when
it matches, red with the difference otherwise. *Add group*; the arm name is editable in place.

**Group variable** (`arm_var`): a name with a default (`group`), and a line saying where it can be used
(*available in visibleIf as {group}*).

**How a participant gets a group** — three choices, as cards:

1. **Balanced by the platform** (default). The least-used group goes to the next participant.
2. **From an answer.** A list of the questions and calculated values that come *before the bank's first
   page* and are saved with the answers; the ones that do not qualify are listed below it, greyed, each
   with the reason (*asked after the bank*, *not saved with the answers*). A note says that group sizes
   then follow the answers.
3. **Rotation across waves** — a helper for the case that prompted `arm_from`. It asks for the question
   that every wave shares (for example the day of birth of the personal code), the **wave number of this
   questionnaire**, and the number of forms (the groups). It shows:
   - the rotation table (*wave 1 → wave 2 → wave 3* for each starting form);
   - the expected split: for a day of birth, the share per form weighted by how often each day occurs in
     a year (the 31st exists in seven months), which gives 32.6 / 32.6 / 34.8% rather than a third each;
   - the calculated value it will add, in words and as an expression.

   *Add to questionnaire* writes the calculated value (`includeIntoResult: true`) and sets
   `arm_from` to it. For the next wave, the author copies the survey and changes only the wave number,
   here.

### ④ Check & save

- **Findings** from the server, errors first, each with a link to the tab and cell it concerns.
  Errors disable *Save*; warnings need to be read, not acknowledged one by one.
- **What saving does**, stated before the button: *saving resets the draws of this bank (12 draws, 0
  responses)*. With responses on record, the button changes to *Save and reset draws* and asks for the
  bank name to be typed, as deleting does elsewhere.
- **Preview the walk**: a link that opens the flow preview (`preview_flow`) for each arm.

### Side panel: example draw

Always visible, refreshed after each change (debounced): for each arm, the drawn pages as chips in
order, coloured by version, with the item name. It is labelled *example: a draw from an empty history,
not the next participant's*, the same wording as the MCP. For an `arm_from` bank it shows one draw per
possible answer value.

### After fielding: balance

The existing status (completed and in-progress per arm, exposure range, largest version gap) becomes a
small panel under the card: a bar per arm, a strip chart of per-item exposure, and the items with the
largest version gap listed first. Counts only, as today; no response content.

## 4. What the server needs

| Endpoint | Purpose | Writes |
|---|---|---|
| `GET /admin/surveys/{slug}/randomization/bank/{id}/model` | the config, the schema's pages (name, title, owner pool or bank), questions and calculated values with their page index, response and draw counts | — |
| `POST …/bank/{id}/check` | a config in, `flow.bank_findings` and `banks.example_draw` out; nothing saved | — |
| `POST …/bank/{id}/save` | as today, accepting JSON as well as the form | bank config |
| `POST /admin/surveys/{slug}/generate-pages` | template page + table → pages inserted into the schema | schema |
| `POST /admin/surveys/{slug}/calculated-values` | add or replace one calculated value | schema |

The two schema writes go through the same guard as the schema editor (`test_edit_guard.py`): refused
once the survey holds responses. `check` and `model` are read-only and owner-scoped like every admin
route. The MCP gains nothing it lacks: `set_bank`, `validate_survey` and `update_schema` already cover
these operations for an assistant.

## 5. What it does not do

- It does not edit page content. The template page is edited where the schema is edited.
- It does not let a page belong to two banks or to a bank and a pool; it shows why a page is unavailable.
- It does not hide the JSON: the switch is always one click away, and both views edit the same object.

## 6. Phases

1. **Grid, quotas, assignment choices 1–2, check endpoint, side panel.** No schema writes. Replaces the
   textareas for every bank that already has its pages. Tests: the round trip editor → config → editor is
   lossless on the Turing-test and PROMETEO configs.
2. **Rotation helper** (schema write of one calculated value).
3. **Page generator** (schema write of many pages), with the preview.
4. **Balance panel** after fielding.

## 7. Open questions

- Should the generator also create the *other* versions of an item from one row (for example a version
  column per row instead of a row per version)? The table shape `item, version, …` is the simplest and
  matches the export; a wide shape could be accepted and converted.
- Copying a survey for the next wave is today done by hand (create, paste the schema, recreate banks).
  A *Duplicate survey* action would make the rotation helper complete; it belongs to the survey page,
  not to this editor.
