<p align="center">
  <b>A lightweight, multi-user survey tool for research data collection.</b><br>
  Multi-step questionnaires, per-user dashboards, mandatory 2FA, CSV/Excel/JSON export.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: AGPL v3" src="https://img.shields.io/badge/License-AGPLv3-blue.svg"></a>
</p>

---

Survey is a self-hosted questionnaire platform built for small-to-medium academic studies.
Users register their own accounts, create and manage surveys from a private dashboard, and
share them at a public `/s/{slug}` URL; responses land in SQLite and export to CSV, Excel,
or JSON.

## Features

- **Multi-user accounts** — open registration, each user owns their own surveys and
  responses; nothing is shared across accounts.
- **Mandatory two-factor auth** — TOTP (Ente Auth, Google Authenticator, Aegis…) with
  one-time backup codes; secrets encrypted at rest.
- **Admin dashboard** — manage registered users: enable/disable, reset 2FA, issue a
  temporary password.
- **Multi-page questionnaires** with per-page and total timing recorded per response.
- **Canton/country reference data** (EN/DE/FR/IT) — upload once via the admin file manager,
  reuse across surveys.
- **Balanced randomization**: multiple independent pools per survey, each with sampleable
  pages, show-count and balancing counters. Balancing is driven by *completed responses*
  rather than page loads, plus a one-hour window of in-flight assignments so that concurrent
  starts spread out; an abandoned session stops skewing the arms once it ages out.
  A pool can also **counterbalance presentation order**: alongside the condition it assigns,
  an optional page-order map reshuffles named pages into the positions they already occupy,
  so a crossover arm needs no duplicate pages — and therefore no duplicate question names.
