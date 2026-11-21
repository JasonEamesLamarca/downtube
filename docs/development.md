# Development guide

This guide describes how DownTube is built: the runtime shape, component boundaries, the REST API,
the database, the download job lifecycle, and how to test. Setup commands are in
[CONTRIBUTING.md](../CONTRIBUTING.md). The internal contract is
[architecture/CONTRACTS.md](architecture/CONTRACTS.md); this guide summarises it.

## Architecture

One container, one Python process (uvicorn, a single worker):

```
uvicorn ─ FastAPI app (REST /api/*, static SPA /)
        ├─ Scheduler (APScheduler, threads) ──────────► sync jobs
        │     likes · subscriptions · channels · metadata refresh
        └─ DownloadManager (N worker threads, N = max_concurrent_downloads)
              └─ subprocess: yt-dlp (argv list, never a shell)
                    └─ ffmpeg (stream copy only; invoked by yt-dlp to merge)
SQLite (WAL) at /data/downtube.db · temp at /temp · archive at /archive (bind mount)
```

Key properties:

- **Single writer.** All writes go through `db.write_session()`, which holds a process-wide
  `RLock` and commits or rolls back. Reads use `db.read_session()`.
- **No external services.** No Redis, Celery or Postgres. The job queue is the `download_jobs` table.
- **Discovery is separate from media.** The YouTube Data API finds IDs and metadata. yt-dlp
  alone fetches media, and it runs as a subprocess.

## Component boundaries

| Package | Responsibility |
|---|---|
| `config.py`, `settings_service.py` | Environment configuration, and settings stored in the database with defaults |
| `db.py`, `models.py`, `migrations/` | SQLAlchemy models (the persistence contract) and Alembic migrations |
| `schemas.py` | Pydantic models for the REST API (the API contract) |
| `security/crypto.py` | Fernet encryption of tokens; keys derived from `DOWNTUBE_SECRET_KEY` |
| `auth/` | Google OAuth (PKCE, state, nonce), sessions, allowlist, account status |
| `youtube/` | Data API client: auto token refresh, quota accounting, backoff with jitter |
| `sync/` | Likes, subscriptions, channels and metadata refresh engines |
| `downloads/policy.py` | Format selection. Pure functions, no I/O |
| `downloads/adapter.py` | yt-dlp adapter: probe and download, error classification |
| `downloads/validator.py` | ffprobe checks before a file may enter the archive |
| `downloads/jobs.py` | Queue operations shared by sync, REST and workers (enqueue, cancel, pause, resume, retry, caps) |
| `downloads/manager.py` | Worker pool: claim, probe, select, download, validate, finalize, recovery |
| `storage/paths.py` | Path safety, filename sanitising, channel folder naming, disk checks, browsing |
| `scheduler.py` | APScheduler wiring for sync jobs |
| `api/`, `main.py` | FastAPI routes, error rendering, static SPA |
| `runtime_status.py` | Health flags (`worker`, `scheduler`, `archive_writable`) |

The frontend (`frontend/`) is a React single-page app that the backend serves as static files. It only talks to `/api`.

## REST API

All routes are under `/api`, return JSON, and use a session cookie. Mutating requests also send
`X-CSRF-Token`. Errors use `{"error": {"code", "message"}}`. The live, complete description is at
`/api/docs` (Swagger UI) and `/api/openapi.json` (signed-in users only).

| Area | Method and path | Purpose |
|---|---|---|
| Health | `GET /health` | Liveness and component status (no auth) |
| Auth | `GET /auth/login`, `GET /auth/callback` | Start and finish Google sign-in (no auth) |
| Auth | `GET /auth/me`, `POST /auth/logout`, `POST /auth/acknowledge`, `POST /auth/disconnect` | Session, rights acknowledgement, disconnect |
| Dashboard | `GET /dashboard` | Summary counts and status |
| Subscriptions | `GET /subscriptions`, `POST /subscriptions/sync` | List with filters and sort; manual sync |
| Subscriptions | `GET /subscriptions/{channel_id}/estimate` | Size estimate before large backfills |
| Subscriptions | `PATCH /subscriptions/{channel_id}/rule`, `POST /subscriptions/bulk` | Edit one or many channel rules |
| Subscriptions | `POST /subscriptions/{channel_id}/sync` | Sync one channel |
| Likes | `GET /likes`, `POST /likes/sync`, `POST /likes/backfill` | List, sync, archive baseline likes |
| Downloads | `GET /downloads`, `GET /downloads/counts`, `POST /downloads` | Job list, counts, manual enqueue |
| Downloads | `POST /downloads/{id}/retry\|cancel\|pause\|resume` | Job control |
| Library | `GET /library`, `GET /library/{video_id}/stream`, `GET /library/{video_id}/thumbnail` | Archived files, streaming (HTTP Range), thumbnails |
| Settings | `GET /settings`, `PUT /settings` | Read and update settings |
| Storage | `GET /storage`, `GET /storage/browse`, `POST /storage/root` | Free space, folder browsing, archive root |
| Sync | `GET /sync/runs` | Sync history |
| Quota | `GET /quota` | Units used today against the budget |
| Logs | `GET /logs?lines=200` | Recent log lines, admin only, redacted |

Routes return 401 when unauthenticated, 403 for forbidden or CSRF failures, 409 for conflicts or
confirmations required, 422 for validation, 429 for rate limits, and 503 when quota or a
dependency is unavailable. Every query is scoped to the signed-in user.

## Database schema

SQLite, managed by Alembic. Timestamps are timezone-aware UTC.

