# Operations

Day-to-day running of a DownTube installation: updates, backups, troubleshooting, quota,
download errors, format checks, permissions, interrupted downloads and logs.

Compose commands assume you run them in the folder that holds `docker-compose.yml` and `.env`.

## Updating

```bash
docker compose pull downtube
docker compose up -d
```

- Database migrations run automatically at startup. Do not edit the schema by hand.
- Before a major update, take a backup (see below).
- Check the [CHANGELOG](../CHANGELOG.md) for changes that need action, such as new variables.
- If the new version fails to start, check `docker compose logs downtube`. Roll back by
  setting the image tag back to the previous version in `docker-compose.yml` and running
  `docker compose up -d` again. A rollback after a migration may need the backup restored.

yt-dlp breaks whenever YouTube changes its site. Update the image regularly, even when DownTube
itself has no new release.

### Upgrading to 0.4.0

No action needed. The import folder `<archive root>/.import` is created on first use. Failed
age-restricted downloads now offer **Download myself** (see
[Importing files](#importing-files-you-downloaded)).

### Upgrading to 0.3.0

1. Optional, in Google Cloud: **Google Auth Platform → Data access → Add or remove scopes**, add
   `https://www.googleapis.com/auth/youtube.force-ssl` and save. Unsubscribing and removing likes need
   this permission. Without it the rest of DownTube works as before.
2. Upgrade as above. Migration `0002` adds the `video_exclusions` table.
3. In DownTube, **Settings → Account & connection → Grant permission with Google** if you want to
   unsubscribe and remove likes from the app. Google shows the "unverified app" warning again; that
   is expected for a personal client.
4. **Settings → Media server files → Write .nfo files for videos already archived** adds description
   files to everything downloaded before 0.3.0. Files downloaded from now on get an `.nfo` and embedded
   MP4 tags automatically. Existing MP4 files are not rewritten.
5. Plex: the `.nfo` files are read by the Plex NFO agent, which works only in **Movie** and **TV**
   libraries (Plex Media Server 1.43.1 or later). In an **Other Videos** library, Plex uses the tags
   embedded in the MP4 instead. Jellyfin, Emby and Kodi read the `.nfo` files directly.

## Backup and restore

DownTube makes a consistent online copy of its SQLite database every day (SQLite backup API)
into `/data/backups/`, keeping the newest seven. Back up `/data` (including `backups/`) and your
`.env` file with your normal NAS backup tool (e.g. Hyper Backup). For an extra manual copy:

### Manual backup

Stop the container so the database files are consistent, copy them, and start it again:

```bash
docker compose stop downtube
mkdir -p backups/$(date +%Y%m%d)
cp data/downtube.db* backups/$(date +%Y%m%d)/   # includes -wal and -shm files if present
docker compose start downtube
```

Also back up:

- `.env` (it contains `DOWNTUBE_SECRET_KEY`; without it, encrypted tokens cannot be read);
- the `/data` folder (database and backups);
- optionally the archive. Media files are large; back them up with your normal NAS backup.

Keep backups private. They contain personal data (see [PRIVACY.md](../PRIVACY.md)).

### Restore

1. Stop the container: `docker compose down`.
2. Restore `.env` with the **same** `DOWNTUBE_SECRET_KEY` that was in use when the backup was made.
3. Replace the database with the backup copy, and remove the WAL side files:

   ```bash
   rm -f data/downtube.db-wal data/downtube.db-shm
   cp data/backups/downtube-YYYYMMDD-HHMMSS.db data/downtube.db
   ```

4. Start the container: `docker compose up -d`.
5. Sign in. If tokens fail to decrypt, see *invalid_grant* below.

Restoring does not change archived media. Files on disk that the restored database does not
know about are not imported automatically.

## Diagnosing OAuth errors

| Symptom (Google or DownTube message) | Cause | Fix |
|---|---|---|
| `redirect_uri_mismatch` | The redirect URI registered in Google does not equal `DOWNTUBE_BASE_URL` + `/api/auth/callback`, character for character (scheme, host, port, no trailing slash). | Correct either the Google client or `DOWNTUBE_BASE_URL`, restart the container, and try again. |
| `access_denied` | Consent was declined, the account is not a test user (while the app is in *Testing*), or the account is not in `DOWNTUBE_ALLOWED_EMAILS`. | Accept the consent screen. Add the account as a test user, or publish the app. Check the allowlist. |
| `invalid_grant` | The refresh token was revoked, expired (for example, 7 days in *Testing*), or the password or access changed. | Reconnect the account in DownTube. The account shows `reauth_required`, syncs pause, and a banner asks you to reconnect. |
| Unverified-app warning | Expected for a non-verified app with a sensitive scope. | Continue past the warning. It does not block personal use. |
| Cloud Console rejects the redirect URI when you save the client | The base URL is a raw IP or plain HTTP on a non-localhost host. | Use `localhost` with an SSH tunnel, or an HTTPS hostname. See [installation.md](installation.md#redirect-uri-rules). |

If you cannot sign in, check the container logs first (`docker compose logs downtube`). The
log line for an OAuth failure should name the error code. Tokens are never logged.

## Quota exhaustion

YouTube gives each Google Cloud project a default of **10,000 units per day**. The quota resets
at midnight Pacific Time. DownTube's own default budget is **9,000 units** (setting
`quota_daily_budget`, range 100 to 1,000,000), which leaves headroom.

| Call | Cost | Used for |
|---|---|---|
| `videos.list` | 1 unit per call (up to 50 IDs) | Metadata for liked videos and channel uploads |
| `videos.list?myRating=like` | 1 unit per page of 50 | Liked-video discovery |
| `subscriptions.list` | 1 unit per page | Subscription sync |
| `channels.list` | 1 unit per call (up to 50 IDs) | Channel details and uploads playlist |
| `playlistItems.list` | 1 unit per page of 50 | Channel uploads |
| `search.list` | not used | DownTube never calls it |

Practical costs:

- A likes poll reads at most 3 pages per run (up to 3 units), plus metadata calls.
- A channel with *N* uploads costs about `ceil(N / 50)` units to walk, plus metadata calls.
- A full sync of a few hundred channels usually stays far below the daily budget.

When usage reaches the budget, or Google returns `quotaExceeded` (HTTP 403), DownTube:

- marks the sync run as `quota_deferred`;
- stops channel syncs at 90 % of the budget, so likes polling keeps priority;
- resumes automatically after midnight Pacific Time.

`GET /api/quota` shows the usage for the current day.

## Download error codes

Jobs record an `error_code` when they fail or pause. The table lists the codes from the design
contract, plus additional codes the downloader can emit.

| Code | Meaning | Retryable | What happens | What you can do |
|---|---|---|---|---|
| `unavailable` | Private, deleted, removed or terminated video | No | Job `skipped` | Nothing. Archive record is kept. |
| `geo_restricted` | Not available in the server's country | No | Job failed | Nothing. DownTube never bypasses geo restrictions. |
| `drm` | Content is DRM-protected | No | Job failed | Nothing. DRM is never bypassed. |
| `members_only` | Channel membership required | No | Job failed | If your account is a member: **Download myself** (see [Importing files](#importing-files-you-downloaded)). |
| `age_restricted` | Age confirmation or sign-in required | No | Job failed | **Download myself** with your own signed-in account and import the file. |
| `login_required` | YouTube asks for sign-in or cookies | No | Job failed | As above. DownTube itself never uses cookies. |
| `bot_check` | YouTube asks the server to confirm it is not a bot | Yes, never fails the job | **All** downloads pause: 30 min, doubling on each repeat up to 12 h; the Dashboard shows when they resume | Wait, or click **Try again now** on the Dashboard after changing network or VPN (if YouTube still refuses, the pause restarts at the same length, never longer). The block is on your public IP address (other devices on the same connection are affected too); it usually clears after hours to a few days with little traffic. Reduce concurrency. |
| `network` | Timeouts, resets, DNS or 5xx errors | Yes | Exponential backoff | Check connectivity. |
| `http_429` | Too many requests | Yes | Backoff | Lower concurrency or bandwidth. |
| `http_403` | Forbidden response from a media server | Yes | Backoff | Usually temporary. |
| `disk_space` | Free space below `min_free_gb` or the disk is full | Yes | Job waits | Free space or lower the minimum. |
| `not_yet_available` | Premiere or live event not finished | Yes | Backoff | Nothing. Completed streams are archived later. |
| `format_unavailable` | Requested formats not offered at the moment | Yes | Backoff | Usually temporary. |
| `no_mp4_combination` | No MP4-compatible streams at the best resolution | No | Job `unsupported` | Enable MKV fallback, or accept the skip. |
| `no_video_stream` / `no_stream_within_limit` | No usable video, or everything exceeds the limit | No | Job `unsupported` | Adjust the maximum resolution. |
| `validation_failed` | ffprobe check failed (duration, container, cap) | Once, then failed | Retry once | Check the file in logs. Retry the job. |
| `ffmpeg_error` | Merge or remux failed | Yes | Backoff | Check logs. Update the image if it persists. |
| `invalid_id` | Malformed video ID | No | Job failed | Investigate the source record. |
| `internal` | Unexpected error or missing binary | Yes | Backoff | Check logs, then report an issue if it repeats. |

Retries use exponential backoff: `min(2^attempts × 60 s, 6 h)`, with random jitter of ±20 %.
Jobs have a maximum number of attempts (default 5). Retry, pause, resume or cancel a job with `POST /api/downloads/{id}/retry|cancel|pause|resume`.

## Importing files you downloaded

DownTube downloads without signing in to YouTube and never handles YouTube cookies. Videos that
YouTube only shows to a signed-in account (age-restricted, members-only) therefore fail with
`age_restricted`, `login_required` or `members_only`. To archive them anyway:

1. **Downloads → Failed → Download myself** on the video. The dialog links to the video on YouTube
   and shows a yt-dlp command for your own computer. It reads your browser's sign-in locally
   (`--cookies-from-browser`), keeps the original streams up to the job's resolution limit, merges
   to MP4 without re-encoding, embeds the metadata and puts the video ID in the file name. Pick the
   browser you are signed in with and, on Linux, the keyring; the dialog remembers your choices.

   **Setting up your computer:** install [yt-dlp](https://github.com/yt-dlp/yt-dlp#installation)
   (keep it current with `yt-dlp -U`), ffmpeg, and a JavaScript runtime for YouTube: Deno, or choose
   *node* in the dialog if you have Node.js. Common errors:

   | yt-dlp says | Fix |
   |---|---|
   | `cannot decrypt v11 cookies: no key found` (Linux) | yt-dlp did not recognise the desktop (for example COSMIC). Choose the keyring: *gnomekeyring* (GNOME, COSMIC, most others) or *kwallet6*/*kwallet5* (KDE). On Debian, Ubuntu and Pop!_OS also install `python3-secretstorage`. |
   | `could not find chrome cookies database` | Wrong browser, for example Chromium rather than Chrome. |
   | `Sign in to confirm your age` | No usable cookies were read. Check the two rows above and that the video plays in that browser while signed in. |
   | Cookie database locked / cannot read cookies (Windows) | yt-dlp usually cannot read Chrome or Edge cookies on Windows; use Firefox, and close the browser first. |
2. Give DownTube the file, either way:
   - **Upload** it in the same dialog; or
   - copy it into the **import folder**, `<archive root>/.import` (on Synology, for example
     `/volume1/video/DownTube/.import`). The file name must contain the video ID in brackets, for
     example `Title [dQw4w9WgXcQ].mp4`. DownTube scans the folder every 5 minutes; **Downloads →
     Import folder → Scan now** scans immediately. Files must be unchanged for 30 seconds before they
     are picked up, so a copy in progress is never taken.

DownTube then checks the file: MP4 or MKV/WebM with a video stream (ffprobe), duration within 2 % (at
least 2 s) of the YouTube duration, no embedded metadata naming a different video, and no larger than
the resolution limit for that video. A file that passes is moved into the channel folder with the
usual name, thumbnail and `.nfo`, and the job is marked completed. A file that fails stays where it
was, with the reason shown in the Import folder list. Imports work for any video DownTube already
knows (one with a download job or in your likes/subscriptions), not only failed ones. A video that is
already archived is refused. Importing a video you deleted from the library lifts its exclusion.

The import folder holds a `.plexignore` so Plex does not index files waiting there. Uploads go
through the web server in one request; the DSM reverse proxy allows large uploads by default, other
reverse proxies may need their request size limit raised (nginx `client_max_body_size`).

## Checking format compatibility

To see what a finished file actually contains:

```bash
ffprobe -v error -show_entries format=format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate \
  -of default=noprint_wrappers=1 "/path/to/2026-01-01 - Title [VIDEOID].mp4"
```

`ffprobe` comes with ffmpeg. The container image includes it, so you can run it inside the container:

```bash
docker compose exec downtube ffprobe -v error -show_streams -of json "/archive/<Channel>/<file>.mp4"
```

Interpret the output using [quality-policy.md §9](quality-policy.md#9-plex-and-client-playback):

- `codec_name=h264` with `mp4a` audio plays almost everywhere;
- `vp9`, `av1` and `opus` may need transcoding on some clients.

## Directory permissions

The container runs as `PUID:PGID`. Each of these folders must be writable by that user:

| Folder | Used for |
|---|---|
| `/data` | Database, backups |
| `/logs` | Log files |
| `/temp` | Partial downloads |
| `/archive` (and any other mount) | Finished media |

Symptoms of a permission problem: the archive is reported as not writable, downloads stop with
`internal` or `disk_space` errors, and logs show `Permission denied`.

Fix:

```bash
docker compose exec downtube id            # shows the container user
ls -ld /path/to/archive                    # shows the host owner
sudo chown -R <PUID>:<PGID> /path/to/archive
```

Do not run the container as root to avoid this. Set `PUID` and `PGID` instead.

## Interrupted downloads

DownTube is designed to survive restarts and power cuts.

| Event | What happens |
|---|---|
| Container restarts or stops gracefully | Running jobs go back to `queued`. Partial files in `/temp/jobs/<id>/` are kept, and the download resumes. |
| Pause | The download process is stopped. Temp files are kept so the job continues from where it stopped. |
| Cancel | The process is stopped and the temp folder is deleted. |
| Crash or power loss | On startup, running jobs become `queued` and their interrupted attempt is not counted. Temp folders of jobs that are not active are removed. |
| Disk full during a download | The job waits (`disk_space`) and resumes when space is available. |

Finished files are moved into the archive atomically, through a `*.downtube-partial` name, so a
half-written file is never listed as a completed archive. Leftover `*.downtube-partial` files
in the archive can usually be deleted when no job is running.

## Troubleshooting

Synology-specific problems (CPU quota error, ACLs, SSH forwarding, DNS, double NAT) are in
[synology.md](synology.md#troubleshooting). Other lessons from running DownTube in production:

| Symptom | Cause | Fix |
|---|---|---|
| Sign-in breaks after a week | The Google consent screen is in *Testing*, where refresh tokens expire after 7 days. | In Google Cloud, set the publishing status to **In production**, then reconnect. The unverified-app warning is expected for personal use. |
| Google rejects the consent screen branding | The app home page or privacy policy link uses a domain that is not an authorised domain. | Add the domain of every link under **Authorised domains**. If the privacy policy is this repository's `PRIVACY.md` on github.com, add `github.com`. |
| Lost the OAuth client secret | The new Google Auth Platform shows the secret only once, at creation. | Create a new secret in the client, then enter it in **Settings → Google sign-in & access**. |
| The site works from some networks and not others | A router without NAT loopback, split DNS missing, or clients using a mix of DNS servers. | Test from a phone on mobile data first. If that works, fix DNS inside the LAN (see synology.md). |
| An online "is it up" or fetch service cannot reach the site, but browsers can | Some fetch services are blocked or refused by your host or network. | Do not rely on one service. Check from a phone on mobile data or a multi-location checker. |
| Downloads were running when the container restarted | Expected during an upgrade. | Nothing to do. Jobs return to `queued`, resume from the partial files in `/temp`, and the restart does not use up a retry attempt. |
| Health is `degraded`, or sign-in is not configured | Missing secret, or setup is not complete. | Read `docker compose logs downtube`. For a new install, open `/setup` and use the setup code from the log. |

## Logs

| Source | Location | Notes |
|---|---|---|
| Application log | `/logs/downtube.log` (host: your `logs` folder) | Rotates at 10 MB, keeps 5 files |
| Container output | `docker compose logs -f downtube` | Same messages, from the container runtime |
| UI log viewer | Admin users, or `GET /api/logs?lines=200` | Last lines, with secrets redacted |

Set `DOWNTUBE_LOG_LEVEL=DEBUG` temporarily when you need more detail, then set it back to `INFO`.
Debug logs can be verbose. Review them before sharing, even with redaction.

When you report a problem, include the version, the relevant error code and the log lines
around the event. Remove any personal email addresses and paths before posting publicly.
