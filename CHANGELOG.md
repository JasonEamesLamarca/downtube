# Changelog

All notable changes to DownTube are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
