# Changelog

All notable changes to DownTube are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.2] - 2026-11-27

### Added
- **Try again now** on the Dashboard while downloads are paused by a YouTube bot check
  (`POST /api/downloads/bot-check/retry-now`). It ends the pause and makes the held videos due
  immediately. If YouTube still refuses, the queue pauses again for the same time as before: a
  manual retry never makes the wait longer.

## [0.6.1] - 2026-11-27

### Fixed
- **YouTube bot checks no longer keep the IP address flagged.** A "confirm you're not a bot"
  answer used to delay only that one video, so the worker moved on to the next one and asked
  YouTube again every few seconds (thousands of times a day), which kept the block alive. Now one
  bot check pauses **all** downloads for 30 minutes, doubling on each repeat up to 12 hours; any
  successful request resets it. The pause survives restarts and is shown on the Dashboard
  (`Dashboard.downloads_paused_until`, `bot_checks_in_a_row`).
- A bot check no longer uses up a job's attempts, so a long block never fails jobs permanently.
  Jobs that failed with `bot_check` before 0.6.1 can be retried from the Downloads page.

## [0.6.0] - 2026-11-27

### Changed
- **Most compatible codec first.** At the chosen resolution, DownTube now prefers
  H.264 > VP9 > AV1 > HEVC (was VP9 > AV1 > H.264). Videos at 1080p and below are archived as
  H.264/AAC MP4, which plays everywhere, including Safari on iPhone. Above 1080p YouTube offers no
  H.264, so those stay VP9 or AV1. Resolution, frame rate and the other quality rules are unchanged.
  Files already archived are not re-downloaded.

### Removed
- The **Compatibility profile** setting (`compatibility_profile` in `/api/settings`); its behaviour
  is now the only codec ranking. A stored value is ignored. Sending the field to
  `PUT /api/settings` now returns 422, so reload any Settings page opened before the upgrade.

## [0.5.0] - 2026-11-24

### Added
- **Logo and browser icon:** a white "DT" on a red rounded square, shown in the sidebar, sign-in
  and setup pages, as the browser tab icon (`favicon.ico` + `favicon.svg`), the Apple touch icon and
  in a web app manifest (192/512 px and maskable icons). Regenerate with
  `python3 tools/brand/make_icons.py` (needs Chromium).
- **Delete a subscription** once you are unsubscribed (here or directly on YouTube):
  `POST /api/subscriptions/{channel_id}/delete`. Only the subscription entry is removed: archived
  videos stay in the library, the channel's archive settings are kept but switched off, and any
  queued downloads of the channel are cancelled. If you subscribe again on YouTube, the next
  subscriptions refresh brings the channel back and its archived videos are counted again.
  Refused with 409 `still_subscribed` while you are subscribed.
- **Subscription filter** on the Subscriptions page (all / subscribed / unsubscribed;
  `GET /api/subscriptions?subscribed=`).
- **Release dates in the library:** every item shows when it was released on YouTube and when it
  was archived (`MediaFileOut.published_at`). Missing release dates are fetched at startup and every
  day (YouTube API, 1 quota unit per 50 videos; for videos gone from YouTube, the date yt-dlp embedded
  in the file), or on demand with **Settings → Fetch missing release dates**
  (`POST /api/library/release-dates`).

### Changed
- Library sorting offers **Release date** (new default) and **Date downloaded** separately
  (`sort=published|downloaded`; the old `sort=date` still means release date). Videos with an unknown
  value are listed last in both directions.

### Fixed
- The library sort labelled "Date downloaded" actually sorted by release date, and the date shown
  on each item was the archive date.

## [0.4.4] - 2026-11-22

### Changed
- **Dependencies brought up to date** (supersedes the open Dependabot pull requests):
  - Frontend: React 19, Tailwind CSS 4 (configuration moved into `src/index.css`; the
    Tailwind 3 look is kept: border colour, placeholder colour, pointer cursor on buttons), msw 3
    (tests now fail on unmocked requests again: `onUnhandledFrame: 'error'`). TypeScript stays on 6.0
    until typescript-eslint supports 7.x (Dependabot ignores TypeScript majors until then).
  - Backend minimums raised to the tested versions: FastAPI 0.165, SQLAlchemy 2.1.9, Alembic 1.20.1,
    Pydantic 2.14, cryptography 50.0.2, python-multipart 0.0.34, uvicorn 0.54, setuptools 84.2.1.
  - Image: Node 24.22.3 (Alpine 3.24) for the frontend build.
  - GitHub Actions: checkout 7, setup-node 7, setup-python 7, upload-artifact 7, Docker build-push 7,
    login 4, metadata 6, setup-buildx 4, setup-qemu 4.
- **GitHub releases:** pushing a `v*` tag now also creates the GitHub release, with this version's
  CHANGELOG section as notes.

### Fixed
- The file picker button in **Download myself** is styled like the other buttons.

## [0.4.3] - 2026-11-22

### Fixed
- **Library list view:** the thumbnail took the whole row, so titles and channels disappeared and the
  details wrapped one word per line. Rows now show thumbnail, title, channel, details and path, with
  the buttons on the right.
- **Vertical videos** (Shorts and other portrait videos) were labelled by their long side (for example
  "1920p" for a 1080×1920 file). The library, the likes "Archived" column and sorting by resolution
  now use the short side, like the resolution limits.

## [0.4.2] - 2026-11-22

### Fixed
- **Library channel filter** listed only the channels of the first 100 videos (by title), so larger
  archives were missing channels. It now lists every channel with archived videos, alphabetically,
  with the number of videos. New endpoint `GET /api/library/channels`.

## [0.4.1] - 2026-11-22

### Changed
- **Download myself:** the yt-dlp command can name the Linux keyring (`chromium+gnomekeyring`,
  `kwallet6`, …) for desktops yt-dlp does not recognise, such as COSMIC, and the JavaScript runtime
  (`--js-runtimes node`/`bun` instead of Deno). Browser, keyring and runtime are remembered in the
  browser. A "Setting up yt-dlp" section explains the requirements and the common cookie errors.

## [0.4.0] - 2026-11-22

### Added

- **Download myself / import.** Videos YouTube only serves to a signed-in account (age-restricted,
  members-only) can now be archived without giving DownTube any cookies: **Downloads → Download myself**
  shows a ready-made yt-dlp command for your own computer (reads your browser's sign-in locally, keeps
  the original streams up to the job's limit, MP4 by stream copy, metadata embedded) and lets you upload
  the resulting file. DownTube validates it (container, duration, embedded video ID, resolution limit)
  and archives it with the usual name, thumbnail and `.nfo`; the job is marked completed.
- **Import folder** `<archive root>/.import`: files named `…[VIDEOID].mp4|mkv|webm` are imported
  automatically every 5 minutes or with **Scan now**; problems are listed on the Downloads page.
  A `.plexignore` keeps Plex out of the folder.
- **YouTube links** on every download.
- API: `PUT /api/downloads/{id}/import`, `GET /api/imports`, `POST /api/imports/scan`.

## [0.3.1] - 2026-11-22

### Fixed
- Scheduled jobs without a startup delay (daily database backup, nightly metadata refresh, full likes
  reconciliation) were added paused and never ran. They now run on their schedule.

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
