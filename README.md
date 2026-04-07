# X Archive Explorer 🚀

[![CI](https://github.com/informigados/x-archive-explorer/actions/workflows/ci.yml/badge.svg)](https://github.com/informigados/x-archive-explorer/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-black?logo=flask&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-Pytest-0A9EDC?logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

**Local-first explorer for archived X (Twitter) posts and replies.**

Import your official X export, index everything locally, search with filters, and export results.  
No scraping as the primary strategy.

## ✨ Current Status

Production-oriented baseline implemented with:

- 🔐 Local admin authentication
- 🔑 Sign in with email or username
- 👥 Full user management (create/edit/delete/change password)
- 🧭 Role-based access control (`admin` / `viewer`)
- 🛡️ Protected default user (cannot be deleted; password can be changed)
- 🚦 Login rate limiting (IP-based temporary block after repeated failures)
- 🗂️ Archive/project creation
- 📦 Official X archive import (`.zip` / `.json`)
- 🔁 Reimport modes: `merge` and `overwrite`
- 🧩 Parser/normalizer for posts, replies, hashtags, mentions, URLs, media
- 🗄️ SQLite storage + FTS5 full-text search
- 🔎 Rich filters (date range, author, language, replies/media/links only)
- 🧵 Conversation view by `conversation_id`
- 📤 CSV / JSON / HTML export
- 🧰 Async import queue with worker, retry, progress
- 🖼️ Local media extraction/preview from archive ZIP
- 🌐 Optional sync via official X API (`username`/`user_id`, incremental `since_id`)
- 🧠 Adaptive API rate-limit handling per endpoint + retry/backoff
- 📚 Advanced conversation reply pagination (`next_token`, multiple pages per conversation)
- ⏱️ Conversation reply time-window filter (`start_time`, configurable in days)
- 📈 Operational logs + persisted operational metrics (`operation_metrics`)
- 🩺 Runtime probes: `/healthz` (liveness) and `/readyz` (database readiness)
- 🧷 Request correlation with `X-Request-ID` on every response
- 🌍 Default UI in Brazilian Portuguese with language switch (Portuguese/English)

## 🧱 Stack

- Backend: `Flask`
- Database: `SQLite` + `FTS5`
- Frontend: `Jinja2` + CSS
- Auth: `Flask-Login`
- Security: `Flask-WTF` (CSRF), upload validation, upload size limits

## 📁 Project Structure

```text
x-archive-explorer/
├─ app/
├─ instance/
├─ uploads/
├─ migrations/
├─ tests/
├─ run.py
├─ start-x-archive-explorer.bat
└─ docker-compose.yml
```

## ⚙️ Quick Start (Windows, Smart Launcher)

Use the intelligent launcher:

```bat
start-x-archive-explorer.bat
```

What it does automatically:

- Creates `.venv` if missing
- Installs dependencies from `requirements.txt`
- Finds a free port between `5000` and `5100`
- Reconciles legacy DB state (`db stamp head` when needed)
- Runs DB migrations (`flask db upgrade`)
- Prints current default login variables for local bootstrap
- Opens browser automatically
- Starts the app on the selected port using `waitress` (production-grade WSGI server)

Default local bootstrap access:

- Username: `admin`
- Email: `admin@localhost`
- Password: **no hardcoded default** (`change-this-password` is no longer used)
- In development, when `XAE_ADMIN_PASSWORD` is not set, the launcher generates a strong bootstrap password automatically
- Generated bootstrap password is saved to: `instance/bootstrap-admin-password.txt` (restricted local permissions)
- Sign in with either the username or the email.

Security recommendation:

- Change the default password immediately after first sign-in.
- Go to **Users** in the top navigation and update the password of the default account.

If the default password no longer works on an existing database, reset it with:

```bash
flask --app run.py reset-user-password --identity admin@localhost --password <new-password>
```

If you upgraded code and the app reports outdated schema, run:

```bash
flask --app run.py db upgrade
```

## 🛠️ Manual Local Setup

1. Create and activate virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Apply migrations:

```bash
flask --app run.py db upgrade
```

4. Set environment variables:

```bash
set XAE_SECRET_KEY=your-strong-key
set XAE_ADMIN_USERNAME=admin
set XAE_ADMIN_EMAIL=admin@localhost
set XAE_ADMIN_PASSWORD=your-strong-password
set XAE_AUTO_CREATE_SCHEMA=false
```

5. Run:

```bash
python run.py
```

6. Open:

```text
http://127.0.0.1:5000
```

## 🐳 Docker

```bash
docker compose up --build
```

Services:

- `x-archive-explorer` (web, served by Gunicorn)
- `x-archive-worker` (queue worker)
- Both containers run `flask db upgrade` automatically on startup before serving traffic.

Important:

- In production mode, `docker-compose.yml` requires `XAE_SECRET_KEY` and `XAE_ADMIN_PASSWORD`.
- If these variables are missing, Docker Compose will fail fast at startup.

## 🚢 Production Deployment (Recommended)

1. Copy `.env.production.example` (or `.env.example`) to `.env.production` and set strong values:

```bash
XAE_ENV=production
XAE_SECRET_KEY=<64+ char random secret>
XAE_ADMIN_PASSWORD=<strong bootstrap password>
XAE_SECURE_COOKIES=true
XAE_TRUST_PROXY_HEADERS=true
XAE_ENABLE_HSTS=true
```

2. Start services with the production env file:

```bash
docker compose --env-file .env.production up -d --build
```

3. Verify runtime probes:

```bash
curl http://127.0.0.1:5000/healthz
curl http://127.0.0.1:5000/readyz
```

4. Sign in using:

- Username: `admin` (or your `XAE_ADMIN_USERNAME`)
- Email: `admin@localhost` (or your `XAE_ADMIN_EMAIL`)
- Password: value from `XAE_ADMIN_PASSWORD`

5. Immediately rotate the bootstrap password in **Users** after first login.

## 📥 How to Bring X Data In

You have two supported paths:

### 1) Account Export Import (Recommended baseline)

- In X, request your account archive.
- Then import the `.zip`/`.json` in X Archive Explorer.

To request archive in the X interface:

- English UI path: **More > Settings and privacy > Your account > Download an archive of your data**
- Portuguese UI path: **Mais > Configurações e Privacidade > Sua Conta > Faça download de um arquivo com seus dados**

Official references:

- https://help.x.com/en/managing-your-account/accessing-your-x-data
- https://help.x.com/en/managing-your-account/how-to-download-your-x-archive

### 2) Official X API Sync (Optional)

- Enable API sync in env variables.
- Open archive detail.
- Click **Sync API X**.
- Provide `username` or `user_id`.
- Optional incremental sync (`since_id`).
- Optional conversation reply collection with multiple pages per conversation.
- Optional reply time window control (in days).

Official API references:

- https://docs.x.com/x-api/posts/search/introduction
- https://docs.x.com/x-api/posts/search-recent-posts
- https://docs.x.com/x-api/users/get-posts
- https://docs.x.com/x-api/getting-started/pricing

Developer rules:

- https://docs.x.com/developer-guidelines

## 🔄 Main Usage Flow

1. Sign in
2. Create archive/project
3. Import official export (`merge` or `overwrite`)
4. Search with filters
5. Open details and conversation threads
6. Export results
7. Optionally sync with X API

## 👤 User Roles

- `admin`: full access (archives import/sync, user management, retry operations)
- `viewer`: read-only operations (dashboard/search/archive details) + own password change
- Default system account is protected and always preserved as administrator

## 🌐 Language

- Default language: **Português (Brasil)**
- Language switch available in the top bar
- English is supported as an alternate UI language

## 🧵 Worker Commands

Run one queued job:

```bash
flask --app run.py run-import-worker --once
```

Run continuously:

```bash
flask --app run.py run-import-worker
```

## 📊 Operational Metrics

Authenticated JSON endpoint:

```text
/ops/metrics
```

Prune old metrics:

```bash
flask --app run.py prune-op-metrics --days 90
```

## 🔐 Security Notes

- Set a strong `XAE_SECRET_KEY` in production
- The app refuses to boot in non-development with missing/weak `XAE_SECRET_KEY`
- Store `XAE_ADMIN_PASSWORD` securely
- Do not expose `instance/x_archive.db` publicly
- Run behind HTTPS reverse proxy (Nginx/Caddy)
- Enable trusted proxy headers only behind your own reverse proxy (`XAE_TRUST_PROXY_HEADERS=true`)
- Keep secure cookies enabled in production (`XAE_SECURE_COOKIES=true`)
- Enable HSTS in production (`XAE_ENABLE_HSTS=true`) with TLS end-to-end
- Use `X-Request-ID` in client/proxy logs to correlate app requests end-to-end
- Back up SQLite database regularly

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## ✅ GitHub Publish Checklist

Before pushing:

1. Run `python -m pytest -q` and confirm all tests pass.
2. Ensure `.env` files are not committed (`.env.example` and `.env.production.example` are tracked templates).
3. Ensure `instance/` and `uploads/` contain only `.gitkeep` (no local data dumps).
4. Verify production values for `XAE_SECRET_KEY`, `XAE_ADMIN_PASSWORD`, and HTTPS/proxy settings.
5. Apply migrations in target environment: `flask --app run.py db upgrade`.

## 🌱 Environment Variables (Key)

- `XAE_IMPORT_ASYNC=true|false`
- `XAE_IMPORT_MAX_RETRIES=2`
- `XAE_IMPORT_WORKER_SLEEP_SECONDS=2`
- `XAE_IMPORT_STALLED_JOB_MINUTES=30`
- `XAE_LOGIN_RATE_LIMIT_WINDOW_SECONDS=300`
- `XAE_LOGIN_RATE_LIMIT_MAX_ATTEMPTS=5`
- `XAE_LOGIN_RATE_LIMIT_BLOCK_SECONDS=300`
- `XAE_ARCHIVE_MEMBER_MAX_FILE_MB=25`
- `XAE_ARCHIVE_MAX_TOTAL_MB=200`
- `XAE_STORE_RAW_JSON=true|false`
- `XAE_EXPORT_MAX_POSTS=5000`
- `XAE_MEDIA_MAX_FILE_MB=25`
- `XAE_MEDIA_MAX_TOTAL_MB=500`
- `XAE_MEDIA_MAX_FILES=5000`
- `XAE_USER_ITEMS_PER_PAGE=25`
- `XAE_ARCHIVE_DETAIL_ITEMS_PER_PAGE=20`
- `XAE_CONVERSATION_ITEMS_PER_PAGE=50`
- `XAE_ARCHIVE_FILTER_MAX_OPTIONS=300`
- `XAE_DASHBOARD_ARCHIVES_PER_PAGE=8`
- `XAE_DASHBOARD_CACHE_TTL_SECONDS=30`
- `XAE_ADMIN_USERNAME=admin`
- `XAE_ADMIN_EMAIL=admin@localhost`
- `XAE_ADMIN_PASSWORD=<set-strong-password>`
- `XAE_X_API_ENABLED=false`
- `XAE_X_API_BASE_URL=https://api.x.com/2`
- `XAE_X_API_BEARER_TOKEN=...`
- `XAE_X_API_TIMEOUT_SECONDS=20`
- `XAE_X_API_MAX_RETRIES=2`
- `XAE_X_API_BACKOFF_SECONDS=1.0`
- `XAE_X_API_MAX_BACKOFF_SECONDS=10.0`
- `XAE_X_API_ADAPTIVE_RATE_LIMIT_ENABLED=true|false`
- `XAE_X_API_RATE_LIMIT_MAX_WAIT_SECONDS=60`
- `XAE_OPS_METRICS_RETENTION_DAYS=90`
- `XAE_LOG_LEVEL=INFO|WARNING|ERROR`
- `XAE_ACCESS_LOG_ENABLED=true|false`
- `XAE_REQUEST_ID_HEADER=X-Request-ID`
- `XAE_REQUEST_ID_ACCEPT_INCOMING=true|false`
- `XAE_GUNICORN_WORKERS=2`
- `XAE_GUNICORN_THREADS=2`
- `XAE_GUNICORN_TIMEOUT=60`
- `XAE_SECURE_COOKIES=true|false`
- `XAE_TRUST_PROXY_HEADERS=true|false`
- `XAE_PROXY_FIX_X_FOR=1`
- `XAE_PROXY_FIX_X_PROTO=1`
- `XAE_PROXY_FIX_X_HOST=0`
- `XAE_PROXY_FIX_X_PORT=0`
- `XAE_PROXY_FIX_X_PREFIX=0`
- `XAE_FORCE_HTTPS=true|false`
- `XAE_ENABLE_HSTS=true|false`
- `XAE_HSTS_MAX_AGE_SECONDS=31536000`
- `XAE_HSTS_INCLUDE_SUBDOMAINS=true|false`
- `XAE_HSTS_PRELOAD=true|false`
- `XAE_PREFERRED_URL_SCHEME=https|http`
- `XAE_PORT=5000` (optional, app runtime port)

## 🧭 Positioning

> A lightweight local-first explorer for archived X posts and replies.  
> Import, search, filter, inspect, sync, and export with speed and clarity.

## 📝 Changelog

### 2026-04-07 (1.0.0)

- Initial release.

## 🤝 Contributing

Contributions are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## 👥 Authors

- [INformigados](https://github.com/informigados)
- [Alex Brito](https://github.com/alexbritodev)

## 📄 License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.
