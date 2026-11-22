# Documentation screenshots and diagrams

Everything in `docs/images/` is generated from fictional demo data: no real accounts, channels or hosts.

## Diagrams

```bash
python3 docs/images/diagrams/make_diagrams.py          # writes the .svg files
python tools/docs-screenshots/render_svg.py docs/images/diagrams/*.svg   # .png next to each .svg (needs playwright)
```

## Screenshots

Requires the backend dev environment, a built frontend (`npm run build` in `frontend/`), and
Playwright for Python (`pip install playwright`) with a Chromium at `/usr/bin/chromium`.

```bash
export DEMO_DIR=$(mktemp -d) PYTHON=~/.venvs/downtube/bin/python
tools/docs-screenshots/run.sh a 8801 > $DEMO_DIR/a.log 2>&1 &          # setup-mode instance (log holds the setup code)
tools/docs-screenshots/run.sh c 8802 seed | tail -1 > $DEMO_DIR/sess    # seed demo data, keep the session cookie
tools/docs-screenshots/run.sh c 8802 > $DEMO_DIR/c.log 2>&1 &           # seeded instance
python tools/docs-screenshots/shoot.py $DEMO_DIR $DEMO_DIR/shots
```

`demo_app.py` fakes a 64 TB volume and shows the archive as `/archive`; `shoot.py` masks local
paths and `localhost` URLs as `https://downtube.example.com`. Copy the PNGs you need into
`docs/images/screenshots/`.
