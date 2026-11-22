# DownTube — Architecture & Internal Contracts

This is the single source of truth shared by all contributors (human or agent).
Code-level contracts live in `backend/downtube/models.py` (DB) and
`backend/downtube/schemas.py` (REST). Feasibility evidence: `docs/research/feasibility.md`.

## 1. Runtime shape

One container, one Python process (uvicorn, **1 worker**):

```
uvicorn ─ FastAPI app (REST /api/*, static SPA /)
        ├─ Scheduler (APScheduler BackgroundScheduler, threads) → sync jobs
        └─ DownloadManager (N worker threads, N = max_concurrent_downloads)
              └─ subprocess: yt-dlp (argv list, never shell) → ffmpeg (stream copy only)
SQLite (WAL) at /data/downtube.db  ·  temp at /temp  ·  archive at /archive (bind mount)
```

* **Single-writer strategy**: one process; all writes go through `db.write_session()`, which
  holds a process-wide `threading.RLock` and commits/rolls back. Reads use `db.read_session()`.
  `PRAGMA journal_mode=WAL, synchronous=NORMAL, busy_timeout=10000, foreign_keys=ON`.
* No Redis/Celery/Postgres. The queue is the `download_jobs` table.

## 2. Module ownership (who edits what)

| Module | Owner (build phase) | Purpose |
|---|---|---|
| `config.py`, `db.py`, `models.py`, `schemas.py`, `settings_service.py`, `security/crypto.py`, `migrations/` | foundation (lead) | shared; others must not change signatures without the lead |
| `storage/paths.py`, `downloads/jobs.py` | foundation (lead) | path safety/naming/browse; queue ops: `enqueue`, `compute_effective_cap`, cancel/pause/resume/retry, control signals |
| `downloads/` | download agent | policy, yt-dlp adapter, validator, job manager |
| `auth/`, `youtube/`, `sync/`, `scheduler.py`, `api/` | backend agent | OAuth, API client, sync engines, REST |
| `frontend/` | frontend agent | React SPA |
| `Dockerfile`, `docker-compose.yml`, `.github/` | devops agent | packaging, CI |

## 3. Service interfaces (Python)

```python
# settings_service.py (lead)
DEFAULTS: dict[str, Any]
def get_settings(s: Session) -> SettingsOut
def update_settings(s: Session, upd: SettingsUpdate, user_id: int | None) -> SettingsOut
def get_value(s: Session, key: str) -> Any

# security/crypto.py (lead)
def encrypt(plaintext: str) -> str ; def decrypt(token: str) -> str   # Fernet, key from DOWNTUBE_SECRET_KEY

# storage/paths.py (download agent)
class StorageError(Exception)
def allowed_mounts() -> list[Path]                         # from settings.storage_mounts (env)
def resolve_within_mounts(p: str | Path) -> Path          # realpath; raises StorageError on escape/symlink escape
def sanitize_component(name: str, max_bytes: int = 180) -> str   # unicode-safe, no / \ : * ? " < > | control chars, no leading dots, no trailing dot/space, NFC
def channel_folder_name(s: Session, channel: Channel) -> str     # assigns & persists Channel.folder_name once; collision → "Title [UCid]"
def video_filename(video: Video, ext: str) -> str         # "YYYY-MM-DD - Title [VIDEOID].ext" (date = published_at UTC; "0000-00-00" if unknown)
def disk_usage(path: Path) -> tuple[int, int]             # (total, free)
def has_space_for(root: Path, needed_bytes: int, min_free_bytes: int) -> bool
def browse(path: str | None) -> BrowseResult

# downloads/policy.py (download agent) — PURE, no I/O
@dataclass Fmt: format_id, ext, vcodec, acodec, width, height, fps, tbr, abr, filesize, dynamic_range, language, language_preference, format_note, protocol, has_drm
@dataclass Selection: video: Fmt, audio: Fmt | None, container: "mp4"|"mkv", effective_cap: int, source_best_height: int, reasons: list[str]
@dataclass Unsupported: reason: str, source_best_height: int | None
def parse_formats(info: dict) -> list[Fmt]
def effective_cap(requested: int | None) -> int           # None → 2160; min(requested, 2160)
def short_side(f: Fmt) -> int                              # min(width, height); portrait-safe
def select_formats(fmts, cap, *, audio_pref, prefer_hdr, compat_profile, mkv_fallback) -> Selection | Unsupported
def best_available_height(fmts, cap=2160) -> int | None

# downloads/adapter.py (download agent)
class DownloaderAdapter(Protocol):
    def probe(self, video_id: str) -> dict                 # yt-dlp -J (info json); raises DownloadError(code, retryable)
    def download(self, video_id, selection, out_dir, *, rate_limit_kbps, on_progress, cancel_event) -> Path
class YtDlpAdapter(DownloaderAdapter)                      # subprocess, --newline --progress-template json
class DownloadError(Exception): code: str; retryable: bool

# downloads/validator.py
@dataclass Probe: container, width, height, fps, vcodec, acodec, duration_s, size_bytes, is_hdr
def ffprobe(path: Path) -> Probe                           # raises ValidationError
def validate(path, selection, expected_duration_s) -> Probe   # rejects: no video stream, height > cap, height > selected source height (upscale guard), duration mismatch > max(2s, 2%), size 0

# downloads/manager.py
class DownloadManager:
    def start(self) / stop(self, timeout)                  # graceful: running jobs → queued (resume on restart)
    def wake(self)                                         # new work available
    def cancel(self, job_id) / pause(self, job_id) / resume(self, job_id) / retry(self, job_id)
def enqueue(s, video_id, *, user_id, max_height, priority, kind="archive") -> DownloadJob | None   # dedupe-aware
def compute_effective_cap(s, video_id) -> int              # see §5 precedence
def recover_on_startup(s)                                  # running → queued; clean orphan temp dirs

# youtube/client.py (backend agent)
class YouTubeClient:  # httpx, auto token refresh, quota accounting, backoff+jitter
    def liked_videos(page_token=None) -> (items, next_token)        # videos.list myRating=like, 50/page (API cap: newest 1000)
    def subscriptions(page_token=None) -> (items, next_token)
    def channels(ids: list[str]) -> list[dict]                      # ≤50 ids per call
    def playlist_items(playlist_id, page_token=None) -> (items, next_token)
    def videos(ids: list[str]) -> list[dict]                        # ≤50; parts snippet,contentDetails,status,liveStreamingDetails
class QuotaExceeded(Exception); class AuthRevoked(Exception)

# sync/likes.py, sync/subscriptions.py, sync/channels.py (backend agent)
def sync_likes(user_id, *, full: bool, trigger) -> SyncRun
def sync_subscriptions(user_id, trigger) -> SyncRun
def sync_channel(user_id, channel_id, trigger) -> SyncRun
def refresh_stale_metadata(max_age_days=25)               # API data retention: refresh or purge before 30 days
```

