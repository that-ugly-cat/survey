# Deploying Survey

Survey is a single FastAPI app backed by one SQLite file. No build step, no external
services. It is multi-user: anyone can register, each account owns its own surveys, and
two-factor authentication (TOTP) is mandatory for everyone.

## 1. Configuration (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **yes, in production** | `change-me` | signs the session cookie — set a long random value |
| `FERNET_KEY` | **yes** | — | encrypts stored TOTP secrets; app won't start without it. Set once and never change (rotating it invalidates every user's 2FA) |
| `ADMIN_EMAIL` | first run | `admin@survey.local` | email of the bootstrap admin created on first start |
| `ADMIN_PASSWORD` | first run | `admin` | bootstrap admin's initial password |
| `DB_PATH` | no | `/data/survey.db` | path to the SQLite file |
| `UPLOADS_PATH` | no | `/data/uploads` | path to per-survey and shared file uploads |
| `PUBLIC_URL` | for MCP | — | the public origin, e.g. `https://survey.example`. The MCP transport checks Host headers against DNS rebinding, so without this every proxied `/mcp` request is refused |

Generate the two keys:

```bash
# session signing key
python3 -c "import secrets; print(secrets.token_hex(32))"
# Fernet key for encrypting TOTP secrets
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

On first start the app creates the bootstrap admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD`
and assigns any pre-existing (owner-less) surveys to it. That admin still has to enrol in
2FA at first login, like every other account. The schema migration is additive — an
existing `survey.db` upgrades in place, nothing is dropped.

One migration carries data rather than only structure. The aggregate randomization counter
was replaced by a per-assignment ledger, so that balancing counts completed responses rather
than page loads; on the first start after that upgrade, the old counters are copied into the
new `assignments` table as completed assignments, once. It is guarded against running twice,
but it cannot distinguish a completed response from an abandoned page load in the old data, so
historical counts carry over slightly overstated. That is the closest reconstruction available,
and it means balancing continues from where it was rather than restarting. Back up before
upgrading (§7) and the whole thing is reversible by restoring the file.

## 2. Local / bare-metal

```bash
pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY, FERNET_KEY, ADMIN_EMAIL / ADMIN_PASSWORD
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 3. Docker

```bash
cp .env.example .env   # set SECRET_KEY, FERNET_KEY, ADMIN_EMAIL / ADMIN_PASSWORD
docker compose up -d --build
```

`docker-compose.yml` maps the app to `127.0.0.1:8001` and mounts `./data` for the SQLite
file and uploads.

## 4. Reverse proxy (HTTPS)

Put it behind a proxy that terminates TLS. Example **Caddy**:

```
yourdomain.example {
    reverse_proxy 127.0.0.1:8001
}
```

Reload after editing: `systemctl reload caddy`.

## 5. Verify

- `https://yourdomain.example/` — public landing (sign in / register)
- `https://yourdomain.example/login` — sign in (then mandatory 2FA)
- `https://yourdomain.example/register` — create an account
- `https://yourdomain.example/s/{slug}` — public survey

The bundled checks can also be run against the built image, in a throwaway container that
leaves the running one alone. They create their own temporary database, so production data is
never touched:

```bash
docker compose run --rm --no-deps --entrypoint sh survey \
  -c "pip install --quiet httpx && cd /app && for t in flow mcp page_order panel panel_migration purge; do python test_$t.py || exit 1; done"
```

`test_page_order.py` needs `node` for its last section; in a container without it that one
section fails while everything else passes.

## 5b. The MCP endpoint

`/mcp` is mounted inside the same app and gated by an API key — one per user, generated
by the user on `/admin` and stored on their row.
Two things have to be right at deploy time:

- **`PUBLIC_URL` set** (§1), or the transport's DNS-rebinding check refuses every proxied
  request.
- **The proxy passes `/mcp` through.** It carries its own key, so it must not sit behind the
  SSO gate: an assistant cannot complete an interactive sign-in. Under `AUTH_MODE=gateway`,
  add `/mcp /mcp/*` to the public prefixes alongside `/s/*` and `/uploads/*`.

