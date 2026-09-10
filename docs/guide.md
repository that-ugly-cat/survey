# Survey — User Guide

Survey is a self-hosted questionnaire platform for academic studies: you write the questionnaire as a JSON schema, share one public link, and download the responses as CSV, Excel or JSON. It runs on the institute's own machine, so no respondent data passes through a commercial survey service. It is built for experimental designs — balanced randomization, condition variables, counterbalanced presentation order — and it does nothing about analysis, which stays where you already do it.

---

## 1. Getting in

Two doors stand between you and the dashboard, and they are not the same door.

1. **Borant ID** is the shared sign-in for the borant.eu tools. Reaching the researcher side of Survey needs an account there; ask Spit for one. Respondents never meet it: the questionnaire at `/s/{slug}`, its submit route and the uploaded files stay open, because a participant has no account here and must not be asked for one.
2. **The Survey account** itself. Register with your name, email and a password of at least eight characters. If your institutional sign-in took you straight to the dashboard and no password was asked, your account is already linked — go to §2.

**Two-factor authentication is mandatory.** A fresh account gets a session that lasts ten minutes and cannot reach the dashboard until you enrol: scan the QR code with an authenticator app (Ente Auth, Google Authenticator, Aegis), confirm the six-digit code, and save the **ten backup codes** you are then shown — each works once, and they are the only way back if you lose the device. Every later sign-in asks for the current code. You can regenerate the backup codes from **Profile**; an administrator can reset your 2FA if you are locked out, and can also disable an account or issue a temporary password.

The reason 2FA survived the move behind the shared gate, when other tools dropped theirs, is what the exports contain: respondent data. The dashboard that lists them is respondent data too.

**Your surveys are yours.** Every route under `/admin/surveys/...` filters by ownership — you see the surveys you created, an administrator sees all of them, and nothing is shared between accounts. A survey belonging to someone else answers "not found" rather than "forbidden", so no one can enumerate what they cannot read.

## 2. Creating a survey

A survey is three things: a **title**, a **slug** (the URL segment: `/s/pgt-gge-2026`, lowercased with spaces turned into hyphens, and unchangeable afterwards, because a link already handed out has to keep working), and a **schema** — the SurveyJS JSON that defines the whole questionnaire.

You do not build the schema here. Use the free **SurveyJS editor** (the *Build survey* button on the dashboard links to it), then either upload the `.json` file or paste the JSON into the form. The only check at this point is that the JSON parses; everything else is checked later, by you (§4).

Three things can go wrong at creation and all three say so: no schema supplied, JSON that does not parse, and a slug already in use. Editing later works the same way — **Edit schema** shows the stored JSON, formatted, and takes a replacement by upload or by typing. The title can change; the slug cannot. Note that the web form saves an edit even when the survey already holds responses, and says nothing — the chat surface refuses that same edit unless forced (§9). Read the absence of a warning as an absence of a warning, not as permission; §11 says why it matters.

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

**Validation and arm preview** are on the chat surface (§9), not in the web pages, because both are things you ask while you are editing rather than things you read on a screen. `validate_survey` reports duplicate question names, conditions reading a field nothing defines, conditions reading an answer given on a *later* page (which can never be true when the question is shown), placeholder choice values like `Item 1` that make an export unreadable, pool pages that do not exist in the schema, and arms that reach no questions at all. `preview_flow` reports, for every arm at once, the pages in the order shown and the questions on each.

Both read the schema, and the schema cannot know everything: `{condition} = 'C'` is decidable before anyone opens the survey, `{pgt_q2} >= 2` is not. A question whose visibility depends on an answer comes back marked **conditional, with the expression that governs it**, rather than guessed at — you can pin known answers to see what they open. The expression reader covers `=`, `<>`, `>`, `>=`, `<`, `<=`, `contains`, `notcontains`, `anyof`, `allof`, `empty`, `notempty`, `and`, `or`, `not` and parentheses; anything outside that subset is reported as undecidable or unparseable instead of being assumed true.

## 5. Randomization

A **pool** is a set of pages from which each participant is shown a given number. A survey can hold several independent pools, and they do not interfere.

