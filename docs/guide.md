# Survey — User Guide

Survey is a self-hosted questionnaire platform for academic studies: you write the questionnaire as a JSON schema, share one public link, and download the responses as CSV, Excel or JSON. It runs on a server in Switzerland that the research group operates itself, so no respondent data passes through a commercial survey service. It is built for experimental designs — balanced randomization, condition variables, counterbalanced presentation order. It can also show what came back: a results page you compose, for yourself, for your respondents or for anyone with the link, with small groups masked before they leave your view (§9). Inference stays where you already do it.

---

## 1. Getting in

On survey.borant.eu you sign in once, at **Borant ID**, the shared sign-in for the borant.eu tools. Ask Spit for an account there. Survey's own login, registration and two-factor pages are switched off behind it, so there is one password and one authenticator, not two. Your Survey profile is created the first time the gate lets you through.

**Two-factor authentication is mandatory, and enforced by the gate.** Survey is the one tool in the borant.eu family where the gate asks for the second factor on everything it guards, from the landing page down, not only on the admin pages. Enrol an authenticator app (Ente Auth, Google Authenticator, Aegis) in Borant ID and keep its backup codes: each works once, and they are the way back if you lose the device. If you are locked out, Spit can reset the second factor there.

The reason is what sits behind the door: respondent data, in the exports and in the dashboard that lists them. Other tools let a password do. This one does not.

**Respondents never meet any of this.** The questionnaire at `/s/{slug}`, its submit route, published results pages, uploaded files and the platform's own scripts stay outside the gate, because a participant has no account here and must not be asked for one.

*Running your own copy.* Without a gate in front (`AUTH_MODE=local`, the default for a fresh install), Survey does all of this itself: open registration with a password of at least eight characters, a ten-minute session that cannot reach the dashboard until you enrol in TOTP, ten single-use backup codes shown once and regenerable from **Profile**, and an administrator page that can reset a user's second factor, disable an account or issue a temporary password. `DEPLOY.md` covers both modes.

**Your surveys are yours.** Every route under `/admin/surveys/...` filters by ownership — you see the surveys you created, an administrator sees all of them, and nothing is shared between accounts. A survey belonging to someone else answers "not found" rather than "forbidden", so no one can enumerate what they cannot read.

## 2. Creating a survey

A survey is three things: a **title**, a **slug** (the URL segment: `/s/pgt-gge-2026`, lowercased with spaces turned into hyphens, and unchangeable afterwards, because a link already handed out has to keep working), and a **schema** — the SurveyJS JSON that defines the whole questionnaire.

You do not build the schema here. Use the free **SurveyJS editor** (the *Build survey* button on the dashboard links to it), then either upload the `.json` file or paste the JSON into the form. The only check at this point is that the JSON parses; everything else is checked later, by you (§4).

Three things can go wrong at creation and all three say so: no schema supplied, JSON that does not parse, and a slug already in use. Editing later works the same way — **Edit schema** shows the stored JSON, formatted, and takes a replacement by upload or by typing. The title can change; the slug cannot. When the survey already holds responses the form says so and **refuses the save** until you tick the box that states the consequence: those answers were given to the wording stored now, and no backup restores the wording a respondent read (§12 says why it matters). Confirming is recorded in the application log, which is the only place it is recorded — nothing in the export says the instrument moved. The chat surface applies the same rule, where it is called `force` (§10).

New surveys created from the dashboard are **open** immediately. Close one with **Close** and it shows a "not available" page instead of the questionnaire, and its submit route refuses too — so closing is a real gate on the data, not just on the front page. Use it while you are still building.

## 3. Writing the questionnaire

A schema is an array of **pages**, each with an array of **elements**. Two properties carry most of the weight:

- **`name`** — the identifier of a question. It becomes the column header in your export, so it is the name your analysis will use. Two questions sharing a name collide into one column, which is why duplicate names are the first thing validation reports.
- **`visibleIf`** — a condition that decides whether a page or a question is shown, e.g. `"{consent} = 'yes'"` or `"{pgt_q2} >= 2"`. A question hidden at submission time contributes no answer at all, which is what makes the export's missing cells meaningful.

Question types render in the browser through the SurveyJS form library, so anything SurveyJS supports works. These are the ones Survey itself understands well enough to describe in the review document (§4):

