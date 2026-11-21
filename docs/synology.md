# Running DownTube on Synology (DSM 7)

This guide describes a generic setup on Synology DSM 7 with Container Manager. Names such as
`nas.example.com` and `/volume1/...` are examples. Replace them with your own.

DownTube is not affiliated with or endorsed by Google or YouTube.

> **Do not expose DownTube to the internet.** Use your LAN, a VPN such as Tailscale, or an
> authenticated HTTPS reverse proxy. See [Reverse proxy and HTTPS](#reverse-proxy-and-https).

## Prerequisites

- DSM 7.2 or later with **Container Manager** installed from the Package Center.
- A shared folder for the archive, for example `/volume1/video/DownTube`.
- A Google Cloud OAuth client. Follow [installation.md](installation.md#4-create-the-oauth-client)
  first; you need the client ID and secret.
- Admin access to DSM.

## 1. Create a dedicated DSM user

Create a user (for example `downtube`) in **Control Panel > User & Group**.

- Give it **read and write** access to the archive shared folder.
- Give it **read and write** access to `/volume1/docker/downtube`.
- Do not add it to the `administrators` group.

Find its IDs over SSH:

```bash
id downtube
# uid=1026(downtube) gid=100(users) ...
```

Use the `uid` as `PUID` and the `gid` as `PGID`. Container Manager runs DownTube as this user,
so the archive files are owned by it.

## 2. Create the folder layout

Over SSH (or in File Station), create:

```
/volume1/docker/downtube/
├── data/       database and state
├── logs/       rotating logs
├── temp/       partial downloads
├── config/     .env and docker-compose.yml
/volume1/video/DownTube/     archive (finished media)
```

```bash
sudo mkdir -p /volume1/docker/downtube/{data,logs,temp,config}
sudo chown -R 1026:100 /volume1/docker/downtube/{data,logs,temp}
```

Keep the archive folder owned by the DownTube user. Changing the owner of an existing folder
with many files can take a long time; do it once, when the folder is empty.

## 3. Write the configuration

Create `/volume1/docker/downtube/config/.env`, using your own values:

```ini
DOWNTUBE_SECRET_KEY=replace-with-a-64-character-random-hex-string
GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-replace-me
DOWNTUBE_BASE_URL=http://localhost:8092
DOWNTUBE_ALLOWED_EMAILS=you@example.com
DOWNTUBE_HOST_ARCHIVE_PATH=/volume1/video/DownTube
TZ=UTC
PUID=1026
PGID=100
```

Restrict the file so only admins can read it:

```bash
sudo chmod 600 /volume1/docker/downtube/config/.env
```

`DOWNTUBE_BASE_URL` must match the redirect URI registered in Google. For SSH-tunnel access
keep `http://localhost:8092`. For a reverse proxy, use your HTTPS URL.

## 4. Create the project in Container Manager

1. Open **Container Manager > Project > Create** (menu names vary by DSM version).
2. **Project name**: `downtube`.
3. **Path**: `/volume1/docker/downtube/config`.
4. **Source**: *Create docker-compose.yml*, then paste the repository's `docker-compose.yml`
   and change the host paths to match this layout:

   | Container path | Host path |
   |---|---|
   | `/data` | `/volume1/docker/downtube/data` |
   | `/logs` | `/volume1/docker/downtube/logs` |
   | `/temp` | `/volume1/docker/downtube/temp` |
   | `/archive` | `/volume1/video/DownTube` |

5. Select **Next**, leave the web portal settings empty for now, then select **Done**.
6. Container Manager pulls `ghcr.io/jasoneameslamarca/downtube` and starts the project.

The command-line equivalent, from the `config` folder, is `docker compose up -d`.

## 5. Check the port

DownTube listens on **8092**. Before you start, make sure nothing else uses that port:

```bash
sudo ss -ltnp | grep ':8092'
```

If the port is busy, change the host side of the port mapping in the compose file (for example
`18092:8092`) and update the URL you use. Do not change the container side.

## 6. Resource limits

DownTube downloads and remuxes video, which is CPU and disk heavy, and a NAS has limited
resources. Recommended starting settings:

- Keep **max concurrent downloads** at 1 or 2 (the default is 2).
- Set a **download window**, for example `01:00-07:00`, so downloads run overnight.
- Set a **bandwidth limit** if the NAS also serves streams during the day.
- Keep the **minimum free space** setting (default 100 GB) so the archive volume never fills completely.
- In Container Manager, you can set a CPU and memory limit in the container's settings. Start
  with a memory limit of 2 GB.

## 7. First sign-in

Open `http://<nas-ip>:8092` from a machine on the same network. Google does not accept a raw IP
for the redirect, so sign-in only works through an SSH tunnel to `localhost`, or through an HTTPS
hostname. The usual approach:

```bash
ssh -L 8092:localhost:8092 admin@nas.example.com
```

Open `http://localhost:8092` in your local browser and follow
[installation.md](installation.md#8-first-sign-in). You only need the tunnel to sign in or reconnect.

## Reverse proxy and HTTPS

DSM includes a reverse proxy in **Control Panel > Login Portal > Advanced > Reverse Proxy**.

1. Create a rule:
   - **Source**: protocol HTTPS, hostname `downtube.example.com`, port 443.
   - **Destination**: protocol HTTP, hostname `localhost`, port `8092`.
2. Obtain a certificate for `downtube.example.com` in **Control Panel > Security > Certificate**
   (Let's Encrypt with DNS validation keeps the NAS off the internet).
3. Set `DOWNTUBE_BASE_URL=https://downtube.example.com` and redeploy the project.
4. Register `https://downtube.example.com/api/auth/callback` in the Google OAuth client.

Restrict access to the hostname with a DSM firewall rule, or to your LAN and VPN, so that it is
not reachable from the internet.

## Remote access with Tailscale

Install the Tailscale package on the NAS and sign in to your tailnet. Then:

- Browse to DownTube at the tailnet address or with an SSH tunnel through the tailnet.
- Or use `tailscale serve`/`tailscale cert` to get an HTTPS name. Test that Google accepts the
  redirect URI before relying on it.

Do not enable Tailscale Funnel or port forwarding for DownTube.

## Plex library

Point Plex at the archive folder so it can index the files:

1. In Plex, **Add Library**, and choose the **Other Videos** type, which suits self-archived files.
2. Add the folder `/volume1/video/DownTube`. Plex sees the folder as it is on the NAS.
3. Under **Advanced**, enable **Scan my library automatically** and **Run a partial scan when
   changes are detected**.
4. Let Plex index. Files are named `YYYY-MM-DD - Title [VIDEOID].mp4`, and channel folders hold them.

Some clients cannot play 4K VP9 or AV1 files or Opus audio in MP4 directly. Plex may transcode
them, which is slow on a NAS CPU. See [operations.md](operations.md#checking-format-compatibility).

## Updating and stopping

- To update, pull the new image and recreate the project (see [operations.md](operations.md#updating)).
- To stop, select the project and choose **Stop**. Partial downloads are kept and resume on
  the next start.

## Security checklist

- [ ] DownTube is reachable only from the LAN, a VPN, or an authenticated HTTPS proxy.
- [ ] Router port forwarding does not point to port 8092 or 443 for DownTube.
- [ ] `.env` is readable only by administrators (`chmod 600`).
- [ ] `DOWNTUBE_ALLOWED_EMAILS` lists only the accounts you trust.
- [ ] The DSM user has no administrator rights.