- **Show count** — how many of the pool's pages each participant gets. Four pages with a show count of two make six combinations, and each participant is assigned one whole combination.
- **Balanced allocation** — the combination with the lowest count so far is assigned, ties broken at random. This keeps arms level without participant-level blocking, and the balancing is **uniform over combinations**, with no weights: a design that needs unequal arms needs two pools, not one pool with more arms.
- **The count is completed responses**, plus assignments issued in the last hour. Counting page loads is what this replaced: every abandonment and every reload consumed an arm, which is tolerable on Prolific and not tolerable on panel traffic, where abandonment is high. The one-hour window exists so that a burst of simultaneous starts does not all read the same counts and pile onto the same arm. The randomization page shows both figures, as *Completed* and *In progress*.
- **Condition variable** — available only when the show count is 1, because a pool that draws several pages has no single condition to name (the drawn pages are in the data anyway, as `_assignment`). Name it `condition` and the assigned value is exposed to the questionnaire before it renders, ready for use anywhere: `"visibleIf": "{condition} = 'A' or {condition} = 'C'"`.
- **Value map** — optional JSON turning page names into readable values: `{"info_a": "control", "info_b": "treatment"}`. Without it, the page name *is* the value.
- **Page order** — optional JSON that counterbalances presentation order for one condition value: `{"GGE_FIRST": ["vignette_gge", "vignette_pgt"]}`. The named pages are put back into the positions they collectively occupy in the schema, in the order listed; pages not named do not move, and a condition value with no entry keeps the schema order, so only the reversed arm needs listing. This exists so that counterbalancing does not require duplicating pages — duplicated pages mean duplicated question names, and the same answer landing in a different column depending on the arm. The reordering happens before the questionnaire is built, so it is invisible to `visibleIf` and to the progress bar.

Two constraints to hold in mind. Value map and page order are accepted only when the show count is 1 **and** a condition variable is set, and JSON that does not parse is dropped without an error message, so reopen the page after saving to confirm the field kept what you typed.

**Saving a pool resets that pool's balance counters** — the web form and the chat surface behave identically here, and the form asks you to confirm. The reason is that the old counts describe a set of combinations that no longer exists: changing the pages or the show count changes what the arms are, so carrying counts across would balance toward something you are no longer running. **Reset** clears the counts without touching the configuration.

Assignments issued and never submitted are cleared out after a week, since a session that old can no longer complete. Nothing you see in the *Completed* column is ever an abandonment.

The pattern worth copying, from the PGT/GGE vignette study: **two orthogonal pools instead of one pool with more arms** — one assigning the version (`condition` → A/B/C), one assigning the order (`vorder` → PGT_FIRST/GGE_FIRST). One four-page pool would split 25/25/25/25 and send half the sample to the wrong place; two pools hold a third per version and balance order within one of them, recruiting nobody extra.

## 6. Fielding it

**Share** gives you the public URL and a **QR code** to right-click and drop into slides or a poster. The link works while the survey is open, and anyone holding it can answer — there are no per-participant links, no email invitations, and no limit on how many times the same person can respond. If you need one-response-per-person, that comes from panel recruitment or from the recruitment platform you are using, not from Survey.

**Panel recruitment** is for demoscopic providers (Bilendi, Dynata, Cint, Toluna). They hand each respondent a single-use identifier in the entry link and expect the questionnaire to send them back to a return URL carrying it: the return leg is how the provider learns the person finished, and therefore how the person gets paid. Configure it on the manage page:

- **Token parameter** — the query parameter holding the respondent id, matched case-insensitively because providers disagree on capitalisation (`RID` for Bilendi and Cint, `psid` for Dynata, `tid` for Toluna). Setting this field is what switches panel mode on; clearing it returns the survey to an open link.
- **Three return URLs** — complete, screenout, quota full. The token is appended as `?param=token`, or substituted for a `{token}` placeholder when the provider's URL carries the id mid-path.
- **Entry URL** — hand the provider `https://survey.borant.eu/s/{slug}?RID=[respondent id]`.

