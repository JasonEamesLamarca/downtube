# Installation

This guide walks through the Google Cloud setup, the DownTube configuration, first sign-in,
and the archiving options. For Synology specifics see [synology.md](synology.md).

DownTube is not affiliated with or endorsed by Google or YouTube.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the Google account whose
   YouTube data you want to archive.
2. Create a project (for example, `downtube-personal`) or select an existing one.

## 2. Enable the YouTube Data API v3

1. Go to **APIs & Services > Library**.
2. Search for **YouTube Data API v3** and select **Enable**.

DownTube uses only this API for discovery. It does not use the search API.

## 3. Configure the OAuth consent screen

1. Go to **APIs & Services > OAuth consent screen**.
2. User type: **External**. Select **Create**.
3. Fill in the app name (for example, `DownTube`), a support email and a developer contact email.
4. **Scopes**: select **Add or remove scopes** and add:
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/userinfo.profile`
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/youtube.force-ssl` (optional: only requested when a user grants
     DownTube permission to unsubscribe and remove likes; declaring it avoids an extra warning)
5. **Test users**: add your own Google account.
6. **Branding** (new Google Auth Platform): set an app home page and a privacy policy link, for
   example this repository's `PRIVACY.md` on GitHub. The domain of every link must be listed under
   **Authorised domains**. For `github.com` links, add `github.com`; for your own hostname, add the
   parent domain (for example `example.com`).

### Testing or Production

| Publishing status | Behaviour | Use it? |
|---|---|---|
| **Testing** | Refresh tokens expire **7 days** after consent. You must reconnect every week. Up to 100 listed test users. | Only for a short trial |
| **In production** | Refresh tokens do not expire on that schedule. Google shows an "unverified app" warning during consent. Up to 100 users over the life of the project. | Yes, for personal use |

For a personal installation, select **Publish app** and confirm **In production**. You do not
need to submit the app for verification, but the warning screen will appear once during
consent. Click **Advanced**, then continue to DownTube. Google's wording may vary. The "unsafe" label refers only
to the unverified status; it does not mean DownTube is malicious.

DownTube still handles revoked or expired tokens. If a token fails, the app asks you to reconnect.

## 4. Create the OAuth client

1. Go to **APIs & Services > Credentials > Create credentials > OAuth client ID**.
2. Application type: **Web application**.
3. Name: `DownTube`.
4. **Authorized redirect URIs**: add exactly one URI, built from your base URL:

   ```
   <DOWNTUBE_BASE_URL>/api/auth/callback
   ```

5. Select **Create** and copy the **Client ID** and **Client secret**. The newer Google Auth
   Platform shows the secret only once, at creation. Copy it now, or create a new secret later.

### Redirect URI rules

Google validates the redirect URI strictly. It must match your `DOWNTUBE_BASE_URL` exactly.

| Rule | Allowed | Rejected |
|---|---|---|
| Scheme | `https://...`, or `http://localhost...` | `http://192.168.x.x:8092` (plain HTTP on a non-localhost host) |
| Host | `localhost`, or a hostname under a public domain (`downtube.example.com`) | Raw IP addresses (other than localhost), `.local`, `.lan`, `.home.arpa`, bare names such as `nas` |
| Extras | None | User info, fragments, wildcards |

Choose one of these two options:

**Option A: `http://localhost:8092` with an SSH tunnel (no domain needed).**
Use this for a personal installation. Forward the port from your browser machine to the server:

```bash
ssh -L 8092:localhost:8092 user@nas.example.com
```

This needs a host whose SSH server allows TCP forwarding. Synology DSM does not, so on a NAS use
Option B.

Then open <http://localhost:8092> in your local browser. The OAuth redirect goes to your
local machine, which the tunnel forwards to DownTube. You only need the tunnel when you sign in
or reconnect. After that, token refreshes happen on the server.

Set `DOWNTUBE_BASE_URL=http://localhost:8092` and register `http://localhost:8092/api/auth/callback`.