| Type | What the participant does |
|---|---|
| `rating` | Picks a point on a numeric scale, with optional anchor labels at the ends |
| `radiogroup` | Picks one option from a visible list |
| `checkbox` | Picks any number of options |
| `dropdown` / `tagbox` | Picks one (`dropdown`) or several (`tagbox`) from a menu, optionally loaded from a URL |
| `boolean` | Flips a two-label switch (Yes / No by default) |
| `comment` | Types free text in a box |
| `text` | Types one line; `inputType` makes it a number, date or email field |
| `matrix` | Answers several rows against one shared set of columns |
| `expression` | Nothing — it is a computed value, never asked |
| `html` | Nothing — it is text to read, and carries no answer |
| `panel` / `paneldynamic` | Answers a group of questions treated as a block |

Any other type still renders for the participant, but comes out of the review document as a note naming the type instead of a described answer format — `image`, used for visual stimuli, is the common case.

**Four languages, one schema.** Localized strings (`{"en": …, "de": …, "fr": …, "it": …}`) are picked up automatically: the language bar shows only the languages actually present, hides itself when there is just one, and the initial choice follows the respondent's browser, falling back to the first available language. A `"default"` key counts as English. Anything you leave untranslated falls back to the primary text, silently — which is why the review document flags translation coverage per element rather than per survey.

**Files.** Each survey has its own upload folder, so two surveys can hold `diagram.png` without colliding. Upload jpg, jpeg, png, gif, webp, svg or pdf, up to 10 MB each, and reference the result from the schema by its stable path: `/uploads/{slug}/diagram.png`. Non-alphanumeric characters in a filename are replaced, so check the listed URL rather than assuming the name you uploaded.

**Reference lists.** Swiss cantons and world countries ship with the platform in all four languages, served from `/uploads/shared/cantons.json` and `/uploads/shared/countries.json`. Point a `dropdown` at one with *Choices from web*: the web service URL is the path above, the path to data stays empty, the value to store is `code`, and the value to display is `name_en` / `name_de` / `name_fr` / `name_it`. Hard-coding 195 countries into every schema is how translations drift apart.

## 4. Checking it before anyone answers

**Review DOCX** (Questionnaire → Review DOCX) renders the questionnaire itself as a Word document: every text in the primary language exactly as the participant reads it, the answer format under each question, required items starred, `visibleIf` rewritten as prose ("Shown only if: pgt_q3 is at least 2"), the randomization pools including which condition value each page sets, and a translation flag on every element (present, partial, missing). The cover carries a summary of the randomization, and every pooled page repeats the note in place, so a reviewer reading page nine knows they are looking at one arm of three. This is the artefact you circulate for feedback, because colleagues comment on wording, not on JSON.

**Walk it yourself** by opening `/s/{slug}`. This is the honest test of appearance, and it is also its limit: one arm at a time, whichever arm you happen to be assigned, and it costs you a response in your own data. Purge before fielding (§8).

**Validation and arm preview** are on the chat surface (§10), not in the web pages, because both are things you ask while you are editing rather than things you read on a screen. `validate_survey` reports duplicate question names, conditions reading a field nothing defines, conditions reading an answer given on a *later* page (which can never be true when the question is shown), placeholder choice values like `Item 1` that make an export unreadable, pool pages that do not exist in the schema, and arms that reach no questions at all. `preview_flow` reports, for every arm at once, the pages in the order shown and the questions on each.

Both read the schema, and the schema cannot know everything: `{condition} = 'C'` is decidable before anyone opens the survey, `{pgt_q2} >= 2` is not. A question whose visibility depends on an answer comes back marked **conditional, with the expression that governs it**, rather than guessed at — you can pin known answers to see what they open. The expression reader covers `=`, `<>`, `>`, `>=`, `<`, `<=`, `contains`, `notcontains`, `anyof`, `allof`, `empty`, `notempty`, `and`, `or`, `not` and parentheses; anything outside that subset is reported as undecidable or unparseable instead of being assumed true.

## 5. Randomization

A **pool** is a set of pages from which each participant is shown a given number. A survey can hold several independent pools, and they do not interfere.