| Table | Contents |
|---|---|
| `users` | Signed-in people: Google subject, email, display name, picture, admin flag |
| `google_accounts` | Connected Google accounts; encrypted access and refresh tokens; account status |
| `web_sessions` | Server-side sessions; only a SHA-256 hash of the cookie ID is stored |
| `oauth_states` | Pending sign-in requests: state hash, PKCE verifier, nonce; single use, short TTL |
| `channels` | YouTube channel metadata and the stable archive folder name |
| `subscriptions` | A user's subscription to a channel |
| `channel_rules` | Per-user archive rule for a channel: mode, filters, maximum resolution, backfill progress |
| `videos` | One canonical record per YouTube video ID: metadata, duration, availability |
| `video_sources` | Why a user wants a video: liked, from a rule, or manual |
| `media_files` | Validated archive files; exactly one is current per video; superseded copies are kept as rows |
| `download_jobs` | The download queue: status, stage, priority, attempts, errors, progress |
| `sync_runs` | History of sync runs and their outcome |
| `sync_checkpoints` | Resumable positions, such as backfill page tokens |
| `quota_usage` | YouTube Data API units used per Pacific-time day |
| `settings` | Application settings as JSON values, with defaults in `settings_service` |
| `audit_events` | Records of settings changes and similar actions |

Identity rules: YouTube video and channel IDs are primary keys and never change. A video has one
canonical row no matter how many users or sources reference it.

## Download job lifecycle

```
enqueue ─► queued ──pick (priority, next_attempt_at, created_at)──► running
             ▲  ▲                                                   │  stages: probing → downloading
  resume     │  │ retryable error, attempts < max:                  │          → merging → validating
          paused│ next_attempt_at = now + backoff                    │          → finalizing → done
             ▲  └──────────────────────────────────────────────────────┤
             └── pause (kills subprocess, keeps temp)                 │
  retry (user)                                                        ├─► completed   (MediaFile current)
  failed ◄── non-retryable error, or attempts exhausted ──────────────┤
  cancelled ◄── cancel (kills subprocess, deletes temp)               ├─► unsupported (no acceptable stream)
                                                                      └─► skipped     (already archived, or unavailable)
```

| From | To | Trigger |
|---|---|---|
| (new) | `queued` | Enqueue from a sync, a like, a rule or the API |
| `queued` | `running` | A worker claims the highest-priority runnable job |
| `running` | `queued` | Retryable error with attempts left; or restart recovery |
| `running` | `paused` | Pause request; subprocess stopped, temp kept |
| `paused` | `queued` | Resume request |
| `running` | `completed` | Validated file finalized into the archive |
| `running` | `unsupported` | `no_mp4_combination` or no usable stream |
| `running` / `queued` | `skipped` | Video unavailable, or already archived at the target quality |
| `running` | `failed` | Non-retryable error, or attempts exhausted |
| `failed` | `queued` | Retry request |
| any active | `cancelled` | Cancel request; subprocess stopped, temp deleted |

Priorities: liked videos are queued at priority 10, rule-driven new uploads at 50, and backfill at 80.
Lower numbers run first.

Worker concurrency is `max_concurrent_downloads`. A worker only claims a job inside the download
window and when free space exceeds the `min_free_gb` threshold plus the estimated size.

## Testing

Backend tests live in `backend/tests/`. Run them from `backend/` with the dev extra installed.

| Test type | How it runs | Marker |
|---|---|---|
| Foundation tests | Health, migrations, WAL mode, crypto, settings, error rendering, log redaction. Use temporary directories and `DOWNTUBE_DISABLE_BACKGROUND=1` | none |
| Policy tests | Table-driven selection tests with fixture formats (`tests/downloads/yt_fixtures.py`) | none |
| Manager tests | Use `FakeAdapter`, a fake downloader, and a fake validator. No yt-dlp, no network | none |
| Media tests | Real ffmpeg and ffprobe on small generated files | `media` (skipped if the binaries are missing) |
| Integration tests | Real Google credentials and network. The marker is defined; tests use it when they are added | `integration` (skipped in CI) |

```bash
pytest                              # everything that can run locally
pytest -m "not integration"         # what CI runs
pytest -m media                     # only media tests (needs ffmpeg and ffprobe)
pytest tests/downloads/test_policy.py -q
ruff check . && mypy downtube
```

Testing rules:

- Never call the real YouTube API or yt-dlp from unit tests. Mock HTTP with `respx` (a dev dependency).
- Every quality-policy change needs a table case in `test_policy.py`, including the mandatory
  resolution table (see [quality-policy.md](quality-policy.md#10-mandatory-resolution-examples)).
- Use `tmp_path` for archive, temp and data directories. Never write to a real archive from a test.

The frontend uses `npm run test` (unit tests) and `npm run typecheck`.

## Local development

1. Backend: create a virtual environment and run `pip install -e 'backend[dev]'`.
2. Set the environment for local runs. Use `DOWNTUBE_DATA_DIR`, `DOWNTUBE_TEMP_DIR`,
   `DOWNTUBE_LOG_DIR` and `DOWNTUBE_STORAGE_MOUNTS` pointing at folders under your home directory.
   Create a `.env` file that is never committed.
3. Start the backend with uvicorn using the app factory, for example `uvicorn --factory downtube.main:create_app --reload`.
4. Frontend: `npm ci` and `npm run dev` in `frontend/`.
5. Sign-in requires the OAuth client. Use `http://localhost:<port>` as the base URL, as described in
   [installation.md](installation.md#4-create-the-oauth-client).

Schema changes: edit `models.py`, then create an Alembic revision in `migrations/versions/`.
Never edit an applied revision. Keep the contract document in sync.
