# Privacy

DownTube is self-hosted software. There is no DownTube cloud service, no account with us,
and no telemetry. Your data stays on the server you run DownTube on.

DownTube is not affiliated with or endorsed by Google or YouTube.

## Where your data lives

| Data | Location | Who can access it |
|---|---|---|
| Database (accounts, subscriptions, liked-video IDs, metadata, jobs) | `/data/downtube.db` in the container | Whoever controls the server |
| Encrypted Google tokens | Same database, encrypted with Fernet | Needs `DOWNTUBE_SECRET_KEY` to decrypt |
| Archived media, thumbnails and NFO files | Your archive folder (`/archive` in the container) | Whoever controls the server and anyone with access to that folder |
| Logs | `/logs/downtube.log` | Whoever controls the server |
| Partial downloads | `/temp` (deleted after completion or cleanup) | Whoever controls the server |

DownTube does not send your data to the developers or to any third party, other than the
requests needed to talk to Google (sign-in, the YouTube Data API) and to YouTube (media
downloads initiated by you or by your rules).

## Google data DownTube stores

When you sign in with Google, DownTube stores:

| Item | Why | Stored as |
|---|---|---|
| Google account ID (`sub`), email address, display name, profile picture URL | To identify you and check the allowlist | Plain text in the database |
| OAuth access token and refresh token | To call the YouTube Data API on your behalf | Encrypted at rest |
| Your YouTube channel ID | To read your subscriptions and likes | Plain text |
| Your subscriptions (channel IDs, titles, descriptions, thumbnails) | Subscription archiving | Plain text (API data) |
| Liked-video IDs and first-seen times | Likes archiving | Plain text (API data) |
| Video and channel metadata (titles, descriptions, publish dates, durations, availability) | Naming files, filters and the library | Plain text (API data) |
| Your archive rules and settings | Archiving behaviour | Plain text |
| Audit events (for example, settings changes) | Accountability on shared installs | Plain text |

Your YouTube watch history is not accessed. The app does not use the search API.

### OAuth scopes requested

| Scope | Purpose |
|---|---|
| `openid` | Sign-in identity |
| `email` | Identify your account and check the allowlist |
| `profile` | Display name and picture in the UI |
| `https://www.googleapis.com/auth/youtube.readonly` | Read your subscriptions and liked videos. DownTube never writes to your YouTube account. |

## Retention of YouTube API data

The YouTube API Services Terms require that data obtained through the API is refreshed or
deleted within 30 days. DownTube follows this rule:

- Metadata older than 25 days is refreshed by a background job.
- Records for videos that are no longer available keep their archive record, because the
  archived file is your own copy. The API metadata is refreshed or removed as the policy requires.
- Subscription and like lists are refreshed on the schedule you configure (by default, likes
  every 15 minutes and subscriptions every 24 hours).

Archived media files are not API data and are not subject to this refresh rule.

## Disconnecting and deleting

You can remove DownTube's access and data at any time.

### Disconnect your Google account

Use **Disconnect** in the app, or call `POST /api/auth/disconnect`. The request body has two options:

| Option | Default | Effect |
|---|---|---|
| `revoke_google_token` | `true` | Revokes the OAuth token with Google, so DownTube can no longer call the API for your account. |
| `delete_local_data` | `false` | Deletes DownTube's database rows for your account (subscriptions, likes, rules, tokens, jobs). |

Disconnecting never deletes archived media files. Your archive stays in your archive folder.

### Revoke access from Google

You can also revoke DownTube's access at any time from
<https://myaccount.google.com/permissions>. After this, DownTube's next API call fails, the
account is marked as needing reconnection, and syncs pause until you sign in again.

### Delete archived files

Archived media is stored as ordinary files in your archive folder. Delete them with your
file manager or media server. DownTube never deletes archive files on its own, except for
superseded copies created during a quality upgrade, which you can disable (see the
`keep_superseded_copies` setting in [docs/installation.md](docs/installation.md)).

## Backups

Backups contain the same personal data as the live database, including encrypted tokens.
Store them with the same care as the live data. A backup can only be decrypted with the
`DOWNTUBE_SECRET_KEY` that was in use when it was created.

## No telemetry

DownTube does not collect analytics, crash reports or usage statistics. The only outbound
connections are to Google (sign-in and the YouTube Data API), to YouTube (media downloads),
and to any update or image source you choose to use.

## Changes to this document

Changes to what DownTube stores are described in [CHANGELOG.md](CHANGELOG.md) and in this file.