- **Show count** — how many of the pool's pages each participant gets. Four pages with a show count of two make six combinations, and each participant is assigned one whole combination.
- **Balanced allocation** — the combination with the lowest count so far is assigned, ties broken at random. This keeps arms level without participant-level blocking, and the balancing is **uniform over combinations**, with no weights: a design that needs unequal arms needs two pools, not one pool with more arms.
- **The count is completed responses**, plus assignments issued in the last hour. Counting page loads is what this replaced: every abandonment and every reload consumed an arm, which is tolerable on Prolific and not tolerable on panel traffic, where abandonment is high. The one-hour window exists so that a burst of simultaneous starts does not all read the same counts and pile onto the same arm. The randomization page shows both figures, as *Completed* and *In progress*.
- **Condition variable** — available only when the show count is 1, because a pool that draws several pages has no single condition to name (the drawn pages are in the data anyway, as `_assignment`). Name it `condition` and the assigned value is exposed to the questionnaire before it renders, ready for use anywhere: `"visibleIf": "{condition} = 'A' or {condition} = 'C'"`.
- **Value map** — optional JSON turning page names into readable values: `{"info_a": "control", "info_b": "treatment"}`. Without it, the page name *is* the value.
- **Page order** — optional JSON that counterbalances presentation order for one condition value: `{"GGE_FIRST": ["vignette_gge", "vignette_pgt"]}`. The named pages are put back into the positions they collectively occupy in the schema, in the order listed; pages not named do not move, and a condition value with no entry keeps the schema order, so only the reversed arm needs listing. This exists so that counterbalancing does not require duplicating pages — duplicated pages mean duplicated question names, and the same answer landing in a different column depending on the arm. The reordering happens before the questionnaire is built, so it is invisible to `visibleIf` and to the progress bar.

Two constraints to hold in mind. Value map and page order are accepted only when the show count is 1 **and** a condition variable is set. JSON that does not parse — or a page order naming a page this questionnaire does not have — is refused with a message naming the field and the reason, and nothing is saved; the page comes back with what you typed still in it, so a rejected paste is never lost.

**Saving a pool resets that pool's balance counters** — the web form and the chat surface behave identically here, and the form asks you to confirm. The reason is that the old counts describe a set of combinations that no longer exists: changing the pages or the show count changes what the arms are, so carrying counts across would balance toward something you are no longer running. **Reset** clears the counts without touching the configuration.

Assignments issued and never submitted are cleared out after a week, since a session that old can no longer complete. Nothing you see in the *Completed* column is ever an abandonment.

The pattern worth copying, from the PGT/GGE vignette study: **two orthogonal pools instead of one pool with more arms** — one assigning the version (`condition` → A/B/C), one assigning the order (`vorder` → PGT_FIRST/GGE_FIRST). One four-page pool would split 25/25/25/25 and send half the sample to the wrong place; two pools hold a third per version and balance order within one of them, recruiting nobody extra.

**A pool cannot sample from many pages.** It enumerates every combination of its pages at each page load, so showing 10 of 160 pages would mean some 10¹⁶ combinations. A pool above 10,000 combinations is refused at save, with the count in the message; that is what item banks are for.

### Item banks

An **item bank** gives each participant a sample of *items*, one *version* of each, in an order, with the share of versions set by their *arm*. It is the tool for stimulus banks: passages, vignettes or cases, each existing in several versions, of which each participant sees a handful.