With panel mode on, anyone arriving without a valid token is stopped before the questionnaire and sent to the screenout URL, so a respondent nobody can credit does not spend ten minutes answering. Append `?preview=1` while signed in as the owner to walk it yourself; preview submissions are stored, but never bounce to the provider. A token that already produced a response can neither re-enter nor submit again, enforced by a database uniqueness constraint rather than a check before the insert, so two simultaneous submissions cannot both slip through. The questionnaire routes non-completions by setting a field named `_outcome` to `screenout` or `quotafull` with a SurveyJS trigger — a consent gate is the usual case — and the response is stored either way, so refusals stay in your data instead of vanishing.

## 7. What the participant walks through

One page at a time, with a progress bar, in the language the bar offers. Pool pages that were not drawn are hidden before the model is built, so the participant never sees a gap.

**Nothing is stored until the participant reaches the end and submits.** There is no save-and-resume, no partial responses, and no way back into a submitted response to correct it. An abandoned session leaves no row in your data — only, for up to an hour, a pending assignment in the balance counters. Plan the questionnaire's length accordingly: a break-off at question forty costs you all forty answers.

**Timing is recorded**: total seconds, and seconds per page keyed by page name, measured in the browser from when the page appears. Only pages the participant actually reached get an entry. It measures a page being open, which is not the same as attention, so treat it as a screening variable for implausibly fast completions rather than as a measure of anything.

One known rough edge, worth designing around: the progress bar counts pages by their visibility, and a page shown *because* something has not been answered is visible from the start. A consent-refusal page conditioned on empty consent therefore appears in the list of steps before anyone refuses anything, so the first thing a participant reads among the stages can be "Unable to Proceed". Put such a page last, or fold the refusal into the consent page itself.

## 8. Responses and export

The manage page shows the response count and the last submission. Three export formats, all from the same rows:

- **CSV** — one row per response, flat. Nested objects become `parent_key` columns, lists become semicolon-separated strings. Column order follows first appearance; `_submitted_at` goes last.
- **Excel** — the same columns, guaranteed, plus a frozen bold header, autofilter, and native number types (a 1–5 scale arrives summable). Strings starting with `=` are forced to text, so an open-ended answer cannot execute as a formula on the analyst's machine.
- **JSON** — the untouched payloads, each with its `submitted_at`. Use this when the flattening loses something you need.

Alongside your own question names, each response carries the platform's own keys:

| Key | Content |
|---|---|
| `_submitted_at` | Server timestamp of the submission |
| `_timing` | Total seconds, and seconds per page |
| `_conditions` | The condition variables assigned, e.g. `{"condition": "B"}` |
| `_assignment` | The pool pages actually drawn |
| `_assignment_ids` | Internal ids of the assignments this response completed |
| `_panel_token` | The provider's respondent id, in panel mode only |
| `_outcome` | `complete`, `screenout` or `quotafull`, when the schema sets it |

Older surveys, configured before pools could name their own variable, carry `_condition` as a plain string instead of `_conditions`.

**Purge answers** (danger zone) empties a survey of its responses and of the randomization balance that goes with them, keeping the questionnaire, its files and its pool configuration. The balance goes because leaving it would count arms as completed by respondents who no longer exist. This is the button for clearing your own test run before fielding. **Delete survey** removes everything, including the uploaded files. Neither can be undone, and the dashboard list offers an inline delete only for surveys at zero responses.

There is no analysis in Survey — no crosstabs, no charts, no significance tests. The export is the handover point.

## 9. Driving it from a chat

If you work with an AI assistant, Survey can be plugged into it. The point is not saving clicks: editing a questionnaire raises the same three questions every time — what does it look like now, what would a participant in each arm actually walk through, and what did the last change break — and the web pages answer only the first.

**Getting a key.** On the dashboard, under **MCP key**, create one. Paste `https://survey.borant.eu/mcp` and the key into your client's MCP settings as an `X-API-Key` header; clients that cannot set headers can use `https://survey.borant.eu/mcp/k/<your-key>` instead, where the key sits in the URL and access logs may keep it. You get one key, and regenerating it replaces it at once, so anything else still configured with the old one stops working.

**What the assistant can do.** List your surveys and read one; read the schema; walk every randomization arm; validate what is broken; read the balance counters, the collected responses with their full payloads, and a summary whose `never_answered` list is the quick way to find a branch nobody reaches. Writing: create a survey, replace its schema, create or replace a pool, reset counters, open or close it.

