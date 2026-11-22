"""Generate the DownTube documentation diagrams as SVG (python3 make_diagrams.py)."""

from pathlib import Path

OUT = Path(__file__).parent
INK, MUTED, LINE = "#0f172a", "#475569", "#94a3b8"
C = {  # fill, stroke
    "red": ("#fde8ec", "#be123c"),
    "blue": ("#e0f2fe", "#0369a1"),
    "green": ("#dcfce7", "#15803d"),
    "amber": ("#fef3c7", "#b45309"),
    "violet": ("#ede9fe", "#6d28d9"),
    "slate": ("#f1f5f9", "#475569"),
    "white": ("#ffffff", "#94a3b8"),
}
FONT = "DejaVu Sans, Segoe UI, Arial, sans-serif"


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class D:
    def __init__(self, w: int, h: int, title: str) -> None:
        self.w, self.h, self.el = w, h, []
        self.text(24, 38, title, 22, INK, bold=True)

    def text(self, x, y, t, size=14, fill=INK, bold=False, anchor="start", italic=False):
        self.el.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"'
            f"{' font-weight="700"' if bold else ''}{' font-style="italic"' if italic else ''}>{esc(t)}</text>"
        )

    def box(self, x, y, w, h, title, lines=(), color="slate", r=10, dashed=False, title_size=15):
        f, s = C[color]
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        self.el.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{f}" stroke="{s}" stroke-width="2"{dash}/>'
        )
        self.text(x + w / 2, y + 24, title, title_size, s if color != "white" else INK, bold=True, anchor="middle")
        for i, ln in enumerate(lines):
            self.text(x + w / 2, y + 46 + i * 18, ln, 12.5, MUTED, anchor="middle")

    def group(self, x, y, w, h, label, color="slate"):
        f, s = C[color]
        self.el.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" stroke="{s}" stroke-width="2" stroke-dasharray="8 5"/>'
        )
        self.text(x + 14, y + 22, label, 13, s, bold=True)

    def arrow(self, x1, y1, x2, y2, label="", color=LINE, dy=-8, both=False, lx=None, ly=None):
        mid = "url(#a)"
        start = ' marker-start="url(#as)"' if both else ""
        self.el.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.2" marker-end="{mid}"{start}/>'
        )
        if label:
            self.text(
                lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + dy,
                label,
                12,
                MUTED,
                anchor="middle",
            )

    def save(self, name):
        defs = (
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>'
            '<marker id="as" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>'
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
            f'{defs}<rect width="100%" height="100%" fill="#ffffff"/>' + "".join(self.el) + "</svg>\n"
        )
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")


# 1. Architecture ------------------------------------------------------------------------------
d = D(1200, 700, "DownTube architecture")
d.box(30, 300, 150, 90, "Browser", ["React UI", "phone / desktop"], "white")
d.box(225, 300, 170, 90, "Reverse proxy", ["DSM nginx / Caddy", "TLS (Let's Encrypt)"], "slate")
d.group(440, 70, 520, 560, "downtube container (non-root, read-only rootfs)", "red")
d.box(465, 110, 470, 70, "FastAPI web server", ["React SPA · REST API · sessions · CSRF · setup page"], "red")
d.box(465, 205, 225, 90, "Scheduler", ["likes poll · channel syncs", "nightly reconcile · backups"], "amber")
d.box(710, 205, 225, 90, "Download workers", ["DB-backed job queue", "retries · backoff · resume"], "amber")
d.box(
    710,
    320,
    225,
    110,
    "Media toolchain",
    ["yt-dlp (+ deno)", "ffmpeg: stream copy only", "ffprobe: validation"],
    "violet",
)
d.box(
    465,
    320,
    225,
    110,
    "SQLite (WAL)",
    ["users · channels · videos", "jobs · media files · quota", "secrets Fernet-encrypted"],
    "blue",
)
d.box(465, 455, 145, 80, "/data", ["DB + backups"], "slate", title_size=14)
d.box(625, 455, 145, 80, "/temp", ["partial downloads"], "slate", title_size=14)
d.box(785, 455, 150, 80, "/archive", ["final MP4 library"], "green", title_size=14)
d.text(700, 575, "bind mounts on the NAS volume", 12, MUTED, anchor="middle", italic=True)
d.box(1000, 110, 180, 80, "Google OAuth", ["sign-in (PKCE)", "youtube.readonly"], "blue")
d.box(1000, 215, 180, 80, "YouTube Data API", ["likes · subscriptions", "uploads (quota)"], "blue")
d.box(1000, 330, 180, 80, "YouTube media", ["original streams", "≤ 4K, no upscale"], "blue")
d.box(1000, 455, 180, 80, "Plex / Jellyfin", ["reads /archive", "(optional)"], "green")
d.arrow(180, 345, 225, 345, "HTTPS", dy=-10)
d.arrow(395, 345, 465, 150, "HTTP :8092", lx=375, ly=232)
d.arrow(935, 145, 1000, 150)
d.arrow(935, 250, 1000, 255)
d.arrow(935, 370, 1000, 370)
d.arrow(935, 495, 1000, 495)
d.arrow(822, 295, 822, 320)
d.arrow(577, 180, 577, 205)
d.arrow(577, 295, 577, 320)
d.arrow(822, 430, 700, 455)
d.arrow(860, 430, 860, 455)
d.arrow(540, 430, 540, 455)
d.save("architecture")