- **Items and versions.** Each version is a page of the schema, named for instance `P017_H` and `P017_L`. Versions keep their own question names, so an answer always lands in a column that says which item and which version it belongs to. Name the questions `<page>_<role>` (`P017_H_quality`): the long export lines answers up by role across versions. On the randomization page, **Propose from page names** builds the item list from the schema with a regex, and lists the pages it left out.
- **Show** — how many items each participant gets. A bank never shows two versions of the same item to the same person.
- **Arms** — optional quotas of versions, each summing to Show: `[{"name": "LLM30", "quota": {"H": 7, "L": 3}}]`. The drawn arm is exposed under the **arm variable**, like a pool's condition, so it can drive `visibleIf` anywhere. Without arms, each item gets the version it has been shown in least.
- **Balance.** The least-used arm is drawn, then the least-shown items, then versions are assigned so that each item moves toward an even split while the arm's quota is met exactly. As for pools, completed responses count, plus draws issued in the last hour. The page states the result in one line — how often items have been shown and the largest gap between an item's versions — with the per-item table one click away.
- **Arm from an answer.** For a rotation across waves, the arm can come from the answers instead of the balancing: **arm from an answer** names a question, or a calculated value saved with the result, whose value is an arm name (for example a form computed from a day of birth and the wave number). The server draws a sequence for every arm at the page load, the browser shows the one the answer names as soon as it is given, and the submit records that one only. Arms are then as large as the answers make them, not balanced. The question must come before the bank's pages. Section 15 of the specification has an example.
- **The editor.** Each bank opens in a visual editor: a grid of items by versions where each cell picks a page, a quota table whose totals are checked as you type, the choice of how a participant gets a group, the server's findings with a link to where each one applies, and an example draw per group beside it. **Edit as JSON** shows the same configuration as text, and the editor reads it back when you return. Saving is the same as before and resets the bank's draws; once the survey holds responses, you are asked to type the bank name.
- **Generate pages from a table.** Under Content, when the version pages do not exist yet: write one template page whose texts carry placeholders in double braces (`{{text}}`), then paste a table from a spreadsheet, or upload a CSV, with the columns `item`, `version` and one per placeholder. **Preview** shows the problems, the names and the first pages as a participant would see them; **Add** writes one page per row, named `item_version`, with questions renamed `item_version_role`, and fills the grid. Removing the template page is ticked by default, since every participant would otherwise see it. Refused once the survey holds responses, unless you confirm.
- **Rotation across waves.** For a study with parallel forms over several waves, choose *Rotation across waves* under Groups: pick an answer every wave asks the same way (a day of birth in a personal code), set the wave of this questionnaire, and add the rotation to the questionnaire. The editor shows who gets which group at each wave and the expected share per group (a day of the month is weighted by the calendar, so three groups get about 32.6, 32.6 and 34.8%). Then save the bank. For the next wave, copy the survey and change only the wave number. Changing the questionnaire this way is refused once it holds responses, unless you confirm.
- **Rules.** A page belongs to one bank at most and never to a bank and a pool. Common pages (consent, demographics, debrief) stay outside both and are shown to everyone. Saving a bank resets its draws, for the same reason saving a pool resets its counters.
- **Data.** Each response records what it was shown (arm, items, versions, pages), written by the server from its own ledger, so it survives a deleted bank. The flat export carries it as columns; the **long export** on the survey page gives one row per response and drawn item, the table a mixed model wants.

The specification, with the allocation algorithm and its guarantees, is `docs/item-banks-spec.md`.

## 6. Fielding it

**Share** gives you the public URL and a **QR code** to right-click and drop into slides or a poster. The link works while the survey is open, and anyone holding it can answer — there are no per-participant links, no email invitations, and no limit on how many times the same person can respond. If you need one-response-per-person, that comes from panel recruitment or from the recruitment platform you are using, not from Survey.

**Panel recruitment** is for demoscopic providers (Bilendi, Dynata, Cint, Toluna). They hand each respondent a single-use identifier in the entry link and expect the questionnaire to send them back to a return URL carrying it: the return leg is how the provider learns the person finished, and therefore how the person gets paid. Configure it on the manage page:

- **Token parameter** — the query parameter holding the respondent id, matched case-insensitively because providers disagree on capitalisation (`RID` for Bilendi and Cint, `psid` for Dynata, `tid` for Toluna). Setting this field is what switches panel mode on; clearing it returns the survey to an open link.
- **Three return URLs** — complete, screenout, quota full. The token is appended as `?param=token`, or substituted for a `{token}` placeholder when the provider's URL carries the id mid-path.
- **Entry URL** — hand the provider `https://survey.borant.eu/s/{slug}?RID=[respondent id]`.

With panel mode on, anyone arriving without a valid token is stopped before the questionnaire and sent to the screenout URL, so a respondent nobody can credit does not spend ten minutes answering. Append `?preview=1` while signed in as the owner to walk it yourself; preview submissions are stored, but never bounce to the provider. A token that already produced a response can neither re-enter nor submit again, enforced by a database uniqueness constraint rather than a check before the insert, so two simultaneous submissions cannot both slip through. The questionnaire routes non-completions by setting a field named `_outcome` to `screenout` or `quotafull` with a SurveyJS trigger — a consent gate is the usual case — and the response is stored either way, so refusals stay in your data instead of vanishing.