**Option B: HTTPS reverse proxy on a real domain.**
Point a hostname (for example, `downtube.example.com`) at your server, put a reverse proxy in
front of DownTube with a valid certificate (for example, from Let's Encrypt), and register
`https://downtube.example.com/api/auth/callback`. Set `DOWNTUBE_BASE_URL=https://downtube.example.com`.
Only do this if you can keep the service off the public internet, or protect it with
authentication at the proxy. See [synology.md](synology.md#public-https-with-dsm-reverse-proxy).

## 5. Configure DownTube

Create a `.env` file next to `docker-compose.yml`. Never commit this file. The Google client
credentials and the allowed accounts can instead be entered in the setup page (step 8), so you
can leave them out of `.env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DOWNTUBE_SECRET_KEY` | Yes | none | At least 32 random characters. Derives the encryption and session keys. Generate with `openssl rand -hex 32`. |
| `GOOGLE_CLIENT_ID` | No | none | OAuth client ID from step 4. If set, it wins over the setup page and is read-only in the UI. |
| `GOOGLE_CLIENT_SECRET` | No | none | OAuth client secret from step 4. Set it together with `GOOGLE_CLIENT_ID`. |
| `DOWNTUBE_BASE_URL` | Yes | `http://localhost:8092` | Public origin of the app. Must match the redirect URI origin exactly. |
| `DOWNTUBE_ALLOWED_EMAILS` | No | none | Comma-separated Google accounts allowed to sign in. If set, it wins over the setup page and is read-only in the UI. Removing an address revokes that user's sessions and stops their background syncs. |
| `DOWNTUBE_ADMIN_EMAILS` | No | none | Comma-separated administrators. If unset, the first account to sign in becomes admin. Admins change settings, Google sign-in and the storage root. |
| `DOWNTUBE_TRUSTED_PROXIES` | No | `127.0.0.1/32` | Reverse proxies (CIDRs) whose `X-Real-IP`, `X-Forwarded-For` and `X-Forwarded-Proto` headers are trusted. Behind Synology DSM, use `172.16.0.0/12` (see [synology.md](synology.md#public-https-with-dsm-reverse-proxy)). |
| `DOWNTUBE_BIND` | No | `0.0.0.0` | Host address the port is published on (compose). Use `127.0.0.1` when only a reverse proxy on the same host should reach DownTube. |
| `DOWNTUBE_PORT` | No | `8092` | Host port that maps to the container's 8092 (compose only). |
| `DOWNTUBE_DATA_DIR` | No | `/data` | Database location in the container. |
| `DOWNTUBE_TEMP_DIR` | No | `/temp` | Partial downloads. |
| `DOWNTUBE_LOG_DIR` | No | `/logs` | Log files. |
| `DOWNTUBE_STORAGE_MOUNTS` | No | `/archive` | Container paths DownTube may write to. |
| `DOWNTUBE_ARCHIVE_ROOT` | No | `/archive` | Initial archive folder. You can change it in the UI, within the mounts. |
| `DOWNTUBE_HOST_ARCHIVE_PATH` | No | none | Display-only hint of the host path, shown in the UI (for example `/volume1/video/DownTube`). |
| `DOWNTUBE_COOKIE_SECURE` | No | auto | `true` when the base URL is HTTPS. |
| `DOWNTUBE_LOG_LEVEL` | No | `INFO` | Log verbosity. |
| `TZ` | No | `UTC` | Time zone for the download window and logs. |
| `PUID` / `PGID` | No | `1000` / `1000` | User and group the container runs as. Files are owned by this user. |

## 6. Storage mounts

DownTube writes to a container path (default `/archive`). The host folder you mount there is where
the files appear on your server. Always use the container path (`/archive`) in `DOWNTUBE_ARCHIVE_ROOT`
and in the UI. `DOWNTUBE_HOST_ARCHIVE_PATH` is a label only and does not change where files go.

Mount these four volumes:

| Container path | Purpose | Typical host path |
|---|---|---|
| `/data` | Database and state | `./data` |
| `/logs` | Rotating logs | `./logs` |
| `/temp` | Partial downloads | `./temp` |
| `/archive` | Finished media | Your media folder, for example `/volume1/video/DownTube` |

### Ownership and permissions

The container runs as `PUID:PGID`. That user must be able to write to every mounted folder.
Create a dedicated user on the host, give it write access to the folders, and use its IDs:

```bash
id downtube            # prints uid and gid to use as PUID and PGID
sudo chown -R 1026:100 ./data ./logs ./temp /path/to/archive
```

If a folder is owned by another user, DownTube reports the archive as not writable and
stops downloads until you fix the permissions.

## 7. Start the container

```bash
docker compose up -d
docker compose logs -f downtube
```

The image is `ghcr.io/jasoneameslamarca/downtube` and listens on port **8092**. The
`docker-compose.yml` in the repository defines the volumes and environment. Database migrations
run automatically on start.

Check that the service is healthy:

```bash
curl -s http://localhost:8092/api/health
```

If the configuration is incomplete (for example, a missing secret), the health endpoint reports
`degraded`. Check the container logs for the cause.

## 8. First-run setup and first sign-in

If `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` or `DOWNTUBE_ALLOWED_EMAILS` is empty, DownTube starts
in *setup mode* and serves a setup page:

1. Start the container with those variables empty.
2. Open `<DOWNTUBE_BASE_URL>/setup` (for example <http://localhost:8092/setup>).
3. Get the one-time setup code from the container log:

   ```bash
   docker logs downtube | grep "setup code"
   ```

   A new code is issued at every start until setup is complete. After 20 wrong attempts the code
   stops working until the container restarts. The page accepts 10 attempts per minute per IP address.
4. Register the **redirect URI** shown on the page in your OAuth client (step 4). It must match exactly.
5. Paste the client ID and client secret, the allowed Google accounts and the administrators, then
   select **Complete setup**.
6. Select **Sign in with Google** and choose an account in the allowlist. Accept the consent screen.
   If you see the unverified-app warning, see step 3.
7. Read the rights acknowledgement and accept it. Archiving is blocked until you do.

Accounts not on the allowlist are refused, even if they can complete Google sign-in.

Environment variables still work. If you set them, they win over the setup page and are shown
read-only. After setup, administrators change the sign-in client and the allowed accounts in
**Settings → Google sign-in & access**.

## 9. Choose the download directory

In the web UI, open the storage settings and choose an archive folder under one of the mounts in
`DOWNTUBE_STORAGE_MOUNTS` (`POST /api/storage/root`). Changing the archive root does **not** move
existing files. Move them yourself if you need them in the new location.

DownTube writes each channel into its own folder, named after the channel, and names files
`YYYY-MM-DD - Title [VIDEOID].mp4`.

## 10. Enable liked-video archiving

Set the likes mode in the settings (`PUT /api/settings`):

| Mode | Behaviour |
|---|---|
| `off` | Likes are not archived. Existing files are untouched. |
| `future_only` (default) | Your current likes are recorded as a baseline and not downloaded. Only likes made after this point are archived. |
| `all` | All likes, including those you made earlier, are archived. The API returns only your newest 1000 likes. |

To archive the baseline later, run **Backfill likes** (the `POST /api/likes/backfill` endpoint).

Likes polling runs every 15 minutes by default, with a full reconciliation every 24 hours.
You can change both in Settings. Set the quality for liked videos with **Likes maximum resolution**
(best available, up to 4K, by default).

## 11. Subscription archiving

In the subscriptions list, open a channel and set its rule:

| Mode | Behaviour |
|---|---|
| `off` (default) | Nothing is archived from this channel. |
| `backfill_once` | Archives all existing uploads once, then stops. |
| `backfill_and_follow` | Archives all existing uploads, then every new upload. |
| `follow_new` | Archives only uploads published after the rule is activated. |

Each rule can also set a maximum resolution, a paused flag, and filters: include shorts, include livestreams,
published-after date and maximum duration. Excluding shorts skips videos of 180 seconds or less.

Activating a backfill on a large channel (more videos than the large-channel threshold, default
200) asks for confirmation and shows an estimate first. Use the bulk action to apply the same rule to many channels.

Subscriptions refresh every 24 hours by default. Channel uploads are checked every 60 minutes
for channels with an active rule.

## 12. Resolution and formats

Each rule has a **maximum resolution**. DownTube picks the highest original resolution at or below
that maximum. It never upscales, never exceeds 4K, and never re-encodes.

| Source the video offers | Maximum set | Result |
|---|---|---|
| 480p | 4K (or best available) | 480p. DownTube never upscales. |
| 720p | 1080p | 720p |
| 1080p | 720p | 720p |
| 2160p (4K) | 1080p | 1080p |
| 2160p (4K) | Best available | 2160p (4K) |
| 8K (4320p) | Best available | 2160p. Anything above 4K is never selected. |

The full rules, including codec and audio choices, are in [quality-policy.md](quality-policy.md).

### Format options

| Setting | Default | Effect |
|---|---|---|
| Container | MP4 | Stream copy into MP4. |
| MKV fallback | Off | If no MP4-compatible streams exist at the chosen resolution, store as MKV instead of skipping. |
| Audio preference | AAC | AAC is the default. Choose Opus for Opus audio when the source provides it. |
| Prefer HDR | Off | Prefers HDR streams at the same resolution. |
| Keep superseded copies | On | When an upgrade replaces a file, keep the old one as `*.superseded.mp4`. |

## Next steps

Read [operations.md](operations.md) for backups, updates and troubleshooting.
