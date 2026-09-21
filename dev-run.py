"""
Run the app locally, reading .env by hand.

In production the environment comes from Docker's `env_file`, so the app itself
has no dotenv dependency and no code path that reads a file — this script is the
only thing that does, and only outside the container.

    python dev-run.py            # http://127.0.0.1:8001
    python dev-run.py --reload

The database defaults to ./data/survey.db, which is gitignored, so a local run
never opens the production file by accident.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
os.chdir(HERE)

env_path = HERE / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

if not os.environ.get("SECRET_KEY") or not os.environ.get("FERNET_KEY"):
    sys.exit("SECRET_KEY and FERNET_KEY are missing: copy .env.example to .env first.")

# Local paths, unless .env says otherwise. The container's /data does not exist
# here, and failing on it is a confusing way to learn that.
os.environ.setdefault("DB_PATH", str(HERE / "data" / "survey.db"))
os.environ.setdefault("UPLOADS_PATH", str(HERE / "data" / "uploads"))
(HERE / "data").mkdir(exist_ok=True)

import uvicorn  # noqa: E402

uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8001)),
            reload="--reload" in sys.argv)