**Link tags** tell recruitment channels apart on an open link. On the manage page, list up to five parameter names, for instance `channel`; then hand out one link per channel, such as `…/s/{slug}?channel=newsletter` and `…/s/{slug}?channel=conference`. The value is stored with the response as `_url.channel`, which the CSV and Excel exports show as the column `_url_channel`, and the questionnaire can read it in expressions as `{url_channel}`. Names are matched case-insensitively; values may hold letters, digits, dots, hyphens and underscores, and anything else is dropped rather than cleaned, so what is stored is what the link said. Parameters not on the list are ignored. A tag records which link somebody used, not who they are, since anyone can edit the end of a link: use it to describe where responses came from, never to admit or exclude people. That is what panel mode is for.

## 7. What the participant walks through

One page at a time, with a progress bar, in the language the bar offers. Pool pages that were not drawn are hidden before the model is built, so the participant never sees a gap.

**A link in the header, *How this platform works*, opens a notice** in the participant's language: who runs the platform, what is recorded alongside the answers, who sees what, that the page sets no cookies and loads nothing from another site, and that an anonymous response cannot be found again to be deleted. It explains the tool, not the study: purpose, legal basis, retention and ethics approval are yours to state in your own participant information, and the notice tells the reader so. Read it once before fielding, because your information sheet should not contradict it.

**Nothing is stored until the participant reaches the end and submits.** There is no save-and-resume, no partial responses, and no way back into a submitted response to correct it. An abandoned session leaves no row in your data — only, for up to an hour, a pending assignment in the balance counters. Plan the questionnaire's length accordingly: a break-off at question forty costs you all forty answers.

**Timing is recorded**: total seconds, and seconds per page keyed by page name, measured in the browser from when the page appears. Only pages the participant actually reached get an entry. It measures a page being open, which is not the same as attention, so treat it as a screening variable for implausibly fast completions rather than as a measure of anything.

One known rough edge, worth designing around: the progress bar counts pages by their visibility, and a page shown *because* something has not been answered is visible from the start. A consent-refusal page conditioned on empty consent therefore appears in the list of steps before anyone refuses anything, so the first thing a participant reads among the stages can be "Unable to Proceed". Put such a page last, or fold the refusal into the consent page itself.

## 8. Responses and export

The manage page shows the response count and the last submission. Three export formats, all from the same rows:

- **CSV** — one row per response, flat. Nested objects become `parent_key` columns, lists become semicolon-separated strings. The response id `_response` comes first, then columns in order of first appearance; `_submitted_at` goes last.
- **Excel** — the same columns, guaranteed, plus a frozen bold header, autofilter, and native number types (a 1–5 scale arrives summable). Strings starting with `=` are forced to text, so an open-ended answer cannot execute as a formula on the analyst's machine.
- **JSON** — the untouched payloads, each with its `response` id and `submitted_at`. Use this when the flattening loses something you need.

Alongside your own question names, each response carries the platform's own keys:

| Key | Content |
|---|---|
| `_response` | The response id, the same number as the `response` column of the item-bank long export, so the two tables join on it |
| `_submitted_at` | Server timestamp of the submission |
| `_timing` | Total seconds, and seconds per page |
| `_conditions` | The condition variables assigned, e.g. `{"condition": "B"}` |
| `_assignment` | The pool pages actually drawn |
| `_assignment_ids` | Internal ids of the assignments this response completed |
| `_panel_token` | The provider's respondent id, in panel mode only |
| `_url` | Link tags, e.g. `{"channel": "newsletter"}`, when the survey has some configured |
| `_outcome` | `complete`, `screenout` or `quotafull`, when the schema sets it |

Older surveys, configured before pools could name their own variable, carry `_condition` as a plain string instead of `_conditions`.

**Purge answers** (danger zone) empties a survey of its responses and of the randomization balance that goes with them, keeping the questionnaire, its files and its pool configuration. The balance goes because leaving it would count arms as completed by respondents who no longer exist. This is the button for clearing your own test run before fielding. **Delete survey** removes everything, including the uploaded files. Neither can be undone, and the dashboard list offers an inline delete only for surveys at zero responses.

The export is the handover point for analysis. What Survey adds on top of it, in §9, is for watching the data arrive and for showing it, not for writing it up.

## 9. Showing the results

A **report** is a results page for one survey: an ordered list of blocks, each either text you write or a chart of one question's answers, served live to up to three audiences. Open the editor from the manage page with **Results report**. One survey has one report.

