"""Seed a demo DownTube database with fictional channels, videos and jobs (for screenshots).

Usage: see tools/docs-screenshots/README.md. Prints a session cookie value on the last line.
"""

import os
import random
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from downtube.main import create_app
from fastapi.testclient import TestClient

_tc = TestClient(create_app(start_background=False))
_tc.__enter__()
from downtube.auth.sessions import create_session
from downtube.db import write_session
from downtube.models import *  # noqa
from downtube.security.crypto import encrypt

random.seed(7)
now = datetime.now(UTC)
STATIC = sys.argv[1]
PAL = [
    ("#1e3a8a", "#38bdf8"),
    ("#7c2d12", "#fb923c"),
    ("#14532d", "#4ade80"),
    ("#4c1d95", "#c084fc"),
    ("#831843", "#f472b6"),
    ("#134e4a", "#2dd4bf"),
    ("#713f12", "#facc15"),
    ("#1f2937", "#94a3b8"),
]


def thumb(name, label, i, w=320, h=180):
    a, b = PAL[i % len(PAL)]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs><rect width="{w}" height="{h}" fill="url(#g)"/><circle cx="{w * 0.78}" cy="{h * 0.3}" r="{h * 0.45}" fill="#fff" opacity="0.08"/><text x="16" y="{h - 20}" font-family="DejaVu Sans,Arial" font-size="{18 if w > 200 else 40}" font-weight="700" fill="#fff" opacity="0.92">{label.replace("&", "&amp;")}</text></svg>'''
    open(f"{STATIC}/demo/{name}.svg", "w").write(svg)
    return f"/demo/{name}.svg"


CH = [
    ("UCdemo0000000000000001", "Workshop Diaries", "Woodworking and small-shop builds.", 412),
    ("UCdemo0000000000000002", "Orbital Mechanics Lab", "Spaceflight explained with simulations.", 188),
    ("UCdemo0000000000000003", "Retro Computing Hour", "Restoring and programming vintage machines.", 655),
    ("UCdemo0000000000000004", "Alpine Trails", "Hiking and mountain photography.", 96),
    ("UCdemo0000000000000005", "Synth & Circuit", "Modular synthesis and DIY electronics.", 274),
    ("UCdemo0000000000000006", "The Map Room", "History told through maps.", 143),
    ("UCdemo0000000000000007", "Garden Physics", "Science experiments with everyday things.", 321),
    ("UCdemo0000000000000008", "Slow Kitchen", "Long recipes, done properly.", 507),
    ("UCdemo0000000000000009", "Field Recordings", "Nature sound, recorded in 4K.", 61),
    ("UCdemo0000000000000010", "Bridge Engineering 101", "How big structures stand up.", 129),
]
TITLES = {
    0: [
        "Building a workbench from one sheet of plywood",
        "Hand-cut dovetails, start to finish",
        "Restoring a 1950s hand plane",
        "The cheapest router table that works",
    ],
    1: [
        "Why Hohmann transfers are not always optimal",
        "Gravity assists, visualised",
        "Lagrange points in 12 minutes",
        "Docking is harder than you think",
    ],
    2: [
        "Reviving a dead home computer",
        "Writing a game in 6502 assembly",
        "Recapping a 40-year-old power supply",
        "Floppy disks: how they really work",
    ],
    3: ["Sunrise traverse of the ridge", "Three days, two passes, one tent", "Winter hut-to-hut route"],
    4: ["Patching a modular synth from scratch", "Build a simple filter on a breadboard", "Sequencers explained"],
    5: ["How borders were drawn in 1919", "The map that started a war", "Atlases of the 17th century"],
    6: [
        "Measuring the speed of sound with two phones",
        "Why ice floats (and why it matters)",
        "Rainbows in a glass of water",
    ],
    7: ["Twelve-hour ragù", "Sourdough without stress", "Stock: the foundation of everything"],
    8: ["Forest at dawn, 4K, no music", "Thunderstorm over the lake"],
    9: ["Why suspension bridges sway", "Concrete: the material that built the world"],
}
RES = [
    (3840, 2160, "vp9"),
    (2560, 1440, "vp9"),
    (1920, 1080, "vp9"),
    (1920, 1080, "h264"),
    (1280, 720, "h264"),
    (3840, 2160, "av1"),
]
with write_session() as s:
    u = User(
        google_sub="demo-sub",
        email="you@example.com",
        display_name="Demo User",
        is_admin=True,
        acknowledged_terms_at=now,
        last_login_at=now,
    )
    s.add(u)
    s.flush()
    acc = GoogleAccount(
        user_id=u.id,
        google_sub="demo-sub",
        email="you@example.com",
        scopes="openid email profile https://www.googleapis.com/auth/youtube.readonly",
        access_token_enc=encrypt("ya29.demo"),
        refresh_token_enc=encrypt("1//demo-refresh-token-000000000000"),
        token_expires_at=now + timedelta(hours=1),
        status=AccountStatus.active,
    )
    s.add(acc)
    for i, (cid, title, desc, count) in enumerate(CH):
        s.add(
            Channel(
                id=cid,
                title=title,
                description=desc,
                custom_url="@" + title.lower().replace(" ", "").replace("&", ""),
                thumbnail_url=thumb(f"ch{i}", title[:1], i, 88, 88),
                uploads_playlist_id="UU" + cid[2:],
                video_count=count,
                folder_name=title,
                metadata_fetched_at=now,
            )
        )
        s.add(
            Subscription(
                user_id=u.id, channel_id=cid, active=True, first_seen_at=now - timedelta(days=3), last_seen_at=now
            )
        )
    s.flush()
    modes = {
        0: ("follow_new", 2160),
        1: ("backfill_and_follow", 2160),
        2: ("follow_new", 1080),
        3: ("backfill_once", 2160),
        7: ("follow_new", 1080),
        8: ("backfill_and_follow", 2160),
    }
    rules = {}
    for i, (m, h) in modes.items():
        r = ChannelRule(
            user_id=u.id,
            channel_id=CH[i][0],
            mode=ArchiveMode(m),
            paused=False,
            max_height=h,
            include_shorts=False,
            include_live=False,
            activated_at=now - timedelta(days=2),
            sync_status=SyncStatus.ok,
            last_sync_at=now - timedelta(minutes=12),
            last_success_at=now - timedelta(minutes=12),
            backfill_completed_at=(now - timedelta(days=1)) if m.startswith("backfill") else None,
        )
        s.add(r)
        s.flush()
        rules[i] = r.id
    vid_n = 0
    jobs_spec = []
    for ci, titles in TITLES.items():
        for t in titles:
            vid_n += 1
            vid = f"demo{vid_n:07d}"
            pub = now - timedelta(days=random.randint(1, 900), hours=random.randint(0, 23))
            dur = random.randint(300, 2400)
            v = Video(
                id=vid,
                channel_id=CH[ci][0],
                channel_title=CH[ci][1],
                title=t,
                description="Demo video.",
                published_at=pub,
                duration_s=dur,
                is_short=False,
                is_live=False,
                availability=Availability.public,
                thumbnail_url=thumb(f"v{vid_n}", CH[ci][1], ci),
                metadata_fetched_at=now,
            )
            s.add(v)
            s.flush()
            liked = vid_n % 3 == 0
            src = DiscoverySource.like if liked else DiscoverySource.subscription
            s.add(
                VideoSource(
                    user_id=u.id,
                    video_id=vid,
                    source=src,
                    channel_rule_id=None if liked else rules.get(ci),
                    discovered_at=now - timedelta(hours=vid_n),
                    last_seen_at=now,
                    active=True,
                    archive_eligible=liked or ci in rules,
                )
            )
            if not liked and ci not in rules:
                continue
            jobs_spec.append((vid, ci, dur, t))
    s.flush()
    for k, (vid, ci, dur, t) in enumerate(jobs_spec):
        cap = modes.get(ci, ("", 2160))[1]
        if k < len(jobs_spec) - 7:  # completed
            w, h, vc = random.choice([r for r in RES if r[1] <= cap])
            mf = MediaFile(
                video_id=vid,
                root_path="/archive",
                rel_path=f"{CH[ci][1]}/{t[:40]} [{vid}].mp4",
                container="mp4",
                width=w,
                height=h,
                fps=random.choice([24.0, 25.0, 30.0, 60.0]),
                vcodec=vc,
                acodec="aac",
                duration_s=float(dur),
                size_bytes=int(dur * (h / 1080) ** 1.6 * 1.1e6),
                is_hdr=False,
                source_format_id="313+140",
                max_height_requested=cap,
                best_available_height=h,
                is_current=True,
                created_at=now - timedelta(hours=k),
            )
            s.add(mf)
            s.flush()
            s.add(
                DownloadJob(
                    video_id=vid,
                    user_id=u.id,
                    kind=JobKind.archive,
                    priority=50,
                    status=JobStatus.completed,
                    stage=JobStage.done,
                    max_height=cap,
                    attempts=1,
                    max_attempts=5,
                    progress_pct=100.0,
                    media_file_id=mf.id,
                    created_at=now - timedelta(hours=k + 1),
                    started_at=now - timedelta(hours=k, minutes=5),
                    finished_at=now - timedelta(hours=k),
                )
            )
        else:
            idx = k - (len(jobs_spec) - 7)
            if idx < 2:
                st, stg, pct = JobStatus.running, JobStage.downloading, [63.4, 18.9][idx]
                tot = int(dur * 1.6e6)
                j = DownloadJob(
                    video_id=vid,
                    user_id=u.id,
                    kind=JobKind.archive,
                    priority=50,
                    status=st,
                    stage=stg,
                    max_height=cap,
                    attempts=1,
                    max_attempts=5,
                    progress_pct=pct,
                    bytes_total=tot,
                    bytes_downloaded=int(tot * pct / 100),
                    speed_bps=[11.8e6, 7.4e6][idx],
                    eta_s=[48, 173][idx],
                    started_at=now - timedelta(minutes=2),
                )
            elif idx == 5:  # sign-in-only video: "Download myself" (0.4.0)
                j = DownloadJob(
                    video_id=vid,
                    user_id=u.id,
                    kind=JobKind.archive,
                    priority=50,
                    status=JobStatus.failed,
                    stage=JobStage.done,
                    max_height=cap,
                    attempts=1,
                    max_attempts=5,
                    error_code="age_restricted",
                    error_message="Sign in to confirm your age. This video may be inappropriate for some users.",
                    finished_at=now - timedelta(hours=1),
                )
            elif idx == 6:
                j = DownloadJob(
                    video_id=vid,
                    user_id=u.id,
                    kind=JobKind.archive,
                    priority=50,
                    status=JobStatus.failed,
                    stage=JobStage.done,
                    max_height=cap,
                    attempts=5,
                    max_attempts=5,
                    error_code="network",
                    error_message="HTTP Error 503: Service Unavailable (after 5 attempts)",
                    finished_at=now - timedelta(hours=3),
                )
            else:
                j = DownloadJob(
                    video_id=vid,
                    user_id=u.id,
                    kind=JobKind.archive,
                    priority=50,
                    status=JobStatus.queued,
                    stage=JobStage.pending,
                    max_height=cap,
                    attempts=0,
                    max_attempts=5,
                )
            s.add(j)
    for kind, seen, new, q, mins in [
        ("subscriptions", 10, 0, 2, 15),
        ("likes", 48, 2, 1, 14),
        ("channel", 31, 1, 3, 12),
        ("channel", 19, 0, 2, 12),
        ("likes", 48, 0, 1, 29),
    ]:
        s.add(
            SyncRun(
                user_id=u.id,
                kind=kind,
                trigger="schedule",
                status=SyncStatus.ok,
                started_at=now - timedelta(minutes=mins),
                finished_at=now - timedelta(minutes=mins - 1),
                items_seen=seen,
                items_new=new,
                jobs_queued=new,
                quota_used=q,
            )
        )
    s.add(QuotaUsage(day=now.strftime("%Y-%m-%d"), units=184))
    for k, v in [
        ("google_client_id", "123456789012-abcdefghijklmnop.apps.googleusercontent.com"),
        ("google_client_secret_enc", encrypt("GOCSPX-demo-not-a-real-secret")),
        ("allowed_emails", ["you@example.com", "family@example.com"]),
        ("admin_emails", ["you@example.com"]),
    ]:
        s.add(Setting(key=k, value=v))
    raw = create_session(s, u.id)
print(raw)
