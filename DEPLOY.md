# Deploying Survey

Survey is a single FastAPI app backed by one SQLite file. No build step, no external
services.

## 1. Configuration (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **yes, in production** | `change-me` | signs the admin session cookie — set a long random value |
| `ADMIN_PASSWORD` | **yes, in production** | `admin` | admin dashboard password |
| `DB_PATH` | no | `/data/survey.db` | path to the SQLite file |
| `UPLOADS_PATH` | no | `/data/uploads` | path to per-survey and shared file uploads |

Generate a secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 2. Local / bare-metal

```bash
pip install -r requirements.txt
cp .env.example .env   # edit SECRET_KEY / ADMIN_PASSWORD
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 3. Docker

```bash
cp .env.example .env   # edit SECRET_KEY / ADMIN_PASSWORD
docker compose up -d --build
```

`docker-compose.yml` maps the app to `127.0.0.1:8001` and mounts `./data` for the SQLite
file and uploads.

## 4. Reverse proxy (HTTPS)

Put it behind a proxy that terminates TLS. Example **Caddy**:

```
survey.example.org {
    reverse_proxy 127.0.0.1:8001
}
```

Reload after editing: `systemctl reload caddy`.

## 5. Verify

- `https://survey.example.org/login` — admin login
- `https://survey.example.org/s/{slug}` — public survey

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