### 3a. Implemented shared modules (read the code — it is the contract)

* `downloads/jobs.py`: `enqueue(s, video_id, *, user_id, priority, max_height=None)`,
  `compute_effective_cap`, `needs_upgrade`, `cancel_job/pause_job/resume_job/retry_job` (raise
  `JobStateError`), worker control: `signal/pop_signal/peek_signal(job_id)` ("cancel"|"pause"),
  `set_wake_callback(fn)` / `wake()`. A running job is stopped by the worker when it sees a signal.
* `storage/paths.py`: `resolve_within_mounts`, `safe_join`, `sanitize_component`,
  `channel_folder_name`, `video_basename/video_filename`, `disk_usage`, `has_space_for`, `is_writable`, `browse`.
* `downloads/manager.py` must expose module functions `startup()`, `shutdown()`, `get_manager()`,
  and set `runtime_status` flags `worker` and `archive_writable`. `scheduler.py` exposes
  `start()`, `stop()`, `reschedule()` and sets flag `scheduler`.

## 4. Download job state machine

```
            enqueue
              │
              ▼
  ┌──────► queued ──pick (priority ASC, next_attempt_at ≤ now, created_at)──► running
  │          ▲  ▲                                                             │
  │  resume  │  │ retryable error (attempts < max): next_attempt_at =        │ stages: probing → downloading
  │          │  │   now + min(2^attempts·60s, 6h) · jitter(0.8–1.2)          │   → merging → validating → finalizing
  │       paused └──────────────────────────────────────────────────────────── │
  │          ▲ pause (kills subprocess, keeps temp for --continue)            │
  │          └────────────────────────────────────────────────────────────────┤
  │ retry (user)                                                              ├─► completed (MediaFile current)
  └── failed ◄── non-retryable error, or attempts exhausted ─────────────────┤
      cancelled ◄── cancel (kills subprocess, deletes temp)                   ├─► unsupported (no acceptable stream/container)
                                                                              └─► skipped (already archived ≥ target, or unavailable)
```

