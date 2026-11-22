# Security Policy

## Supported versions

Only the latest tagged release and the `main` branch receive security fixes.

| Version | Supported |
|---|---|
| `main` | Yes |
| Latest release (0.3.x) | Yes |
| Older | No |

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report vulnerabilities privately through **GitHub Security Advisories**:

1. Go to <https://github.com/JasonEamesLamarca/downtube/security/advisories>.
2. Select **Report a vulnerability** and fill in the form.

Include, where possible:

- the affected version or commit;
- steps to reproduce, or a proof of concept;
- the impact you expect;
- whether you want to be credited.

You can expect an acknowledgement within 7 days. We aim to publish a fix or a mitigation
plan within 30 days, and we coordinate disclosure with you before any public announcement.

## Security design summary

DownTube is designed for a single household or small trusted group running it on a server
they control. The main controls are:

| Area | Control |
|---|---|
| Sign-in | Google OpenID Connect with Authorization Code flow, PKCE (S256), `state` (stored as a hash, single use, 10-minute TTL) and `nonce`. |
| Token validation | ID token received directly from Google's token endpoint over TLS. `iss`, `aud`, `exp`, `nonce` and `email_verified` are checked. |
| Access control | Only email addresses in `DOWNTUBE_ALLOWED_EMAILS` may sign in. The first allowlisted sign-in becomes admin. |
| Setup mode | Until sign-in is configured, a one-time setup code is printed in the container log at every start. Only its SHA-256 hash is stored, and the code is single use. 20 wrong attempts invalidate it until restart. The setup endpoints accept 10 requests per minute per IP address. |
| Token storage | Google access and refresh tokens, and the Google client secret entered in the setup page or Settings, are encrypted at rest with Fernet. The encryption key is derived from `DOWNTUBE_SECRET_KEY` with HKDF. |
| Sessions | Server-side sessions. The browser cookie holds an opaque random ID; only its SHA-256 hash is stored. Cookies are `Secure` when the base URL is HTTPS. |
| CSRF | Every mutating API call requires the `X-CSRF-Token` header. |
| Data scoping | Queries are scoped to the signed-in user. Media is visible only to users who have a source for it. |
| Paths | Writes are restricted to configured storage mounts. Paths are resolved with `realpath`; symlinks and traversal that escape a mount are rejected. Filenames are sanitized. |
| Subprocesses | yt-dlp and ffmpeg/ffprobe run with argument lists, never through a shell. |
| Access-control bypass | DownTube does not use cookies, geo-bypass or other options intended to defeat DRM, paywalls, geographic, age, membership or login restrictions. |
| Logs | The log viewer redacts secrets. Tokens and client secrets are never logged. |
| Container | Runs as a non-root user (`PUID`/`PGID`). It does not mount the Docker socket and needs no privileged mode. |
| Trusted proxies | `X-Real-IP`, `X-Forwarded-For` and `X-Forwarded-Proto` are honoured only from `DOWNTUBE_TRUSTED_PROXIES` (default `127.0.0.1/32`). A plain-HTTP request forwarded by a trusted proxy is redirected to the HTTPS base URL (308). |
| HSTS | With an HTTPS base URL, responses carry `Strict-Transport-Security: max-age=31536000`. |
| API documentation | `/api/docs` and `/api/openapi.json` are served only to signed-in users. |
| Network exposure | Intended for LAN or VPN access. Do not expose it to the internet unless you deliberately publish it through an HTTPS reverse proxy. |

### Operator responsibilities

- Set a long, random `DOWNTUBE_SECRET_KEY` (at least 32 characters) and keep it secret.
  Changing it invalidates sessions and makes stored tokens unreadable.
- Keep the Google client secret (in `.env` or entered in the setup page) and `.env` out of version control and backups that are shared.
- Keep the host and the container image updated. Pull new image versions regularly.
- Restrict who can reach the port. Prefer an SSH tunnel, a VPN such as Tailscale, or an
  authenticated HTTPS reverse proxy.
- Revoke DownTube's access in your Google account if you stop using it (see [PRIVACY.md](PRIVACY.md)).

## Known limitations

- Multiple users who want the same video share one download job; any of them can pause or cancel it.
- Serve DownTube over HTTPS (reverse proxy) for anything beyond a trusted LAN/Tailscale network;
  session cookies are only marked `Secure` when `DOWNTUBE_BASE_URL` starts with `https://`.

## Out of scope

- Vulnerabilities in YouTube, Google services, yt-dlp or ffmpeg. Report those to their maintainers.
- Issues that require the operator to expose the service to the internet without a reverse proxy.
- Denial of service through deliberate quota exhaustion of your own API project.