**Blocks.** Three kinds:

- **Text** — markdown, one version per language of the questionnaire. A language you leave empty falls back to English. Raw HTML is not rendered.
- **Question** — one question by name, with its chart, counts or percentages, schema or frequency order, and whether an *Other* option is shown.
- **All questions** — every question in schema order, resolved each time the page is built, so a question added next week appears by itself. **+ All questions** is the one-click start; **Explode into separate blocks** turns it into one block per question, which is how customising starts.

Blocks reorder by dragging or with the arrow buttons. A block naming a question the schema no longer has stays on the page and says it is orphaned, rather than vanishing.

**What a chart shows.** Each question type gets the chart its shape allows: bars for single and multiple choice, a distribution with mean, median and SD for ratings and numbers, a stacked bar per row for matrices, positions for rankings, a timeline for dates. Every question shows **n** (answered) and **missing** (shown it, did not answer), because branching and randomization make those differ from question to question. Multiple-choice percentages are of respondents, not of selections. Every chart has a table underneath, which is also what the page shows with JavaScript off.

**Audiences.** The report has a ceiling, and each block can travel less far than the report does:

| | Owner (you) | Respondents | Anyone with the link |
|---|---|---|---|
| Closed-choice questions | all | the blocks you allow | the blocks you allow |
| Open text answers | yes | **no** | **never** |
| Split by randomization arm | yes | no | no |
| n and missing | yes | yes | yes |
| Their own answers marked | — | yes | — |
| Cells with fewer than 5 people | shown | masked | masked |

Open text is refused at every level but yours, whatever a block asks for: the sentence somebody typed into a comment box was not written for a public page. The split by arm stays with you because it is the experimental manipulation, and nobody's business before the study is written up.

**Masking.** Below five people a cell is hidden from respondents and the public, and a question with fewer than five answers in total says *not enough responses yet*. Hiding one cell would hide nothing, since the total is published and a subtraction gives it back, so the hidden group grows until it holds at least two cells adding up to five or more. Cells at zero stay visible.

**Where each audience finds it.**

- **You**: **View results** on the manage page (`/admin/surveys/{slug}/results`). **Looking as** *me / a respondent / anyone* rebuilds the page for that audience through exactly the filter they would get, with a banner saying so — the only way to check what a published page exposes without publishing it. Choosing a condition variable beside *everyone* splits every chart by arm.
- **Respondents**: when the report reaches them, the questionnaire ends with a link to the results, carrying a token minted at submission (`/s/{slug}/results?r=…`). It opens the page with that person's own answers marked. Each link is personal, so there is no respondent link to hand out.
- **Anyone**: a public report lives at `/s/{slug}/results`. The manage page (under **Share**) and the editor both offer that URL, a QR code and an iframe snippet for `/s/{slug}/results/embed`, the same page without its frame. A report that does not reach the public answers "not found", exactly like a survey that does not exist.

The numbers are live: the page re-reads them every thirty seconds and stamps the time. The page's own words come in all four languages, with a switcher on every results page, and question labels follow the schema.

**Publishing while the survey is still open** is allowed, and the editor says what it costs at the moment you choose it: whoever reads the results before answering is anchored by them. Publishing is recorded in the application log.

**Exploring two questions.** On your own results page, a floating button opens a panel that takes two questions and says whether they move together. The test is chosen from the shapes of the two variables, and it is always rank-based: Spearman for two rankable variables, Mann-Whitney or Kruskal-Wallis for a category against a rankable one, Fisher's exact test or chi-square for two categories. A checkbox question counts as one yes/no per option. Every result carries an effect size beside its p, the number of people who answered both, and its caveats; when p falls under 0.05 it also says how many pairs this questionnaire offers, because trying them all yields one in twenty by chance alone. Fewer than ten complete pairs is refused. The **?** beside the asterisks and beside the test name explain what each is worth. This is for looking, not for a results section.

**Letting readers explore.** Once the report reaches respondents or the public, the editor offers a switch, off by default, that gives readers the same panel. They get the statistic and the effect size, never the people behind them: table cells are masked under the same rule as the charts, groups under five are dropped, and they can only cross questions the report already shows them. With the switch off the panel does not exist on their page at all. This makes sense for a citizen-science page and not for a study about abuse, which is why it is a decision and not a default. Turning it on is logged.