- **MCP surface** at `/mcp` — read a questionnaire, walk every randomization arm, validate
  the schema, and write back, from inside an assistant conversation. Two tools do the work
  the admin UI cannot: `preview_flow` reports what a participant in each arm actually sees,
  and `validate_survey` reports what is broken. Both read the schema, so neither needs the
  questionnaire open in a browser. See [The MCP surface](#the-mcp-surface).
- **Panel recruitment** — enter respondents from a demoscopic provider (Bilendi, Dynata,
  Cint, Toluna…) and return them so the provider can credit their participation: a
  configurable token parameter read from the entry URL, three return URLs (complete,
  screenout, quota full), one-use enforcement per token, and an `_outcome` field the
  questionnaire can set to route consent refusals and quota exits to the right URL.
- **File uploads** per survey, plus a shared folder for static assets.
- **Language selector** (EN/DE/FR/IT) with browser autodetect and visibility driven by which
  translations exist in the schema.
- **Per-survey manage page** — stats (responses, last response), shareable public URL with a
  ready-made **QR code**, exports, questionnaire tools, panel settings, and a danger zone that
  spells out what a delete destroys. The survey list keeps only the everyday actions. Help
  modals behind each **?** document panel recruitment, randomization and file uploads in place,
  so the settings do not need a manual open in another window.
- **Response exports: CSV, Excel, JSON** — CSV and Excel are guaranteed to share the same
  columns; the Excel file adds a frozen header, autofilter, native number types, and forces
  formula-looking strings to text so open-ended answers can't execute on the analyst's machine.
- **Questionnaire review export (DOCX)** — renders the survey itself as a Word document for
  circulating to colleagues: primary-language texts, answer formats per question type,
  branching logic in plain English, the survey's randomization pools, and per-element
  translation coverage flags (present / partial / missing).
- Built on the [SurveyJS Form Library](https://surveyjs.io/) (MIT).

## Quick start

```bash
git clone https://github.com/that-ugly-cat/survey.git
cd survey
pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY, FERNET_KEY, ADMIN_EMAIL / ADMIN_PASSWORD
uvicorn main:app --reload
```

Open http://localhost:8000/ for the landing page, then register or sign in. The first
login walks you through 2FA enrolment.

## Running a panel field

Demoscopic providers (Bilendi, Dynata, Cint, Toluna…) hand each respondent a single-use
identifier in the link they open, and expect the questionnaire to send them back to a return
URL carrying it. That return leg is how the provider learns the person finished, and therefore
how the person gets paid; a field that collects the token but never bounces the respondent back
cannot be reconciled or invoiced.

Set it up from the survey's manage page, under **Panel recruitment**:

1. **Token parameter** — the query parameter carrying the respondent id. Providers disagree
   on the name (`RID` for Bilendi and Cint, `psid` for Dynata, `tid` for Toluna) and on its
   capitalisation, so the match is case-insensitive. Setting this field switches panel mode on;
   clearing it returns the survey to an open link.
2. **Return URLs** — the provider supplies three, and all three are used: complete, screenout,
   and quota full. The token is appended as `?param=token`, or substituted for a `{token}`
   placeholder when the provider's URL carries the id mid-path.
3. **Entry URL** — hand the provider `https://…/s/{slug}?RID=[respondent id]`.

From there:

- Anyone arriving without a valid token is stopped before the questionnaire and sent to the
  screenout URL, so a respondent nobody can credit does not spend ten minutes answering. Append
  `?preview=1` while signed in as the owner to walk the questionnaire yourself; preview
  submissions are stored but never bounce to the provider.
- A token that has already produced a response can neither re-enter nor submit again. This is
  enforced by a uniqueness constraint in the database rather than a check before the insert, so
  two simultaneous submissions cannot both slip through.
- The questionnaire routes non-completions by setting a field named `_outcome` to `screenout`
  or `quotafull` (the default when absent is `complete`). A consent gate is the usual case:

  ```json
  "triggers": [{
    "type": "setvalue",
    "expression": "{consent} empty",
    "setToName": "_outcome",
    "setValue": "screenout"
  }]
  ```

  The response is stored either way, so refusals and screenouts stay in the data rather than
  vanishing.
- The token appears as `_panel_token` in the CSV, Excel and JSON exports, which is the column
  you reconcile against the provider's delivery file.

> A panel token is a pseudonymous identifier: the provider can trace it back to a person. A
> study recruiting this way should say so in its ethics application and its participant
> information, and should not describe its responses as carrying no identifying metadata.

## Checks

Five self-contained scripts, no test framework and no running server. They build their own
temporary database, so they never touch real data:

```bash
python test_flow.py && python test_mcp.py && python test_page_order.py
python test_panel.py && python test_panel_migration.py
```

`test_flow.py` covers expression evaluation, arm preview and schema validation — no database
and no server, because that module needs neither. `test_mcp.py` covers the API-key gate,
ownership, and the write guards. `test_page_order.py` covers counterbalancing, including the
reorder snippet itself, extracted from `templates/survey.html` and run in node so the test
cannot drift from the code it checks. `test_panel.py` covers panel entry and return, one-use
tokens, outcome routing, and the assignment ledger. `test_panel_migration.py` upgrades a
database in the pre-panel shape and checks the backfill, then compiles and renders every
template.

To run them against a built image without disturbing the running container:

```bash
docker compose run --rm --no-deps --entrypoint sh survey \
  -c "pip install --quiet httpx && cd /app && for t in flow mcp page_order panel panel_migration; do python test_$t.py || exit 1; done"
```

`test_page_order.py` needs `node` on the path for its last section; without it that one
section fails while everything else passes.

## The MCP surface

A questionnaire is edited far more often than it is filled in, and every edit raises the same
three questions: what does it look like now, what would a participant in each arm actually
walk through, and what did the last change break. The admin UI answers the first. `/mcp`
answers all three, from inside the conversation where the editing is happening.

**Getting a key.** Generate one on `/admin`, under **MCP key**: one per user, held on the
user row. A key is a credential of a person, not of the installation — every call resolves to
its owner and then goes through the same ownership check the web app applies, so it reaches
that user's surveys and nothing else, and a survey the caller cannot manage reports "not
found" rather than "forbidden". Disabling the account closes the key with it; there is no
second door. Regenerating replaces the key immediately, which also means revocation is
all-or-nothing: anything else still configured with the old one stops working too.

**Connecting.** `https://your-domain/mcp` with an `X-API-Key` header, or
`https://your-domain/mcp/k/<key>` for clients that cannot send headers. `PUBLIC_URL` must be
set, or the transport's DNS-rebinding check refuses every proxied request.

**Reading.** `list_surveys`, `get_survey`, `get_schema`, `randomization_status`,
`get_responses` (full payloads, including `_conditions` and `_timing`), and `response_stats`,
whose `never_answered` list is the quick way to find a branch nobody reaches.

**Two tools that read the schema instead of the browser.** `preview_flow` returns, for every
arm at once, the pages a participant walks through in the order they are shown and the
questions on each. Visibility that depends on an answer cannot be decided in advance, so those
come back marked `conditional` with the expression that governs them, rather than guessed at —
pass `answers` to pin a value and see what it opens. `validate_survey` reports duplicate
question names, conditions reading a field nothing defines or one answered on a later page,
placeholder choice values that make an export unreadable, pool pages that do not exist, and
arms that reach no questions.

**Writing, with guards.** `create_survey`, `update_schema`, `set_pool`, `delete_pool`,
`reset_counters`, `set_active`. Three guards matter:

- A schema with structural errors is refused. Pass `force` to override.
- Editing the schema of a survey that already holds responses is refused unless forced: those
  answers were given to the old wording, and reinterpreting them silently is the one mistake a
  backup does not undo.
- Deleting surveys and deleting responses are **not exposed at all**. They stay in the web
  admin, where a human is holding the mouse.

`set_pool` resets that pool's balance counters, exactly as saving from the web admin does, and
says so in its result.

## Stack

FastAPI · SQLite · Jinja2 · [SurveyJS](https://surveyjs.io/) on the frontend. No build step.
Passwords are bcrypt-hashed; sessions are signed cookies (itsdangerous); TOTP is pure-stdlib
and its secrets are Fernet-encrypted at rest.

```
main.py           — routes (auth, 2FA, admin, survey render/submit, uploads, exports)
auth.py           — password hashing + signed session cookies (pending → full scope)
totp.py           — TOTP + backup codes (RFC 6238, stdlib)
crypto.py         — Fernet encryption for stored TOTP secrets
review_export.py  — questionnaire → review DOCX (translations, logic, randomization)
templates/        — landing, twofa, admin, manage, admin_users, profile, survey, …
static-data/      — reference JSON (cantons, countries) to upload via the file manager
test_panel.py     — end-to-end checks: panel entry/return, one-use tokens, assignment ledger
test_panel_migration.py — migrating an existing database, plus template rendering
```

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for production setup (environment variables, Docker, reverse
proxy, backups).

## Tech notes

- Set `SECRET_KEY` and `FERNET_KEY` in production; `ADMIN_EMAIL` / `ADMIN_PASSWORD` seed the
  bootstrap admin on first run. `FERNET_KEY` must stay stable — rotating it invalidates every
  user's 2FA.
- 2FA is mandatory: a fresh account gets a short-lived pending session until it enrols.
- The whole database is a single SQLite file — back up by copying it.
- Survey definitions are created/edited from the admin dashboard, not shipped in this repo.
- Panel mode is off until a token parameter is set on the survey's manage page, and turning it
  on closes the survey to anyone without a valid token. See
  [Running a panel field](#running-a-panel-field).
- Randomization balances on completed responses, plus assignments issued in the last hour so
  that simultaneous starts spread out. An abandoned page load therefore stops skewing the arms
  once it ages out, which matters under panel traffic where abandonment is high. Upgrading from
  a version that counted page loads migrates the old counters once — see
  [DEPLOY.md](DEPLOY.md#1-configuration-environment-variables).

## License

Copyright (C) 2026 Giovanni Spitale. Licensed under AGPL-3.0 — fork it, host it, sell access
to it, but keep it closed-source and you're in violation. No SaaS forks that don't share
back. See [LICENSE](LICENSE).

## Optional: behind an SSO gate

`AUTH_MODE=gateway` hands researcher identity to an upstream `forward_auth` gate
instead of the local password, and `/login` and `/register` switch themselves
off.

**Respondents never meet it.** A questionnaire is answered by people with no
account here, so `/s/{slug}`, its submit route and `/uploads/*` all stay open —
the last one matters because questions can reference uploaded files, and the
shared canton/country lists live there.

If you turn it on, configure the gate for **two factors**: this app enforces its
own TOTP in `local` mode, so a gate set to one would make the switch a
downgrade rather than a move.

`local` is the default and stays fully supported. Details, and the one-off
linking script to run first, in `DEPLOY.md`.
