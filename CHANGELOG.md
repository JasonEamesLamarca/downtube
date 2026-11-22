# Changelog

All notable changes to DownTube are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-11-22

### Added

- **Delete from the library.** Library → Delete removes the video file, its thumbnail, its `.nfo` and
  any kept superseded copy. The video is then excluded: no like or channel sync downloads it again for
  that user (a manual download from the Downloads page lifts the exclusion). For a liked video the like
  can also be removed on YouTube. Refused while a download of the video is running, or when another
  DownTube user also archives it.
- **Unsubscribe.** Subscriptions → Unsubscribe unsubscribes on YouTube, turns the channel's archive rule
  off and cancels its queued downloads. Archived videos stay.
- **Optional permission to manage the YouTube account** (`youtube.force-ssl`), requested only from
  Settings → Account & connection or from the dialogs above (incremental authorization). Without it
  DownTube stays read-only; the actions return `manage_permission_required` before changing anything.
- **Descriptions for media servers.** `.nfo` files (title, full YouTube description, date, year, runtime,
  channel, YouTube id) are now written by default, and new MP4 files carry title, description, date and
  channel tags (`--embed-metadata`, stream copy, no chapter track). Settings → *Write .nfo files for
  videos already archived* backfills existing videos.
- Write calls are counted with their real quota cost (50 units for `videos.rate` and
  `subscriptions.delete`).

### Changed

- Settings defaults: `write_nfo` is now on; new setting `embed_metadata` (on).
- Token refreshes record the scopes Google reports, so a permission withdrawn in the Google account is
  noticed.
- Database migration `0002`: table `video_exclusions`.

### Documentation

- End-to-end guide `docs/DownTube-Guide.docx`: architecture, interface tour, a step-by-step Synology
  deployment (Google Cloud, DNS, port forwarding, certificate, reverse proxy, split DNS, first run) and
  operations. Diagrams and screenshots (demo data) in `docs/images/`, generators in `tools/`.
- Troubleshooting sections from production use (DSM CPU quotas, share ACLs, SSH forwarding, double NAT,
  NAT loopback and DHCP DNS, Google Testing vs production, authorised domains, one-time client secret).

## [0.2.1] - 2026-11-21

### Fixed

- Log redaction no longer breaks uvicorn access-log lines. A request whose URL contained a secret
  (for example the OAuth `code` on `/api/auth/callback`) printed a "Logging error" traceback instead
  of the access line. Secrets were always redacted; only the log line was lost.

## [0.2.0] - 2026-11-21

### Added

- First-run setup page (`/setup`) for the Google sign-in client, the allowed Google accounts and
  the administrators. Setup mode stays active until sign-in is configured.
- One-time setup code, printed in the container log at every start until setup is complete.
- Settings → Google sign-in & access (administrators) to change the sign-in client and the allowlist later.
- `DOWNTUBE_TRUSTED_PROXIES` and `DOWNTUBE_BIND` for reverse-proxy deployments.
- Synology guide section on public HTTPS through the DSM reverse proxy.
- `deploy/deploy_nas.sh`, an SSH deployment script for Synology DSM and Linux Docker hosts.

### Changed

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `DOWNTUBE_ALLOWED_EMAILS` are optional. When set,
  they take precedence over the setup page and are read-only in the UI.
- Client address and scheme headers are honoured only from trusted proxies. Plain-HTTP requests
  forwarded by a trusted proxy are redirected to the HTTPS base URL.
- The OpenAPI documentation (`/api/docs`, `/api/openapi.json`) requires sign-in.

### Security

- Setup code stored only as a SHA-256 hash, single use, invalidated after 20 wrong attempts until
  restart, with 10 setup requests per minute per IP address.
- The Google client secret entered in the app is encrypted at rest (Fernet).
- `Strict-Transport-Security` is sent when the base URL is HTTPS.
- API documentation is no longer public.

## [0.1.0] - 2026-11-21

### Added

- Google sign-in (OpenID Connect with PKCE, state and nonce) with an email allowlist. The
  first allowlisted user becomes admin.
- Encrypted storage of Google OAuth tokens (Fernet, key derived from `DOWNTUBE_SECRET_KEY`)
  and server-side sessions with a CSRF header on mutating requests.
- Liked-video archiving with `off`, `future_only` and `all` modes, incremental polling and
  periodic full reconciliation. Manual backfill of baseline likes.
- Subscription archiving with per-channel rules: `off`, `backfill_once`, `backfill_and_follow`
  and `follow_new`, plus filters for shorts, livestreams, publish date and maximum duration.
  Bulk rule updates and a size estimate before large backfills.
- Quality policy: best original stream up to a per-rule maximum, hard cap at 4K (measured
  on the short side), never upscaled, never re-encoded (stream copy only).
- MP4 output by default, optional MKV fallback, AAC audio by default with an Opus option,
  optional compatibility profile that prefers H.264/AAC at the same resolution.
- ffprobe validation before any file is placed in the archive.
- Upgrades: a liked or higher-rule video is re-downloaded at higher quality, and the old copy
  is replaced only after the new one validates (optionally kept as a `.superseded` copy).
- Persistent download queue with priorities, retries with exponential backoff and jitter,
  pause, resume, cancel and retry; restart recovery of interrupted jobs.
- Download window, bandwidth limit, concurrency limit and minimum free disk space.
- YouTube Data API quota accounting with a configurable daily budget and deferral when exhausted.
- Library browser with HTTP range streaming of archived files and local thumbnails.
- Storage browser and archive root selection restricted to configured mounts.
- Settings page for archive, quality, download and quota options.
- Logs viewer for administrators, with secrets redacted, and rotating log files.
- Health endpoint (`/api/health`) and OpenAPI documentation (`/api/docs`).
- Docker image with a non-root user, published as `ghcr.io/jasoneameslamarca/downtube`.
- Documentation: README, installation, Synology, operations, development and quality-policy guides,
  plus privacy and security policies.
- MIT License.
