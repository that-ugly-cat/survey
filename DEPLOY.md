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

## 6. Updating

```bash
cd /opt/apps/survey
git pull
docker compose up -d --build
```

`data/` (SQLite + uploads) and `.env` are gitignored — `git pull` never touches them.

## 7. Backups

```bash
cp data/survey.db backup-$(date +%F).db
```

SQLite is a single file — copying it is enough. Back up `data/uploads/` alongside it if
surveys have file attachments.