There are no cross-tabs to export, no word clouds and no figure export for papers. Open answers belong in a qualitative coding tool, and inference in R.

## 10. Driving it from a chat

If you work with an AI assistant, Survey can be plugged into it. The point is not saving clicks: editing a questionnaire raises the same three questions every time — what does it look like now, what would a participant in each arm actually walk through, and what did the last change break — and the web pages answer only the first.

**Getting a key.** On the dashboard, under **MCP key**, create one. Paste `https://survey.borant.eu/mcp` and the key into your client's MCP settings as an `X-API-Key` header; clients that cannot set headers can use `https://survey.borant.eu/mcp/k/<your-key>` instead, where the key sits in the URL and access logs may keep it. You get one key, and regenerating it replaces it at once, so anything else still configured with the old one stops working.

**What the assistant can do.** List your surveys and read one; read the schema; walk every randomization arm; validate what is broken; read the balance counters, and a summary of how many responses arrived per arm, how long they took and how often each question was answered, whose `never_answered` list is the quick way to find a branch nobody reaches. On the results side (§9): `question_summary` returns the aggregates a published results page would draw, at the public level, so it doubles as the way to check a page before publishing it; `get_report` and `set_report` read and replace the report. Writing: create a survey, replace its schema, create, replace or delete a pool, reset counters, open or close it, and replace the report.

**The model never sees your data.** No tool returns a response. The assistant gets no payload, no open answer, no panel token and no answers split by randomization arm, and `question_summary` refuses the owner's view outright: what it hands over is what anyone would see on a public results page, with cells under five people masked and a question under five answers reported only as too small. What it does see is the questionnaire itself and counts — how many responses, how many per arm, how often each question was answered.

The reason is where a conversation goes. Whatever an assistant reads travels to the company that runs the model, under that company's terms and not this server's, and usually outside Switzerland. The participant notice (§7) tells people that individual answers are seen only by the people running the study, and the MCP surface is built so that this stays true whoever connects an assistant. Individual responses, open text and the experimental split are read on your results page and in the export, by you.

**Where it refuses.** A schema with structural errors is refused unless you force it. **Editing the schema of a survey that already holds responses is refused unless you force it**, because those answers were given to the other wording and reinterpreting them silently is the one mistake a backup does not undo — and if you do force it, write it down, because nothing in the export records that the instrument moved under the data. Deleting a survey and deleting responses are not exposed at all: they stay in the web pages, where a human is holding the mouse. A new survey is created closed unless you say otherwise, so a half-built questionnaire is not reachable while you finish it. Publishing a report beyond yourself while the survey is still collecting is refused unless the call says `publish_on_open`, for the anchoring reason in §9: the web editor shows a warning, and a tool without a warning box has to refuse instead.

**What it cannot do.** Upload files (binary, so the corpus of images arrives through the web pages), set panel recruitment, purge responses, delete a survey, and manage users.

**What it reaches.** Your surveys and no one else's, and less of them than you do. The key carries your identity, so a survey you cannot manage does not exist as far as your assistant is concerned, and disabling an account closes its key with it. The key does **not** pass through the sign-in gate, because an assistant cannot type a six-digit code: `/mcp` sits outside Borant ID and the key alone opens it. A key is therefore a password with no second factor in front of it. Keep it out of shared configuration files, and regenerate it if it might have leaked.

A whole questionnaire has been built this way — the reusable teaching-evaluation survey, created, revised three times, validated and previewed from a conversation without the admin pages being opened once. It works best where the questionnaire is being *thought out* rather than typed in.

## 11. Data protection

**A response is anonymous by construction, and that is a claim about this app, not about your study.** What gets stored is the payload the browser sends — your questions' answers plus the keys in §8 — a server timestamp, and a random token that opens the respondent's own results view (§9). No IP address, no user agent, no cookie, no browser storage, no account: a respondent has no identity here to record. Everything the questionnaire loads comes from this server, so answering contacts no third party. The notice behind the questionnaire's header link (§7) tells participants the same, in their language; `test_notice.py` checks those claims against the rendered page, so one that stops being true fails a test instead of staying written.

Four things qualify that:

