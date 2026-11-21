#!/usr/bin/env bash
# Deploy DownTube to a NAS / Linux Docker host over SSH (tested on Synology DSM 7).
#
#   NAS=user@nas.example.com deploy/deploy_nas.sh
#
# Environment:
#   NAS           ssh target (required)
#   NAS_DIR       app folder on the host             (default /volume1/docker/downtube)
#   ARCHIVE_DIR   host folder for archived videos     (created if missing; optional)
#   COMPOSE_FILE  compose file to install             (default ./docker-compose.yml)
#   ENV_TEMPLATE  template merged into the host .env  (default ./.env.example)
#   IMAGE         image to run                        (default ghcr.io/jasoneameslamarca/downtube:latest)
#   SHIP=1        build/ship a local image over ssh (docker save | docker load) instead of `docker pull`
#   PORT          host port for the health check      (default 8092)
#
# The host .env is merged, never overwritten: existing values are kept, new keys from the
# template are added, DOWNTUBE_SECRET_KEY is generated if empty. Secrets are never printed.
set -euo pipefail

: "${NAS:?set NAS=user@host}"
NAS_DIR="${NAS_DIR:-/volume1/docker/downtube}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENV_TEMPLATE="${ENV_TEMPLATE:-.env.example}"
IMAGE="${IMAGE:-ghcr.io/jasoneameslamarca/downtube:latest}"
PORT="${PORT:-8092}"
REMOTE='export PATH=/usr/local/bin:/usr/bin:/bin:$PATH;'

cd "$(dirname "$0")/.."
ssh_nas() { ssh -o BatchMode=yes "$NAS" "$@" 2> >(grep -v -iE 'post-quantum|store now|openssh.com/pq|server may need' >&2); }
compose() { ssh_nas "$REMOTE cd '$NAS_DIR' && docker compose $*"; }

echo "==> Preparing $NAS:$NAS_DIR"
ssh_nas "mkdir -p '$NAS_DIR/data' '$NAS_DIR/logs' '$NAS_DIR/temp' && chmod 700 '$NAS_DIR/data'"
if [ -n "${ARCHIVE_DIR:-}" ]; then
  ssh_nas "mkdir -p '$ARCHIVE_DIR' && test -w '$ARCHIVE_DIR'" || { echo "archive dir $ARCHIVE_DIR is not writable" >&2; exit 1; }
fi
# No scp on Synology: stream files over ssh.
ssh_nas "cat > '$NAS_DIR/docker-compose.yml'" < "$COMPOSE_FILE"

echo "==> Merging .env (keeps existing values, adds new keys, generates missing secrets)"
current="$(ssh_nas "cat '$NAS_DIR/.env' 2>/dev/null || true")"
merged="$(
  CURRENT="$current" python3 - "$ENV_TEMPLATE" <<'PY'
import os, re, secrets, sys
current = {}
for line in os.environ["CURRENT"].splitlines():
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
    if m:
        current[m[1]] = m[2]
out, seen = [], set()
for line in open(sys.argv[1]).read().splitlines():
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
    if not m:
        out.append(line)
        continue
    key, default = m[1], m[2]
    seen.add(key)
    value = current.get(key, default)
    if key == "DOWNTUBE_SECRET_KEY" and (len(value) < 32 or value.startswith("replace-with")):
        value = secrets.token_urlsafe(48)
    out.append(f"{key}={value}")
extra = [f"{k}={v}" for k, v in current.items() if k not in seen]
if extra:
    out += ["", "# ---- Local additions"] + extra
print("\n".join(out))
PY
)"
printf '%s\n' "$merged" | ssh_nas "umask 077; cat > '$NAS_DIR/.env.new' && mv '$NAS_DIR/.env.new' '$NAS_DIR/.env' && chmod 600 '$NAS_DIR/.env'"
unset current merged

if [ "${SHIP:-0}" = 1 ]; then
  echo "==> Shipping local image $IMAGE over ssh"
  docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" .
  docker save "$IMAGE" | gzip -1 | ssh_nas "$REMOTE gunzip | docker load"
else
  echo "==> Pulling $IMAGE on the host"
  ssh_nas "$REMOTE docker pull -q '$IMAGE'"
fi

echo "==> Starting"
compose "up -d --no-build --remove-orphans"

echo "==> Waiting for health"
healthy=0
for _ in $(seq 1 40); do
  if ssh_nas "curl -fsS http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 3
done
if [ "$healthy" != 1 ]; then
  echo "Health check failed. Logs:" >&2
  compose "logs --tail 50" >&2 || true
  exit 1
fi
ssh_nas "curl -fsS http://127.0.0.1:$PORT/api/health"; echo

echo "==> Checking outbound DNS/HTTPS from inside the container"
if ! compose "exec -T downtube python -c \"import socket,urllib.request; socket.getaddrinfo('www.googleapis.com',443); urllib.request.urlopen('https://www.youtube.com/robots.txt', timeout=10)\""; then
  echo "!! The container cannot resolve or reach Google/YouTube." >&2
  echo "   Add to the downtube service in docker-compose.yml:  dns: [1.1.1.1, 8.8.8.8]  and run this again." >&2
  exit 2
fi
echo "    ok"

status="$(ssh_nas "curl -fsS http://127.0.0.1:$PORT/api/setup/status")"
if printf '%s' "$status" | grep -q '"needs_setup":true'; then
  code="$(compose "logs --no-log-prefix downtube" 2>/dev/null | grep -o 'DownTube setup code: [A-Z0-9-]*' | tail -1 | cut -d' ' -f4)"
  base="$(printf '%s' "$status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["base_url"])')"
  echo "==> Setup required: open $base/setup and enter the setup code: ${code:-<see: docker logs downtube | grep 'setup code'>}"
else
  echo "==> Deployed and configured."
fi
compose "ps"
