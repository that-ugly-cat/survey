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
- **Panel recruitment** — enter respondents from a demoscopic provider (Bilendi, Dynata,
  Cint, Toluna…) and return them so the provider can credit their participation: a
  configurable token parameter read from the entry URL, three return URLs (complete,
  screenout, quota full), one-use enforcement per token, and an `_outcome` field the
  questionnaire can set to route consent refusals and quota exits to the right URL.
- **File uploads** per survey, plus a shared folder for static assets.
- **Language selector** (EN/DE/FR/IT) with browser autodetect and visibility driven by which
  translations exist in the schema.
- **Per-survey manage page** — stats (responses, last response), shareable public URL with a
  ready-made **QR code**, exports, questionnaire tools, and a danger zone that spells out
  what a delete destroys. The survey list keeps only the everyday actions.
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

Run the checks with no test framework and no server:

```bash
python test_panel.py && python test_panel_migration.py
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
- Panel mode is off until a token parameter is set on the survey's manage page. Once on, the
  questionnaire cannot be entered without a valid token; append `?preview=1` while signed in
  as the owner to walk it yourself. A panel token is a pseudonymous identifier the provider
  can trace back to a person, so a study using panel mode should say so in its ethics
  application and in its participant information.

## License

Copyright (C) 2026 Giovanni Spitale. Licensed under AGPL-3.0 — fork it, host it, sell access
to it, but keep it closed-source and you're in violation. No SaaS forks that don't share
back. See [LICENSE](LICENSE).
