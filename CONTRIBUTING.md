# Contributing to DownTube

Thanks for your interest in DownTube. This guide covers local development, the branch and
pull-request flow, and the rules that every change must respect.

Start with [docs/development.md](docs/development.md) for the architecture and
[docs/architecture/CONTRACTS.md](docs/architecture/CONTRACTS.md) for the internal contracts.
Changes that alter a contract (database schema, REST API, job states, quality rules) must
update that document in the same pull request.

## Development setup

### Backend (Python 3.12+)

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pytest                 # unit and mocked tests
ruff check .           # lint
ruff format --check .  # formatting
mypy downtube          # type checks
```

Media tests need `ffmpeg` and `ffprobe` on your `PATH`. They are skipped automatically
when the binaries are missing. Integration tests need real Google credentials and are
marked `integration`; they are skipped in CI.

```bash
pytest -m "not integration"          # what CI runs
pytest -m media                      # only tests that need ffmpeg
```

### Frontend (Node.js LTS)

```bash
cd frontend
npm ci
npm run dev        # development server
npm run test       # unit tests
npm run lint
npm run typecheck
```

Run the backend alongside the dev server while you work on the UI.

### Running the container locally

```bash
docker build -t downtube:dev .
```

Use `DOWNTUBE_BASE_URL=http://localhost:8092` and an SSH tunnel or local browser for OAuth.

## Branches and pull requests

1. Fork the repository, or create a branch in the main repository if you have write access.
   Use a descriptive prefix: `feat/`, `fix/`, `docs/`, `chore/`, `test/`.
2. Keep each pull request focused on one change. Separate refactors from behaviour changes.
3. Write or update tests for every behaviour change. Bug fixes need a test that fails
   before the fix.
4. Run the full backend and frontend checks listed above before you push.
5. Open the pull request against `main`. Describe what changed, why, and how you tested it.
6. Wait for the checks to pass and for a maintainer's review before merging.

Commit messages: a short imperative subject line (under 72 characters), followed by a body
that explains the reason for the change when it is not obvious.

## Tests are required

- New code paths need tests. Changes to `downloads/policy.py` need table-driven cases.
- Do not call the real YouTube API or yt-dlp in unit tests. Use the mocked YouTube client
  (`respx`) and the fake downloader adapter used in `backend/tests/downloads/`.
- Do not depend on network access in tests that are not marked `integration`.

## Never commit secrets

- Never commit `.env` files, OAuth client secrets, refresh tokens, cookies, `DOWNTUBE_SECRET_KEY`
  values, database files, logs, or archive media.
- Use `.env.example` for documenting variables, with placeholder values only.
- Do not put real email addresses, hostnames, IP addresses or NAS names in code, tests,
  fixtures or documentation. Use `example.com` and `nas.example.com` placeholders.
- If you accidentally commit a secret, rotate it immediately, then remove it from history.
  Report it as described in [SECURITY.md](SECURITY.md).

## Quality-policy invariants

The video quality policy is a product promise. Changes to `backend/downtube/downloads/policy.py`,
`validator.py`, `jobs.py` (`compute_effective_cap`) or the `manager.py` upgrade logic must keep
these invariants true. Each one has tests; a pull request that breaks one will not be merged.

1. The resolution cap is `min(requested or 2160, 2160)`. Nothing above 2160 (short side) is ever selected.
2. Never upscale: the output height never exceeds the source stream height.
3. Never re-encode video. ffmpeg uses stream copy only.
4. Resolution is never traded for codec, HDR or audio preference. Those only break ties
   at the same resolution, where the most compatible codec wins (H.264 > VP9 > AV1 > HEVC).
5. DRM-protected formats are never selected.
6. MP4 is the default container. MKV is used only when `mkv_fallback` is enabled and no
   MP4-compatible combination exists at the best resolution.
7. A file must pass ffprobe validation before it is placed in the archive.
8. An upgrade never deletes the previous copy before the replacement is in place.
9. Unknown files already in the archive are never overwritten.
10. DownTube never bypasses DRM, paywalls, geographic restrictions, age or membership gates,
    or login requirements. Such errors are reported, not worked around.

See [docs/quality-policy.md](docs/quality-policy.md) for the full rules.

## Security-sensitive changes

Changes touching authentication, sessions, token storage, path handling, subprocess calls or
container permissions deserve extra review. Read [SECURITY.md](SECURITY.md) first.

Subprocesses must use argument lists, never a shell string. Paths from users must go through
the storage-path helpers, which block traversal and symlink escapes.

## Code style

- Python: `ruff` with the repository settings (line length 120). Type hints on public functions.
- TypeScript/React: ESLint and `tsc --noEmit` must pass.
- Keep dependencies minimal. Justify any new runtime dependency in the pull request.

## Licensing

By contributing, you agree that your contributions are licensed under the MIT License
in [LICENSE](LICENSE).