* Error codes: `unavailable` (private/deleted/removed — non-retryable, skipped), `geo_restricted`,
  `drm`, `members_only`, `age_restricted`, `login_required`, `bot_check` (retryable, long backoff),
  `network` (retryable), `http_429` (retryable), `disk_space` (retryable, job waits),
  `no_mp4_combination` (unsupported), `validation_failed` (retryable once, then failed),
  `ffmpeg_error`, `internal`. DownTube never attempts to bypass DRM, paywalls, geo or login
  restrictions; those are reported, not worked around.
* Restart recovery: on startup every `running` job → `queued` (stage `pending`); temp dirs of
  jobs not in an active status are deleted; temp dirs of queued/paused jobs are kept (resume).
* Concurrency: at most `max_concurrent_downloads` worker threads; one ffmpeg per job (yt-dlp
  invokes it for the merge). No other ffmpeg processes are spawned except ffprobe.
* Download window & disk check: a worker only picks a job inside `download_window` and when
  `free - estimated_size > min_free_gb`.

## 5. Quality rules (absolute)

1. `cap = min(requested or 2160, 2160)`. Formats with `short_side > cap` are never selected (8K never).
2. Choose the **highest short-side ≤ cap** among original video streams (DRM formats excluded).
   Never upscale, never re-encode, never alter fps; ffmpeg only stream-copies (`-c copy`).
3. Within that resolution: prefer non-HDR unless `prefer_hdr`; then higher fps; then codec
   `avc1 > vp9 > av01 > hevc` if `compat_profile`, otherwise `vp9 > av01 > avc1 > hevc` by
   bitrate within the same resolution — *never* trade resolution for codec.
4. Audio: original-language track (`language_preference` / "original" note) first; then
   `audio_preference` (`aac`=mp4a, `opus`) highest abr; fall back to the other codec.
5. MP4-compatible video codecs: avc1, hev1/hvc1, vp09/vp9, av01. Audio: mp4a, opus, ac-3, ec-3, flac (mp4a/opus preferred).
   If the best resolution has no MP4-compatible video stream → try other streams at the
   **same** resolution; if none: `mkv_fallback` → MKV, else `Unsupported("no_mp4_combination")`.
6. Liked-video precedence: `effective cap = max(caps of all active, eligible sources)` where a
   like contributes `likes_max_height` (default best ≤2160) and a channel rule contributes its
   `max_height` or `default_max_height`. If a current MediaFile exists with
   `height < min(best_available_height, effective cap)`, an `upgrade` job is queued; the old file
   is replaced only after the new one validates (old kept as `*.superseded.<ext>` if
   `keep_superseded_copies`, default true).
7. Validation via ffprobe before a job may complete (see `validate`).

## 6. Storage layout

```
<archive_root>/                       (container path, default /archive)
  <Channel Folder>/                   Channel.folder_name (stable once assigned)
    2026-10-15 - Video Title [VIDEOID].mp4
    2026-10-15 - Video Title [VIDEOID].jpg       (optional thumbnail, Plex local asset)
    2026-10-15 - Video Title [VIDEOID].nfo       (title + YouTube description; Kodi movie format, default on)
  .import/                            user-provided files waiting for import (+ .plexignore, .uploading/)
/temp/jobs/<job_id>/                  partial downloads (separate from archive)
```
Finalization: copy/move from temp to `<final>.downtube-partial` in the target directory,
`fsync`, then `os.replace` to the final name (atomic on the same filesystem).

## 7. Sync algorithms

* **Likes (incremental, every `likes_poll_minutes`, default 15)**: page `myRating=like` until a
  full page contains no unseen IDs (max 3 pages per poll). New IDs → `VideoSource(like)` →
  enqueue (priority 10) if `likes_mode != off` and eligible.
* **Likes full reconciliation (every `likes_full_reconcile_hours`, default 24)**: walk all pages;
  new IDs are added (catches re-likes of old videos regardless of ordering); IDs no longer
  present → `active=False` (archive untouched). API limitation: only the newest 1000 likes.
* **Initial scan** on first connect: full walk; with `likes_mode=future_only` all found likes
  are stored with `archive_eligible=False` (baseline). `POST /api/likes/backfill` flips them.
* **Subscriptions** every `subscriptions_refresh_hours` (24) + manual; channels.list in batches of 50.
* **Channels** with active rules every `channel_poll_minutes` (60): uploads playlist (`UU…`) page by
  page; follow modes stop at the first page whose items are all known. Backfill walks all
  pages, persisting `backfill_page_token` after each page (resumable, bounded memory), then
  `videos.list` per page for duration/live/status, applies filters, enqueues (priority 50, backfill 80).
  Shorts: duration ≤ 180 s and (optional) `UUSH` membership; livestream: `liveStreamingDetails` present;
  upcoming/live-now streams are skipped until completed.