- **A panel token is a pseudonymous identifier.** The provider can trace it back to a person. A study recruiting this way has to say so in its ethics application and in its participant information, and cannot describe its responses as carrying no identifying metadata.
- **Open-ended answers identify people.** A free-text box invites names, places, diagnoses and workplaces, and no setting here can prevent that. Whatever a participant types is what you now hold.
- **Timing and condition data are still data about a person.** Seconds per page plus a rare demographic combination is a smaller haystack than it looks.
- **Anonymity cuts both ways.** A participant who changes their mind after submitting cannot be found again by anything stored here. The notice tells them to write to you quickly, with the day and time, so put a contact in your participant information.

**Who can read the responses.** You, and an administrator of the platform, both through the sign-in gate with a password and a second factor (§1). They stay in the platform's database, on a server in Switzerland run by the research group, until you purge or delete the survey. Uploaded files are served publicly at a guessable path, so do not put anything there that is not meant for participants' eyes.

**What a results page lets out.** Aggregates only, and never open text, at any level but yours. Cells under five people are masked with the complementary rule in §9, and the split by randomization arm stays with you. Two things remain your judgement. Masking protects small cells, not small samples: a public page on a study of twelve people says a great deal about each of them, even with every rule applied. And a respondent's results link marks that person's own answers, so anyone they forward it to sees what they answered. **Looking as** *anyone* before publishing is the check; nothing reaches beyond you until you raise the report's audience.

**The MCP key reaches the questionnaire, not the people.** An assistant holding it can read and change the schema, the pools and the report, and read aggregates at the public level (§10), but never a response, an open answer or a panel token. That is what lets the participant notice say that individual answers stay with the study team without an exception for researchers who use an assistant. It also means a leaked key gives away counts and masked aggregates, not a single response, though it can still damage a questionnaire: it has no second factor in front of it, so keep it out of shared configuration and revoke it when the project ends. If you paste exported responses into a conversation yourself, that is a transfer to the model provider like any other, and your ethics approval and participant information have to cover it.

Compliance with GDPR and the Swiss FADP, your institutional requirements and the conditions of your ethics approval is the researcher's responsibility, not the tool's. When in doubt, ask your data protection officer before fielding, not after.

## 12. Good practices

- **Changing question wording after responses arrive changes what the data means.** The old rows keep their answers and the new column headers do not describe them; where you add or remove an item, past respondents are absent from it. Freeze the instrument before the first real response, and if you must edit afterwards, treat it as two studies and say so in the write-up.
- **Name your questions for your analysis**, not for the editor. `pgt_q3` and `consent` survive the export; the SurveyJS builder's default `question1` does not tell you anything six months later. And never leave choice values as `Item 1`: reordering the options then silently changes what past answers mean.
- **Pilot, then purge.** Walk the whole questionnaire yourself in each language, check the timing looks sane, then purge the answers and the balance before you field. A test run left in the data is indistinguishable from a participant.
- **Circulate the review DOCX, not the JSON.** Reviewers comment on wording; the document exists so that the wording is what they see.
- **Validate after every schema edit**, especially the forward-reference check: a condition reading an answer given on a later page can never be true, and the questionnaire will not tell you — the question just never appears.
- **Design arms with two pools rather than one**, whenever the arms should not be equal in size. Balancing is uniform over combinations and knows no weights, so unequal allocation is expressed by structure, not by a setting.
- **Watch the balance counters mid-field.** *In progress* far above *Completed* means people are opening and leaving, which is a questionnaire problem, not a randomization problem.
- **Prefer separate rating questions to one big matrix**, unless the items genuinely share a scale. A matrix imposes a single agreement scale on dimensions that do not take it well and pays the price in acquiescence bias; separate `rating` items each carry their own anchors and still arrive numeric in the Excel export.
- **Keep the questionnaire short enough to finish.** Nothing is saved before submission, so length is paid for in whole responses, not in missing cells.
- **Publish results after closing, unless the study is about sharing them as they come.** An open survey with a public page anchors everyone who looks before answering. Check the page with **Looking as** *anyone* first, and remember that small samples stay revealing even with every cell above five.
- **Keep exports out of conversations.** The MCP surface never hands a model your responses; pasting the CSV into a chat undoes that in one move. Work on the export where your analysis already lives.
- **Close the survey when you are done.** Closing blocks the page and the submit route, so nothing arrives after the cutoff you report.
- **Survey gives you an instrument and a clean table.** Sampling, recruitment, consent, ethics, weighting and analysis are the human work, and none of them is automated here.