**Where it refuses.** A schema with structural errors is refused unless you force it. **Editing the schema of a survey that already holds responses is refused unless you force it**, because those answers were given to the other wording and reinterpreting them silently is the one mistake a backup does not undo — and if you do force it, write it down, because nothing in the export records that the instrument moved under the data. Deleting a survey and deleting responses are not exposed at all: they stay in the web pages, where a human is holding the mouse. A new survey is created closed unless you say otherwise, so a half-built questionnaire is not reachable while you finish it.

**What it cannot do.** Upload files (binary, so the corpus of images arrives through the web pages), set panel recruitment, purge or delete anything, and manage users.

**What it reaches.** Exactly what you reach. The key carries your identity, so a survey you cannot manage does not exist as far as your assistant is concerned, and disabling an account closes its key with it.

A whole questionnaire has been built this way — the reusable teaching-evaluation survey, created, revised three times, validated and previewed from a conversation without the admin pages being opened once. It works best where the questionnaire is being *thought out* rather than typed in.

## 10. Data protection

**A response is anonymous by construction, and that is a claim about this app, not about your study.** What gets stored is the payload the browser sends — your questions' answers plus the keys in §8 — and a server timestamp. No IP address, no user agent, no cookie, no account: a respondent has no identity here to record.

Three things qualify that:

- **A panel token is a pseudonymous identifier.** The provider can trace it back to a person. A study recruiting this way has to say so in its ethics application and in its participant information, and cannot describe its responses as carrying no identifying metadata.
- **Open-ended answers identify people.** A free-text box invites names, places, diagnoses and workplaces, and no setting here can prevent that. Whatever a participant types is what you now hold.
- **Timing and condition data are still data about a person.** Seconds per page plus a rare demographic combination is a smaller haystack than it looks.

Responses live in the platform's database on the institute's server, readable by you and by an administrator, until you purge or delete the survey. Uploaded files are served publicly at a guessable path, so do not put anything there that is not meant for participants' eyes. The MCP key is a second door onto the same responses: an assistant holding one can read them, which means those responses reach whichever model that client talks to — the same judgement, made once for everything you can see, so make it deliberately and revoke the key when the project ends.

Compliance with GDPR and the Swiss FADP, your institutional requirements and the conditions of your ethics approval is the researcher's responsibility, not the tool's. When in doubt, ask your data protection officer before fielding, not after.

## 11. Good practices

- **Changing question wording after responses arrive changes what the data means.** The old rows keep their answers and the new column headers do not describe them; where you add or remove an item, past respondents are absent from it. Freeze the instrument before the first real response, and if you must edit afterwards, treat it as two studies and say so in the write-up.
- **Name your questions for your analysis**, not for the editor. `pgt_q3` and `consent` survive the export; the SurveyJS builder's default `question1` does not tell you anything six months later. And never leave choice values as `Item 1`: reordering the options then silently changes what past answers mean.
- **Pilot, then purge.** Walk the whole questionnaire yourself in each language, check the timing looks sane, then purge the answers and the balance before you field. A test run left in the data is indistinguishable from a participant.
- **Circulate the review DOCX, not the JSON.** Reviewers comment on wording; the document exists so that the wording is what they see.
- **Validate after every schema edit**, especially the forward-reference check: a condition reading an answer given on a later page can never be true, and the questionnaire will not tell you — the question just never appears.
- **Design arms with two pools rather than one**, whenever the arms should not be equal in size. Balancing is uniform over combinations and knows no weights, so unequal allocation is expressed by structure, not by a setting.
- **Watch the balance counters mid-field.** *In progress* far above *Completed* means people are opening and leaving, which is a questionnaire problem, not a randomization problem.
- **Prefer separate rating questions to one big matrix**, unless the items genuinely share a scale. A matrix imposes a single agreement scale on dimensions that do not take it well and pays the price in acquiescence bias; separate `rating` items each carry their own anchors and still arrive numeric in the Excel export.
- **Keep the questionnaire short enough to finish.** Nothing is saved before submission, so length is paid for in whole responses, not in missing cells.
- **Close the survey when you are done.** Closing blocks the page and the submit route, so nothing arrives after the cutoff you report.
- **Survey gives you an instrument and a clean table.** Sampling, recruitment, consent, ethics, weighting and analysis are the human work, and none of them is automated here.
