# Report — design

A **report** is a page of live results for one survey: an ordered document of blocks, each one
either text you wrote or a chart of one question's answers, served to three audiences at three
levels of disclosure.

This is a design document, not a user guide. It fixes the decisions that would otherwise be
made three times over — once in the aggregation module, once in the editor, once in the
renderer — and says what is deliberately left out of the first version.

---

## 1. The object

**One report per survey.** It is a structure, not a copy of the data: a JSON column
`report_json` on `surveys`, in the same additive style as `condition_map` and `page_order`.
The numbers live in `responses` and are computed on request.

```jsonc
{
  "audience": "owner",          // how far this report may reach: owner | respondent | public
  "updated_at": "2026-09-21",
  "blocks": [
    {"kind": "text", "audience": null,                        // null = inherit the report's
     "md": {"default": "## What we found", "it": "## Cosa abbiamo trovato"}},
    {"kind": "all_questions", "audience": "public"},
    {"kind": "question", "name": "ruolo", "audience": "owner",
     "chart": "bar", "value": "percent", "sort": "frequency", "other": "show"}
  ]
}
```

Three block kinds and no more:

- **`text`** — markdown, **one string per locale**, the same `LANGS` set as
  `templates/survey.html` and `review_export.py`. A bilingual questionnaire with a monolingual
  results page is a half-finished page, and the survey already carries both languages
  everywhere else. Missing locales fall back to `default`, and the editor flags them the way
  the review export flags a missing translation.
- **`question`** — one question, by `name`, plus its display options.
- **`all_questions`** — every question in the schema, in schema order, resolved at render
  time. It is dynamic on purpose: a question added to the questionnaire next week appears by
  itself. This is what the one-click "publish everything" preset inserts, and it is why that
  preset stays correct instead of quietly going stale. **Explode** turns it into the
  equivalent list of `question` blocks, which is how customising starts.

## 2. Audiences

Three, ordered, each seeing strictly less than the one before:

| | `owner` | `respondent` | `public` |
|---|---|---|---|
| closed-choice questions | all | per block | per block |
| open text answers | yes | no | **never** |
| breakdown by randomization arm | yes | no | no |
| n, and how many skipped | yes | yes | yes |
| timing, funnel, abandonment | yes | no | no |
| their own answer marked | — | yes | — |
| cells below the disclosure threshold | shown | suppressed | suppressed |

`report.audience` is the ceiling: a block asking for `public` inside a report set to `owner`
is not published. A block with `audience: null` inherits. Raising the report's ceiling never
raises a block that set its own.

Open text is not a block-level choice at `public`. It is refused at that level, because the
question a results page cannot answer is whether the person who wrote "my consultant at the
Inselspital told me" consented to that sentence being on the open web.

## 3. Disclosure

- **Threshold.** Below 5 responses in a cell, `respondent` and `public` see the cell masked
  rather than counted, and a question whose total is below the threshold renders as *not
  enough responses yet*, with the total. A live public page opened on day one is otherwise a
  blank page that reads as broken.
- **Masking one cell masks nothing.** With the total published, a single hidden cell is a
  subtraction away from being read: `1:0 2:0 3:0 4:? 5:5` out of six is a one. So the hidden
  group grows, smallest first, until it holds at least two cells summing to at least the
  threshold. Cells at zero stay visible, because they name nobody and add nothing to the
  group. (Found by running the module over six real responses, not by thinking about it.)
- **Arms stay private while fielding.** The pooled result is a finding; the breakdown by
  condition is the experimental manipulation, and it is nobody's business until the study is
  written up.
- **`_panel_token`, `_assignment` and `_assignment_ids` never reach any rendered page**, at
  any level, including the owner's. They are reconciliation data, and the owner has the
  export.
- **Anchoring.** Results visible while the survey is open change what later respondents
  answer. Publishing a report on an open survey is allowed and the manage page says the
  consequence in the moment you switch it on, in the same register the schema-edit guard uses.
  Whoever runs the study decides; nobody decides it by accident.
- **Markdown is rendered server-side with raw HTML disabled.** The blocks are owner-authored,
  which is not a reason to leave a script tag's worth of daylight on a public page.

## 4. The mapping

One module owns this. Given a schema element and the raw responses it returns a typed
aggregate; the renderer turns the aggregate into a chart. Nothing else in the codebase is
allowed to decide what a question type means.

| type | stored value | aggregate | default chart |
|---|---|---|---|
| `radiogroup`, `dropdown` | string | counts per choice | horizontal bar, sorted |
| `checkbox`, `tagbox` | array | counts per choice, **% of respondents** | horizontal bar |
| `boolean` | bool | two counts | segmented bar |
| `rating` | number | distribution + mean, median, SD | discrete distribution |
| `matrix` | `{row: col}` | distribution per row | 100% stacked bar per row |
| `matrixdropdown` | `{row: {col: v}}` | per column, recursing on cell type | one panel per column |
| `matrixdynamic`, `paneldynamic` | array of objects | per column over all rows, plus rows-per-respondent | per column, plus a distribution |
| `ranking` | ordered array | position counts per item, mean rank | stacked bar of positions |
| `imagepicker` | string | counts per choice | bar with the thumbnail as label |
| `text` (number), `expression` | number | histogram bins + summary | distribution |
| `text` (date) | date | counts per period | timeline |
| `text` (short) | string | count answered only | none — listed, owner only |
| `comment` | string | count, median length | none — listed, owner only |
| `panel` | — | walked through: its children are the questions | — |
| `html` | — | nothing | — |