Check it answers, with a key from `/admin`:

```bash
curl -s https://yourdomain.example/mcp/ -H "X-API-Key: svy_..." -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 200
```

A 401 means the key is wrong or revoked. A 500 usually means `PUBLIC_URL` does not match the
host the request arrived on.

## 6. Updating

Back up first. Migrations run automatically at startup and one of them rewrites randomization
data (§1), so the backup is the rollback.

```bash
cd /opt/apps/survey
cp data/survey.db "data/survey.db.bak-$(date +%Y%m%d-%H%M%S)"
git pull
docker compose up -d --build
```

`data/` (SQLite + uploads) and `.env` are gitignored — `git pull` never touches them.

Then confirm the container came up and the data survived:

```bash
docker compose ps
docker compose logs --tail 20 survey
sqlite3 data/survey.db "SELECT (SELECT COUNT(*) FROM surveys), (SELECT COUNT(*) FROM responses);"
```

To roll back, stop the container, restore the backup over `data/survey.db`, check out the
previous commit, and rebuild.

## 7. Backups

```bash
cp data/survey.db backup-$(date +%F).db
```

SQLite is a single file — copying it is enough. Back up `data/uploads/` alongside it if
surveys have file attachments.

`data/` is owned by the user running the deployment, not by root, so neither backups nor
exports need `sudo`.

## Behind an SSO gate (`AUTH_MODE=gateway`)

Optional, and off unless you switch it on. In `gateway` the researcher side —
the admin dashboard, the survey editor, the exports — is guarded by an upstream
`forward_auth` gate instead of the local password, `/login` and `/register`
switch themselves off, and "log out" goes to `BORANT_LOGOUT_URL`.

**Respondents never meet any of it, and that is the whole constraint.** A
questionnaire is answered by people who have no account here and must never be
asked for one, so `/s/{slug}` and its submit route stay open.

**`/uploads/*` stays open too, and this is the one that would bite quietly.**
The editor tells the survey author to reference uploaded files from inside a
question — `"imageLink": "/uploads/{slug}/diagram.png"` is in the on-screen help
— and the shared canton/country lists live under `/uploads/shared/`. Gate that
prefix and the questionnaire still loads while every image in it silently
redirects to a sign-in page. No survey references it today, which is exactly
what makes it a trap: it arms itself the next time somebody adds a picture.

**`/mcp` and `/mcp/*` have to be public as well, for a different reason.** It is not open: it
carries its own per-user key and refuses without one. But it cannot sit behind
the gate, because an assistant cannot complete an interactive sign-in — put it
there and every call comes back as a redirect to a login page.

```
survey.example.com {
    @public path /s/* /uploads/* /mcp /mcp/* /login /register /logout /2fa /api/2fa/*
    handle @public {
        import noforge
        import nocookie
        reverse_proxy localhost:8001
    }
    handle {
        import borantid
        reverse_proxy localhost:8001
    }
}
```

Note that `/` is **not** public: it is the researcher landing page with the
login form on it, not a page respondents ever see.

**Ask the gate for `two_factor`, not `one_factor`.** In `local` this app
enforces its own TOTP — the session cookie only reaches scope `full` after the
second factor — so a gate configured for one factor would turn switching it on
into a downgrade. The policy belongs on everything the gate guards here, not
only on `/admin`: the exports under `/admin/surveys/{slug}/export.*` are the
respondents' data, and so is the dashboard that lists it.

**Link the existing researchers before switching on, and read the report:**

```bash
docker exec survey python map_borant.py --map you@example.org=01ABC…
docker exec survey python map_borant.py --report
```

`BORANT_TRUSTED_PROXY` is the second lock and the setting people get wrong.
Under Docker the container sees a bridge gateway, not `127.0.0.1`:

```bash
curl -s -o /dev/null http://127.0.0.1:8001/ && docker logs survey 2>&1 | tail -1
```

Rollback, two lines and no data migration:

```bash
sed -i 's/^AUTH_MODE=gateway/AUTH_MODE=local/' .env
docker compose up -d
```
