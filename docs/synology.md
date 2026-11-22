# Running DownTube on Synology (DSM 7)

This guide describes a generic setup on Synology DSM 7 with Container Manager. Names such as
`nas.example.com` and `/volume1/...` are examples. Replace them with your own.

DownTube is not affiliated with or endorsed by Google or YouTube.

> **Keep DownTube off the open internet unless you mean to.** Use your LAN or a VPN such as
> Tailscale by default. Public HTTPS through the DSM reverse proxy is described in
> [Public HTTPS with DSM reverse proxy](#public-https-with-dsm-reverse-proxy).

## Prerequisites

- DSM 7.2 or later with **Container Manager** installed from the Package Center.
- A shared folder for the archive, for example `/volume1/video/DownTube`.
- A Google Cloud OAuth client. Follow [installation.md](installation.md#4-create-the-oauth-client)
  first; you need the client ID and secret, entered later in the setup page.
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
# Google client ID/secret and allowed accounts: enter them in the setup page (installation.md step 8).
DOWNTUBE_BASE_URL=http://localhost:8092
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

### Shared-folder ACLs

DSM shared folders such as `video` grant access through ACLs, often via group membership
(for example `administrators`, gid 101). Inside the container the process only has the groups you
give it. If the health check reports `archive_writable: false` although the user can write the
folder on the host, either grant that user explicit Read/Write on the folder in DSM, or add the
relevant group to the service:

```yaml
    group_add:
      - "101"
```

Also note that DSM kernels refuse CPU quotas (`cpus:`); DownTube only sets a memory limit.

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
- Limit memory, not CPU: the compose file sets `mem_limit: 2g`. DSM kernels refuse CPU quotas, so do not
  add `cpus:` or a CPU limit in Container Manager.

## 7. First-run setup and sign-in

Google does not accept a raw IP for the redirect, so you need an HTTPS hostname. Set it up with
[Public HTTPS with DSM reverse proxy](#public-https-with-dsm-reverse-proxy) first, then open
`https://downtube.example.com/setup` and follow
[installation.md](installation.md#8-first-run-setup-and-first-sign-in). Get the setup code with
`docker logs downtube | grep "setup code"`.

An SSH tunnel to `localhost:8092` does **not** work on DSM: its SSH server forbids TCP forwarding.
Use the reverse proxy instead.

## Public HTTPS with DSM reverse proxy

Use this to reach DownTube at `https://downtube.example.com` from outside your network. The sign-in
page then becomes public, so only do it deliberately.

1. **DNS.** At your DNS provider, create a `CNAME` record `downtube.example.com` pointing to your
   DDNS name (for example `yourname.example-ddns.net`).
2. **Router.** Forward TCP ports **80** and **443** to the NAS. Do not forward 8092. With double NAT
   (an ISP router in front of your own router), forward 80 and 443 on **every** router in the chain.
3. **Certificate.** In **Control Panel > Security > Certificate**, add a certificate from Let's Encrypt
   for `downtube.example.com`. Port 80 must stay forwarded so DSM can answer the ACME challenge.
   Assign the certificate to the reverse proxy service if DSM does not do it automatically.
4. **Reverse proxy.** In **Control Panel > Login Portal > Advanced > Reverse Proxy**, create two rules
   with the same destination, `HTTP` `localhost` port `8092`:
   - Source `HTTPS`, hostname `downtube.example.com`, port `443`, with **HSTS** enabled.
   - Source `HTTP`, hostname `downtube.example.com`, port `80`. DownTube answers forwarded plain-HTTP
     requests with a `308` redirect to HTTPS. DSM still answers the ACME challenges on port 80.
5. **DownTube configuration.** In the project `.env` (see below), set:

   ```ini
   DOWNTUBE_BIND=127.0.0.1
   DOWNTUBE_BASE_URL=https://downtube.example.com
   DOWNTUBE_TRUSTED_PROXIES=172.16.0.0/12
   ```

   `DOWNTUBE_BIND=127.0.0.1` stops direct access to port 8092 from other machines. DSM's proxy reaches
   the container through the Docker bridge gateway, so DownTube sees that address as the client.
   `DOWNTUBE_TRUSTED_PROXIES` makes it honour the `X-Real-IP` and `X-Forwarded-Proto` headers that DSM
   sends. Recreate the project after changing `.env`.
6. **Google.** Register `https://downtube.example.com/api/auth/callback` in the OAuth client.
7. **Hairpin NAT.** If devices on your LAN cannot reach `downtube.example.com` when they are on the same
   network, your router does not support NAT loopback. Add a local DNS record (for example a Pi-hole
   local DNS record) that points `downtube.example.com` to the NAS LAN address. DownTube needs no
   change for this. See [Troubleshooting](#troubleshooting) for the DHCP pitfall.

Consider also limiting who can reach the hostname with DSM's firewall.

## Deploying with deploy/deploy_nas.sh

From a computer with key-based SSH access to the NAS, run the script from the repository root:

```bash
NAS=admin@nas.example.com deploy/deploy_nas.sh
```

Environment variables (also listed in the script header):

| Variable | Default | Meaning |
|---|---|---|
| `NAS` | required | SSH target, `user@host` |
| `NAS_DIR` | `/volume1/docker/downtube` | App folder on the host |
| `ARCHIVE_DIR` | none | Host archive folder; created if missing and checked for write access |
| `COMPOSE_FILE` | `./docker-compose.yml` | Compose file to install |
| `ENV_TEMPLATE` | `./.env.example` | Template merged into the host `.env` |
| `IMAGE` | `ghcr.io/jasoneameslamarca/downtube:latest` | Image to run |
| `SHIP` | `0` | `SHIP=1` builds the image locally and streams it over SSH instead of `docker pull` |
| `PORT` | `8092` | Port used for the health check |

What the script does:

- **Copies the compose file.** DSM has no `scp`, so files are streamed over SSH.
- **Merges `.env`.** Existing values on the NAS are kept, keys from the template are added, and an
  empty `DOWNTUBE_SECRET_KEY` is generated. Keys that exist only on the NAS are kept under
  `# ---- Local additions`. Secrets are never printed, and the file is mode `600`. Values you set on the
  NAS (for example in section [Public HTTPS](#public-https-with-dsm-reverse-proxy)) survive later deploys.
- **Gets the image.** It runs `docker pull` on the NAS, or with `SHIP=1` streams a locally built image over
  SSH. It never builds on the NAS: DNS often fails inside the default bridge network during a build.
- **Starts and checks.** It runs `docker compose up -d --no-build` and waits for `/api/health`.
- **Checks DNS and HTTPS from inside the container.** If it fails, add
  `dns: [1.1.1.1, 8.8.8.8]` under the `downtube` service in the repository's `docker-compose.yml`
  (so the next deploy keeps it) and run the script again.
- **Prints the setup code** if setup is still needed, together with the `/setup` address.

To upgrade later, set the image tag (for example `:0.4.2`) in your compose file and run the script again.
Existing `.env` values and the database are kept.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` fails with `NanoCPUs can not be set` | The DSM kernel has no CPU quota support. | Remove `cpus:` from the compose file. Keep `mem_limit`. The repository's file already does this. |
| `/api/health` shows `archive_writable: false` | The shared folder grants write access through a group ACL (often `administrators`, gid 101) that the container user lacks. | Add `group_add: ["101"]` (or the gid that has access) to the service, or grant the user explicit access in DSM. See [Shared-folder ACLs](#shared-folder-acls). |
| `ssh -L 8092:localhost:8092` fails or does nothing | DSM's sshd forbids TCP forwarding. | Use the HTTPS reverse proxy. |
| Build or pull on the NAS fails with DNS errors | Name resolution is broken in the default bridge during builds. | Pull the published image (the deploy script does) or use `SHIP=1`. |
| `scp` is not found | DSM has no `scp` by default. | Stream the file over ssh (`ssh nas 'cat > file' < file`). The deploy script does this. |
| Site unreachable from the internet; Let's Encrypt fails | Double NAT, or ports 80/443 not forwarded on one router. | Forward 80 and 443 on every router in front of the NAS. |
| Reachable from outside but times out from the LAN, or only sometimes | No NAT loopback on the router, so the public name resolves to your public IP. | Add split DNS (a local record pointing the hostname at the NAS LAN IP). Then make sure every LAN client uses only that DNS server. If DHCP hands out several DNS servers (router, ISP, 1.1.1.1), clients switch between them at random and the name works intermittently. Set DHCP to hand out only your local resolver. |

More general problems (OAuth, Google Cloud, reachability checks) are in
[operations.md](operations.md#troubleshooting).

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

- [ ] DownTube is reachable only from the LAN, a VPN, or the HTTPS reverse proxy you chose.
- [ ] Routers forward only 80 and 443 to the NAS (if public), never 8092.
- [ ] `.env` is readable only by administrators (`chmod 600`).
- [ ] The allowed Google accounts (setup page or `DOWNTUBE_ALLOWED_EMAILS`) list only accounts you trust.
- [ ] The DSM user has no administrator rights.