* **Quota**: every call records units into `quota_usage` (PT day). When `used ≥ budget` or the
  API returns 403 `quotaExceeded`: mark exhausted, syncs end with `quota_deferred` and resume
  after PT midnight. Likes polling has priority over channel syncs (channel syncs stop at 90 % budget).
* **Metadata retention**: API metadata older than 25 days is refreshed; unavailable videos keep
  their archive records (local files are the user's archive, not API data).

## 8. REST API (all under `/api`, JSON, session cookie + `X-CSRF-Token` on mutating calls)

| Method & path | Body → Response |
|---|---|
| GET `/health` (no auth) | → `Health` |
| GET `/auth/login?manage=` (no auth) | 302 to Google (state+PKCE+nonce); `manage=true` also asks for `youtube.force-ssl` |
| GET `/auth/callback` (no auth) | 302 to `/` (or `/login?error=...`) |
| POST `/auth/logout` | → 204 |
| GET `/auth/me` | → `Me` (401 if no session) |
| POST `/auth/acknowledge` | → `Me` (records rights acknowledgement; required before any archiving) |
| POST `/auth/disconnect` | `DisconnectRequest` → 204 |
| GET `/dashboard` | → `Dashboard` |
| GET `/subscriptions?q=&sort=title|archived|last_sync|video_count&order=asc|desc&mode=&status=&page=&page_size=` | → `Page[SubscriptionOut]` |
| POST `/subscriptions/sync` | → `SyncRunOut` (202) |
| GET `/subscriptions/{channel_id}/estimate` | → `ChannelEstimate` |
| PATCH `/subscriptions/{channel_id}/rule` | `ChannelRulePatch` → `SubscriptionOut` (409 `confirmation_required` + `ChannelEstimate` in message details when large) |
| POST `/subscriptions/bulk` | `BulkRuleRequest` → `BulkResult` |
| POST `/subscriptions/{channel_id}/sync` | → `SyncRunOut` (202) |
| POST `/subscriptions/{channel_id}/unsubscribe` | → `SubscriptionOut`. Unsubscribes on YouTube (1 + 50 quota units), sets the rule to off, cancels queued jobs; archives kept. 403 `manage_permission_required`, 409 `not_subscribed` |
| GET `/likes?q=&state=&page=&page_size=` | → `Page[VideoOut]` |
| POST `/likes/sync` | `LikesSyncRequest` → `SyncRunOut` (202) |
| POST `/likes/backfill` | `LikesBackfillRequest` → `{"queued": int}` |
| GET `/downloads?status=&page=&page_size=` | → `Page[JobOut]` |
| GET `/downloads/counts` | → `JobCounts` |
| POST `/downloads` | `ManualEnqueueRequest` → `JobOut` |
| POST `/downloads/{id}/retry|cancel|pause|resume` | → `JobOut` |
| PUT `/downloads/{id}/import?filename=` | raw file body → `JobOut` (completed). Validates (ffprobe container, duration ±2 %, embedded video ID, ≤ job cap) and archives the file; 404, 409 `already_archived` / `job_running`, 422 `invalid_file` / `wrong_video` / `duration_mismatch` / `too_large` / `unsupported_container` / `empty_upload`, 507 `disk_space` |
| GET `/imports` | → `ImportFolderOut` (files waiting in `<archive_root>/.import`, last problem per file) |
| POST `/imports/scan` | → `ImportFolderOut` (imports every stable file named `…[VIDEOID].ext`; also runs every 5 min) |
| GET `/library?q=&channel_id=&sort=date|title|size|height&order=&page=&page_size=` | → `Page[MediaFileOut]` |
| GET `/library/{video_id}/stream` | video bytes (HTTP Range), only the DB-recorded file |
| GET `/library/{video_id}/thumbnail` | jpeg if local thumbnail exists |
| POST `/library/{video_id}/delete` | `DeleteVideoRequest` → `DeleteVideoResult`. Deletes the file, sidecars and kept superseded copies; adds a `video_exclusions` row (no sync re-queues it; `POST /downloads` lifts it); optionally `videos.rate=none` (50 units) first. 403 `manage_permission_required`, 409 `job_running` / `shared_video` |
| POST `/library/sidecars?overwrite=` | → `SidecarResult`: writes missing `.nfo` files for the user's archive |
| GET `/settings` / PUT `/settings` | → `SettingsOut` / `SettingsUpdate` → `SettingsOut` |
| GET `/storage` | → `StorageInfo` |
| GET `/storage/browse?path=` | → `BrowseResult` |
| POST `/storage/root` | `SetRootRequest` → `StorageInfo` (does NOT move existing archives) |
| GET `/sync/runs?kind=&page=` | → `Page[SyncRunOut]` |
| GET `/quota` | → `QuotaInfo` |
| GET `/logs?lines=200` (admin) | → `LogLines` (secrets redacted) |
| GET `/setup/status` (no auth) | → `SetupStatus` |
| POST `/setup` (no auth, one-time code from the container log; 403 `invalid_setup_code`, 409 `already_configured`, 429 `rate_limited`) | `SetupRequest` → `SetupStatus` |
| GET `/admin/google` (admin) | → `GoogleSettingsOut` |
| PUT `/admin/google` (admin) | `GoogleSettingsUpdate` → `GoogleSettingsOut` (409 `cannot_remove_self`) |

Errors: `{"error": {"code", "message"}}`; 401 unauthenticated, 403 forbidden/CSRF, 404, 409
conflict/confirmation, 422 validation, 429 rate limit, 503 quota/unavailable, 507 disk space.
OpenAPI: `/api/docs` (Swagger) and `/api/openapi.json`.

Every query is scoped to the session user (`user_id`), except global media (a MediaFile is
visible to a user only if that user has a VideoSource for the video).

## 9. Configuration (environment)

| Variable | Default | Notes |
|---|---|---|
| `DOWNTUBE_SECRET_KEY` | — (required) | ≥32 chars; derives Fernet + session keys |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | OAuth web client |
| `DOWNTUBE_BASE_URL` | `http://localhost:8092` | must equal the registered redirect origin |
| `DOWNTUBE_ALLOWED_EMAILS` | — (required) | comma-separated allowlist; first login becomes admin |
| `DOWNTUBE_DATA_DIR` | `/data` | SQLite, state |
| `DOWNTUBE_TEMP_DIR` | `/temp` | partial downloads |
| `DOWNTUBE_LOG_DIR` | `/logs` | rotating logs |
| `DOWNTUBE_STORAGE_MOUNTS` | `/archive` | comma-separated container paths the app may write |
| `DOWNTUBE_ARCHIVE_ROOT` | `/archive` | initial root (changeable in UI within mounts) |
| `DOWNTUBE_HOST_ARCHIVE_PATH` | — | display-only host path hint (e.g. /volume1/video/DownTube) |
| `DOWNTUBE_COOKIE_SECURE` | auto (true if base URL https) | |
| `DOWNTUBE_LOG_LEVEL` | `INFO` | |
| `DOWNTUBE_YTDLP_BIN` / `DOWNTUBE_FFPROBE_BIN` | `yt-dlp` / `ffprobe` | |
| `TZ`, `PUID`/`PGID` | `UTC`, 1000/1000 | |

## 9a. Runtime configuration stored in the app (v0.2)

Google OAuth client ID/secret, allowed emails and admin emails may come from the environment
(env wins, shown read-only) or from the first-run setup page / Settings (stored in `settings`,
secret Fernet-encrypted). While Google sign-in or the allowlist is not configured, the app is in
*setup mode*: a fresh one-time setup code is logged at every start (`DownTube setup code: …`).
`DOWNTUBE_TRUSTED_PROXIES` (CIDRs, default `127.0.0.1/32`) lists reverse proxies whose
`X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` headers are honoured.

## 10. OAuth

Authorization Code + PKCE (S256) + `state` (hash stored, single use, 10 min TTL) + OIDC `nonce`.
`access_type=offline`, `prompt=consent` on first connect, `include_granted_scopes=true`.
Scopes: `openid email profile https://www.googleapis.com/auth/youtube.readonly`.
Optional (incremental authorization, `GET /api/auth/login?manage=true`, `prompt=consent`):
`https://www.googleapis.com/auth/youtube.force-ssl`, used only by user-confirmed actions
(`POST /subscriptions/{id}/unsubscribe`, `POST /library/{video_id}/delete` with `remove_like`).
`Me.can_manage_youtube` reports whether it was granted; without it those calls return 403
`manage_permission_required` before changing anything.
ID token received directly from Google's token endpoint over TLS: validate `iss`, `aud`, `exp`,
`nonce`, `email_verified`; email must be in the allowlist. Refresh on expiry; `invalid_grant` →
account `reauth_required`, syncs paused, UI banner prompting reconnect.
Redirect URI: `${DOWNTUBE_BASE_URL}/api/auth/callback`. Google rejects raw private IPs —
use `http://localhost:8092` (SSH tunnel / on-host browser) or an HTTPS hostname under a real domain.