Rules that apply across the table:

- Every aggregate carries `n` (answered), `missing` (shown the question, did not answer) and
  `exposed` (reached the question at all). Branching means these differ per question, and a
  percentage without its denominator on a conditional question is a lie told in good faith.
- **Multi-select percentages are of respondents, not of selections**, and the chart says so.
  Selections sum past 100 and everyone misreads them.
- **`showOtherItem` is two fields**: the value `other` and a separate `-Comment`. The
  aggregate keeps them together so a chart does not show a mute "other" slice; the free text
  behind it is open text and obeys §2.
- Labels are resolved from the schema at render time, so the same aggregate renders in EN or
  IT. This works because the export stores **values**, not labels.
- A `question` block naming something the schema no longer has renders as an orphan and says
  so. It is not dropped: a silently shorter report is worse than a visible hole, and the
  schema-edit guard exists for the same reason.

## 5. Liveness

The document is stable, the numbers are live. The server computes aggregates and caches them
per `(survey, filter, audience)`, invalidated on submit; the page re-reads them on an interval
and stamps *updated at*. No websockets, no push: a results page is a document that refreshes,
not an application.

Locale is deliberately **not** part of that key. Aggregates hold stored values and no labels,
so one cached copy serves every language and the label lookup happens at render time against
the schema.

## 6. Rendering

**Chart.js, pinned and served from this repo**, not from a CDN. The page can end up projected
in a lecture hall or inside somebody's iframe, and a network dependency on unpkg is the one
thing that fails exactly there. Consequence accepted: canvas does not save as SVG, so
publication-quality figures stay a separate door for later, and analysis stays where it
already happens.

Five primitives cover the table above: horizontal bar, segmented bar, discrete distribution,
summary strip, timeline. Everything also has a **table view** underneath it, rendered by the
server into the HTML. That table is what makes the page readable with JavaScript off, and it
is the thing a co-author actually copies.

Two rules the drawing follows, both learned by looking at a real page:

- **A chart with nothing above zero is not drawn.** Masked cells come through as holes and
  unchosen options as genuine zeros; a chart made only of those is an empty canvas that reads
  as a bug. It is replaced by a line saying the groups are too small, and the table stays.
- **Counts are whole people**, so an axis that offers 1.4 of one is turned off.

The page's own words — *answered*, *the numbers*, *median length*, the masking notice — live
in a string table with the same four languages as the questionnaire. Without it an Italian
results page reads "12 people wrote something" in English, which is the half-translated page
the locale machinery exists to prevent.

## 7. Routes

```
/admin/surveys/{slug}/report        the editor        owner
/admin/surveys/{slug}/results       the owner's view  owner
            ?as=public|respondent   the same page built for that audience
            ?by={condition var}     split by randomization arm, owner only
/admin/surveys/{slug}/results.json  what the owner's page polls
/s/{slug}/results                   the public view   if report.audience = public
/s/{slug}/results?r={token}         the respondent's view, own answers marked
/s/{slug}/results/embed             the same, without page chrome, for an iframe
/s/{slug}/results.json              what those pages poll
```

`/static/*` serves the vendored Chart.js and, behind the SSO gate, has to be public for the
same reason `/uploads/*` does: a respondent has no account here, and a gated chart library is
a results page that renders its tables and nothing else.

The respondent's token is minted at submit and is **not** the panel token: one identifies a
person to a provider, the other opens a page. Confusing them would put a reconciliation
identifier in a URL people paste to each other.

## 8. The editor

Lives on the manage page beside Randomization and Files. Starts empty, with one button that
inserts the `all_questions` block — the one-click case. Blocks reorder by **dragging and by
up/down buttons**, both: the arrows work from the keyboard and on a phone, the drag is for a
mouse. Both are hand-written, because this app has no build step.

Per block: kind, audience, and for a question block the chart options from §4.

The preview is on the results page rather than in the editor: **looking as** me / a respondent
/ anyone rebuilds the page for that audience through the same filter they would get, with a
banner saying so. Nothing is simulated, which is what makes it worth trusting — it is the only
way to check what the public view exposes without publishing it and opening another browser.

Two notes for whoever writes the renderer:

- **The editor needs JavaScript; the results page must not.** The editor is behind a login and
  belongs to one person, so a JS-driven list posting one JSON payload is a fair trade. The
  page it produces is read by respondents and strangers, and that one works without.
- **A text block written in the editor stores `en`, not `default`.** The schema uses `default`
  for English because SurveyJS does; a block typed fresh has no `default` to inherit. The
  lookup order is therefore the viewer's locale, then `default`, then `en`.

## 9. Not in the first version

- More than one report per survey. The audience levels on blocks cover what several reports
  would, and nobody maintains three documents.
- Cross-tabs and significance tests. This page is for monitoring and for showing; inference
  stays in R.
- Word clouds on open text. They look like analysis and count words. Open answers belong in
  AutoCode, and a button that sends them there is worth more than a picture.
- Figure export for papers. Separate door, matplotlib, later.

## 10. What this touches elsewhere

The aggregation module is also the natural body of an MCP tool (`question_summary`), so the
same numbers reach a conversation without a browser — the reason `preview_flow` exists.

It also raises the stakes on the open `schema_version` item: forcing a schema change over
collected answers now moves a page that strangers may be reading, and nothing in the data
records that it happened.