# 2. Network path ------------------------------------------------------------------------------
d = D(1200, 580, "Network path (public HTTPS on a Synology NAS)")
d.box(30, 80, 190, 90, "Phone / laptop", ["on the internet"], "white")
d.box(270, 80, 200, 90, "Public DNS", ["downtube.example.com", "CNAME → DDNS name"], "blue")
d.box(520, 80, 200, 90, "ISP router", ["forward TCP 80/443", "→ home router"], "slate")
d.box(770, 80, 200, 90, "Home router", ["forward TCP 80/443", "→ NAS LAN IP"], "slate")
d.group(720, 230, 460, 300, "NAS (Synology DSM)", "red")
d.box(
    745,
    270,
    410,
    90,
    "DSM nginx reverse proxy",
    ["Let's Encrypt certificate · HSTS", "rules: https:443 + http:80 → localhost:8092"],
    "red",
)
d.box(
    745,
    400,
    410,
    100,
    "downtube container",
    ["bound to 127.0.0.1:8092 only", "DOWNTUBE_TRUSTED_PROXIES = docker bridge", "HTTP → HTTPS 308 redirect"],
    "amber",
)
d.arrow(220, 125, 270, 125)
d.arrow(470, 125, 520, 125)
d.arrow(720, 125, 770, 125)
d.arrow(870, 170, 950, 270, "80 / 443", lx=945, ly=215)
d.arrow(950, 360, 950, 400)
d.box(30, 300, 230, 90, "LAN client", ["same home network"], "white")
d.box(30, 420, 300, 90, "Local DNS (e.g. Pi-hole)", ["downtube.example.com", "→ NAS LAN IP (split DNS)"], "green")
d.arrow(145, 390, 145, 420, "1. lookup", lx=200, ly=410)
d.arrow(260, 330, 745, 315, "2. HTTPS straight to the NAS LAN IP", lx=500, ly=305)
d.text(
    30,
    560,
    "Without split DNS, LAN clients resolve the public IP and time out on routers without NAT loopback.",
    12.5,
    MUTED,
    italic=True,
)
d.save("network")

# 3. Download pipeline -------------------------------------------------------------------------
d = D(1200, 470, "From a like or a new upload to a file in the archive")
steps = [
    ("Discover", ["likes poll", "channel rules", "manual URL"], "blue"),
    ("Queue", ["job in SQLite", "dedup per video", "priority + cap"], "slate"),
    ("Probe", ["yt-dlp -J", "pick best original", "≤ cap, no upscale"], "violet"),
    ("Download", ["video + audio", "into /temp", "resumable"], "amber"),
    ("Merge", ["ffmpeg remux", "stream copy → MP4", "no re-encode"], "amber"),
    ("Validate", ["ffprobe checks", "codecs, size,", "duration, height"], "violet"),
    ("Finalize", ["atomic move to", "/archive/<Channel>/", "record media file"], "green"),
]
x = 25
for i, (t, ls, c) in enumerate(steps):
    d.box(x, 90, 150, 120, t, ls, c)
    if i:
        d.arrow(x - 18, 150, x, 150)
    x += 168
d.box(
    25,
    270,
    560,
    150,
    "Failure handling",
    [
        "network / 5xx / throttling → retry with exponential backoff (max attempts)",
        "video private or deleted → skipped, availability recorded",
        "no acceptable stream/container → unsupported (never re-encoded)",
        "restart or shutdown → job re-queued, partial data kept, attempt not counted",
    ],
    "red",
)
d.box(
    615,
    270,
    560,
    150,
    "Quality policy",
    [
        "cap = channel / likes / manual setting (720p … 2160p)",
        "short side ≤ cap, so portrait and ultrawide videos work",
        "prefer the highest original stream; AAC audio by default",
        "a later higher cap creates an 'upgrade' job; old file kept as .superseded",
    ],
    "green",
)
d.save("pipeline")

# 4. First run ---------------------------------------------------------------------------------
d = D(1200, 330, "First run: from container start to the dashboard")
flow = [
    ("1. Deploy", ["docker compose up", "or deploy_nas.sh"], "slate"),
    ("2. Setup code", ["printed in the", "container log"], "amber"),
    ("3. /setup page", ["code + Google client", "ID/secret + allowlist"], "red"),
    ("4. Google sign-in", ["OAuth PKCE", "youtube.readonly"], "blue"),
    ("5. Acknowledge", ["rights &", "responsibility"], "violet"),
    ("6. Dashboard", ["sync subscriptions", "choose archive rules"], "green"),
]
x = 25
for i, (t, ls, c) in enumerate(flow):
    d.box(x, 90, 172, 110, t, ls, c)
    if i:
        d.arrow(x - 22, 145, x, 145)
    x += 197
d.text(
    25,
    250,
    "The setup code is single-use and stored only as a hash; a new one is printed on every start until setup is complete.",
    13,
    MUTED,
)
d.text(
    25,
    275,
    "Client secret and tokens are stored Fernet-encrypted; the allowlist is checked on every request and by the scheduler.",
    13,
    MUTED,
)
d.save("first-run")
print("ok")
