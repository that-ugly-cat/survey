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
  -c "pip install --quiet httpx && cd /app && python test_panel.py && python test_panel_migration.py"
```

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
