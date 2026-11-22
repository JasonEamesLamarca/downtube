// Build docs/DownTube-Guide.docx — the end-to-end guide with a step-by-step deployment section.
//
//   npm install docx@9          (anywhere; then run with NODE_PATH pointing at its node_modules)
//   node tools/docs-guide/build_guide.js
//
// Images come from docs/images/ (fictional demo data, see tools/docs-screenshots/README.md).
// Keep this file free of private deployment details: example hostnames only.

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, ImageRun, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, LevelFormat, TableOfContents, Header, Footer, PageNumber, PageBreak,
  ExternalHyperlink, TabStopType,
} = require("docx");

const REPO = path.resolve(__dirname, "..", "..");
const IMG = (p) => path.join(REPO, "docs", "images", p);
const VERSION = "0.4.3";
const DATE = "November 2026";

const FONT = "Calibri";
const MONO = "Courier New";
const RED = "BE123C";
const INK = "0F172A";
const MUTED = "475569";
const CONTENT_W = 9026; // A4 width 11906 - 2 x 1440 margins (DXA)

// ------------------------------------------------------------------ inline markup
// **bold**, `code`, _italic_
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: MONO, size: 19, color: "9F1239", ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), italics: true, ...base }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}

const body = [];
const P = (text, opts = {}) =>
  body.push(new Paragraph({ children: runs(text, opts.run || {}), spacing: { after: 120 }, keepNext: /:\)?$/.test(text.trim()), ...opts.para }));
const H1 = (t, pageBreak = true) => body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: pageBreak, children: [new TextRun(t)] }));
const H2 = (t) => body.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] }));
const H3 = (t) => body.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] }));
const BUL = (items, level = 0) => items.forEach((t) => body.push(new Paragraph({ numbering: { reference: "bullets", level }, children: runs(t), spacing: { after: 60 } })));
let numberedList = 0;
const NUM = (items) => {
  numberedList += 1;
  const ref = `steps${numberedList}`;
  numberingConfigs.push({
    reference: ref,
    levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }],
  });
  items.forEach((t) => body.push(new Paragraph({ numbering: { reference: ref, level: 0 }, children: runs(t), spacing: { after: 80 } })));
};
const numberingConfigs = [
  {
    reference: "bullets",
    levels: [
      { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1000, hanging: 300 } } } },
    ],
  },
];

function CODE(text) {
  const lines = text.replace(/\n$/, "").split("\n");
  lines.forEach((ln, i) =>
    body.push(
      new Paragraph({
        children: [new TextRun({ text: ln || " ", font: MONO, size: 18, color: INK })],
        shading: { type: ShadingType.CLEAR, fill: "F1F5F9", color: "auto" },
        border: {
          left: { style: BorderStyle.SINGLE, size: 12, color: "94A3B8", space: 6 },
          ...(i === 0 ? { top: { style: BorderStyle.SINGLE, size: 2, color: "F1F5F9", space: 4 } } : {}),
          ...(i === lines.length - 1 ? { bottom: { style: BorderStyle.SINGLE, size: 2, color: "F1F5F9", space: 4 } } : {}),
        },
        indent: { left: 120 },
        spacing: { after: i === lines.length - 1 ? 160 : 0, line: 260 },
        keepLines: true,
        keepNext: i !== lines.length - 1,
      }),
    ),
  );
}

function NOTE(text, kind = "note") {
  const c = { note: ["EFF6FF", "0369A1", "Note"], tip: ["F0FDF4", "15803D", "Tip"], warn: ["FFF7ED", "B45309", "Important"] }[kind];
  body.push(
    new Paragraph({
      children: [new TextRun({ text: `${c[2]}: `, bold: true, color: c[1] }), ...runs(text)],
      shading: { type: ShadingType.CLEAR, fill: c[0], color: "auto" },
      border: { left: { style: BorderStyle.SINGLE, size: 24, color: c[1], space: 8 } },
      indent: { left: 160, right: 160 },
      spacing: { before: 80, after: 200 },
    }),
  );
}

function pngSize(file) {
  const b = fs.readFileSync(file);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}

let figure = 0;
function IMAGE(rel, caption, maxW = 600, maxH = 760) {
  const file = IMG(rel);
  const [w, h] = pngSize(file);
  let width = maxW;
  let height = Math.round((h / w) * width);
  if (height > maxH) {
    height = maxH;
    width = Math.round((w / h) * height);
  }
  figure += 1;
  body.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      keepNext: true,
      spacing: { before: 120, after: 60 },
      children: [new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width, height }, altText: { title: caption, description: caption, name: rel } })],
    }),
  );
  body.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 240 },
      children: [new TextRun({ text: `Figure ${figure}. `, bold: true, size: 18, color: MUTED }), new TextRun({ text: caption, italics: true, size: 18, color: MUTED })],
    }),
  );
}

const cellBorder = { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" };
function TABLE(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const scale = CONTENT_W / total;
  const w = widths.map((x) => Math.floor(x * scale));
  w[w.length - 1] += CONTENT_W - w.reduce((a, b) => a + b, 0);
  const mk = (text, i, head) =>
    new TableCell({
      width: { size: w[i], type: WidthType.DXA },
      borders: { top: cellBorder, bottom: cellBorder, left: cellBorder, right: cellBorder },
      shading: head ? { type: ShadingType.CLEAR, fill: "FDE8EC", color: "auto" } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: String(text)
        .split("\n")
        .map((ln) => new Paragraph({ children: runs(ln, head ? { bold: true, color: RED, size: 20 } : { size: 20 }), spacing: { after: 20 } })),
    });
  body.push(
    new Table({
      width: { size: CONTENT_W, type: WidthType.DXA },
      columnWidths: w,
      rows: [
        new TableRow({ tableHeader: true, cantSplit: true, children: headers.map((t, i) => mk(t, i, true)) }),
        ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((t, i) => mk(t, i, false)) })),
      ],
    }),
  );
  body.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}

// ================================================================== CONTENT

// ---- Title page
body.push(new Paragraph({ spacing: { before: 2400, after: 120 }, children: [new TextRun({ text: "DownTube", bold: true, size: 72, color: RED, font: FONT })] }));
body.push(new Paragraph({ spacing: { after: 360 }, children: [new TextRun({ text: "Self-hosted YouTube archiver", size: 36, color: INK })] }));
body.push(new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: "End-to-end guide: what it is, how it works, how to deploy and run it", size: 26, color: MUTED })] }));
body.push(new Paragraph({ spacing: { after: 1200 }, children: [new TextRun({ text: `Version ${VERSION} · ${DATE}`, size: 22, color: MUTED })] }));
body.push(
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new ImageRun({ type: "png", data: fs.readFileSync(IMG("screenshots/dashboard.png")), transformation: { width: 560, height: Math.round(560 * 1350 / 2160) }, altText: { title: "Dashboard", description: "DownTube dashboard", name: "cover" } })],
  }),
);
body.push(new Paragraph({ spacing: { before: 600 }, children: runs("Source code and issues: https://github.com/JasonEamesLamarca/downtube · License: see LICENSE in the repository. DownTube is an independent tool and is not affiliated with, endorsed by, or sponsored by Google or YouTube.", { size: 18, color: MUTED }) }));

// ---- Contents
body.push(new Paragraph({ pageBreakBefore: true, children: [new TextRun({ text: "Contents", bold: true, size: 32, color: RED })], spacing: { after: 200 } }));
body.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
body.push(new Paragraph({ children: runs("_If the table of contents is empty, right-click it in Word and choose Update Field (LibreOffice: Tools > Update > Update All)._", { size: 18, color: MUTED }) }));

// ---- 1. Overview
H1("1. What DownTube is");
P("DownTube keeps your own copy of the YouTube videos that matter to you. It watches the videos you **like** and the **channels you choose** from your subscriptions, and downloads each video once, in the best original quality YouTube offers, up to 4K. The files land in a normal folder on your NAS, organised by channel, ready for Plex, Jellyfin or any video player.");
P("It is built for a single household on a home NAS (tested on a Synology DS1821+ with DSM 7), runs as one Docker container, and is administered entirely from a web interface.");
H2("Design principles");
BUL([
  "**Original quality only.** DownTube picks the best original stream up to the limit you set (maximum 2160p). It never upscales and never re-encodes: audio and video are stream-copied into an MP4 container.",
  "**Explicit choices.** Nothing is archived until you turn it on: likes have a mode (off, future only, all) and every channel has its own rule.",
  "**Your data stays home.** Videos, metadata and the database live on your NAS. The only outside services are Google sign-in, the YouTube Data API and YouTube's own video servers.",
  "**Safe by default.** Google sign-in with an allowlist, encrypted tokens, a hardened container (non-root, read-only filesystem, no capabilities), and no access to the Docker socket or host network.",
  "**Robust.** A database-backed job queue survives restarts and power cuts; downloads resume from partial files; failed downloads retry with backoff.",
]);
H2("What it does, in one picture");
IMAGE("diagrams/pipeline.png", "From a like or a new upload to a validated MP4 file in the archive.");
H2("Status");
P(`Version ${VERSION} is in production use on a Synology NAS behind the DSM reverse proxy with a Let's Encrypt certificate. Real Google sign-in (OAuth with PKCE) and the YouTube Data API have been verified live: sign-in and allowlist refusal, subscription sync (234 channels for 10 quota units), likes baseline, archiving of hundreds of subscription videos, and resuming interrupted downloads across an upgrade. Version 0.3.0 added deleting videos from the library, unsubscribing from channels, and description files for media servers. Version 0.4.0 lets you archive age-restricted and other sign-in-only videos by downloading them yourself and importing the file.`);

// ---- 2. How it works
H1("2. How it works");
IMAGE("diagrams/architecture.png", "Components inside the container and the services around it.");
H2("Components");
TABLE(
  ["Component", "Role"],
  [
    ["Web server (FastAPI)", "Serves the React interface and the REST API, handles sign-in, sessions (cookie + CSRF token) and the first-run setup page."],
    ["Scheduler (APScheduler)", "Polls likes (every 15 min), checks channels with an active rule (every 60 min), refreshes subscriptions and reconciles likes (every 24 h), and makes a daily database backup."],
    ["Download workers", "Take jobs from the queue in the database (2 at a time by default), honour the download window, bandwidth limit and free-space floor, and retry failures with exponential backoff."],
    ["yt-dlp, ffmpeg, ffprobe", "yt-dlp lists the available streams and downloads them; ffmpeg joins video and audio without re-encoding; ffprobe checks the result before it is accepted."],
    ["SQLite (WAL mode)", "Users, Google accounts, channels, rules, videos, download jobs, media files, sync runs, quota usage, settings and an audit log. Secrets are encrypted with Fernet keys derived from `DOWNTUBE_SECRET_KEY`."],
    ["Volumes", "`/data` (database and backups), `/temp` (partial downloads), `/logs`, and `/archive` (finished files)."],
  ],
  [2200, 6800],
);
H2("Discovering videos");
BUL([
  "**Likes.** DownTube reads your liked-videos list. In _future only_ mode (the default) your existing likes are recorded as a baseline and not downloaded; every like after that is archived. _All_ archives existing likes too (YouTube returns only the newest 1,000).",
  "**Subscriptions.** Your subscriptions are listed with thumbnails and video counts. Each channel has a rule: _Off_, _Backfill existing once_, _Backfill and follow_, or _Follow new uploads_, plus a maximum resolution and filters (shorts, livestreams, published after, maximum duration).",
  "**Manual.** Paste a video ID or URL on the Downloads page.",
]);
H2("Quality policy");
P("Every rule has a **maximum resolution**. DownTube compares the short side of the video with the limit, so portrait and ultrawide videos are handled correctly, and chooses the highest original stream at or below it.");
TABLE(
  ["Source offers", "Maximum set", "Result"],
  [
    ["480p", "Best available (≤4K)", "480p (never upscaled)"],
    ["2160p (4K)", "1080p", "1080p"],
    ["2160p (4K)", "Best available", "2160p"],
    ["4320p (8K)", "Best available", "2160p (nothing above 4K)"],
  ],
  [3000, 3000, 3000],
);
P("Audio is AAC by default (Opus can be preferred). If you later raise the limit for a video that is already archived, DownTube creates an _upgrade_ job and keeps the old file as `*.superseded.mp4` unless you turn that off.");
H2("YouTube API quota");
P("Each Google Cloud project gets 10,000 quota units a day; DownTube budgets 9,000 by default and never uses the expensive search endpoint. Listing 50 likes, subscriptions or uploads costs one unit, so even hundreds of channels use only a small part of the budget. When the budget runs out, syncs are deferred until the quota resets (midnight Pacific Time) and likes polling keeps priority.");
H2("Security model");
BUL([
  "**Sign-in:** Google OAuth 2.0 authorisation code flow with PKCE, state and nonce. The YouTube scope is `youtube.readonly`. The optional `youtube.force-ssl` permission (unsubscribe, remove a like) is requested only when you grant it in Settings, and is used only for actions you confirm.",
  "**Allowlist:** only the Google accounts you list can sign in; the list is re-checked on every request and by the scheduler. Removing an account ends its sessions and stops its syncs.",
  "**Secrets at rest:** OAuth tokens and the client secret are Fernet-encrypted in the database; the setup code is stored only as a SHA-256 hash.",
  "**Web hardening:** HttpOnly session cookies, CSRF token header for every change, strict Content-Security-Policy, HSTS over HTTPS, API documentation only for signed-in users, rate-limited setup page.",
  "**Container hardening:** runs as a fixed non-root user, read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, its own bridge network, memory limit, bounded logs.",
  "**Proxy awareness:** client IP and scheme headers are honoured only from the proxy networks listed in `DOWNTUBE_TRUSTED_PROXIES`.",
]);

// ---- 3. Using DownTube
H1("3. A tour of the interface");
P("All screenshots in this guide use fictional demo data. The interface follows the system light or dark theme and works on phones.");
H2("Dashboard");
IMAGE("screenshots/dashboard.png", "Dashboard: archive totals, download activity, storage, API quota and recent sync runs.");
P("**Synchronize now** starts a sync right away instead of waiting for the next scheduled run.");
H2("Subscriptions");
IMAGE("screenshots/subscriptions.png", "Subscriptions: one rule per channel, with mode, maximum resolution, filters and per-channel status.");
TABLE(
  ["Mode", "What it archives"],
  [
    ["Off (default)", "Nothing. Files already archived stay where they are."],
    ["Backfill existing once", "All existing uploads, once."],
    ["Backfill and follow", "All existing uploads, then every new upload."],
    ["Follow new uploads", "Only uploads published after the rule is switched on."],
  ],
  [2600, 6400],
);
NOTE("Backfilling a channel with more videos than the large-channel threshold (200 by default) first shows the number of videos and an estimate, and asks for confirmation.", "tip");
P("**Unsubscribe** unsubscribes your YouTube account from the channel, turns its rule off and cancels its queued downloads. Videos already archived stay in the library. A channel you left on YouTube is marked _Unsubscribed_.");
IMAGE("screenshots/unsubscribe.png", "Unsubscribing asks for confirmation; without the optional permission it offers to grant it first.");
H2("Liked videos");
IMAGE("screenshots/likes.png", "Liked videos: everything DownTube has seen in your likes, with archive status.");
H2("Downloads");
IMAGE("screenshots/downloads.png", "Downloads: live progress, speed and ETA; pause, cancel and retry; a link to each video on YouTube; archive a single video by URL; the import folder.");
H3("Age-restricted and other sign-in-only videos");
P("DownTube downloads without signing in to YouTube and never receives your YouTube cookies. Videos that YouTube only shows to a signed-in account (age-restricted, members-only) therefore fail with `age_restricted`, `login_required` or `members_only`. **Download myself** on such a job opens a dialog with a link to the video and a ready-made yt-dlp command for your own computer. The command reads your browser's sign-in locally, keeps the original streams up to the job's resolution limit, merges them to MP4 without re-encoding, embeds the metadata and puts the video ID in the file name. Pick the browser you are signed in with and, on Linux, the keyring (choose gnomekeyring on GNOME or COSMIC, kwallet on KDE, if yt-dlp reports \"cannot decrypt v11 cookies\"); the dialog remembers your choices. You need yt-dlp (update it with `yt-dlp -U`), ffmpeg and a JavaScript runtime (Deno, or Node.js selected in the dialog) on that computer; on Debian-based Linux also `python3-secretstorage`. On Windows use Firefox, because yt-dlp usually cannot read Chrome or Edge cookies there.");
P("Then either **upload** the file in the same dialog, or copy it into the **import folder** `<archive>/.import` (on the NAS, for example `/volume1/video/DownTube/.import`). DownTube scans that folder every 5 minutes, or straight away with **Scan now**; the file name must contain the video ID in brackets, such as `Title [dQw4w9WgXcQ].mp4`. Before archiving, DownTube checks that the file is MP4 or MKV/WebM, that it lasts as long as the YouTube video, that any embedded metadata names the same video, and that it does not exceed the resolution limit. A file that passes is moved into the channel folder with the usual name, thumbnail and `.nfo`, and the job is marked completed; a file that fails stays where it is, with the reason listed under Import folder.");
IMAGE("screenshots/download-myself.png", "Download myself: a link to the video, the yt-dlp command for your own computer, and the upload.");
H2("Library");
IMAGE("screenshots/library.png", "Library: archived files with resolution, codec, size and the path inside the archive.");
P("Files are stored as `<Channel>/YYYY-MM-DD - Title [VIDEOID].mp4`, with a thumbnail (`.jpg`) and an `.nfo` file next to each video. The `.nfo` holds the title, the full YouTube description, the date, the runtime and the channel, in the format Plex's NFO agent, Jellyfin, Emby and Kodi read. New MP4 files also carry the title, description and date as embedded tags (copied in, never re-encoded).");
P("The trash button deletes a video: the file, its thumbnail, its `.nfo` and any kept older copy are removed, and DownTube will not download that video again, whether it came from a like or from a channel. For a liked video you can also remove the like on YouTube. To get a deleted video back, archive it again by URL on the Downloads page.");
IMAGE("screenshots/library-delete.png", "Deleting a liked video. Removing the like on YouTube needs the optional permission.");
H2("Settings");
P("Settings group the archive folder, likes monitoring, quality and formats, download limits (concurrency, bandwidth, time window), disk safety, media server files (thumbnail, `.nfo`, embedded tags, and a button that writes `.nfo` files for videos archived earlier), the API quota budget, Google sign-in and access (administrators only) and the log viewer. **Account & connection** shows whether the optional permission to manage your YouTube account is granted, with a button to grant it.");
IMAGE("screenshots/settings.png", "Settings (full page).", 600, 900);
H2("Dark theme and phones");
IMAGE("screenshots/subscriptions-dark.png", "Dark theme.");
IMAGE("screenshots/dashboard-mobile.png", "Dashboard on a phone.", 260, 560);

// ---- 4. Deployment guide
H1("4. Deployment guide (Synology DSM 7)");
P("This chapter takes you from nothing to a working, publicly reachable DownTube at `https://downtube.example.com` on a Synology NAS. Replace the example names with your own. The same steps work on any Linux host with Docker: skip the DSM-specific parts and use your own reverse proxy (Caddy, nginx, Traefik).");
IMAGE("diagrams/network.png", "The network path: public DNS, port forwards, DSM reverse proxy and the container bound to localhost.");
NOTE("Publishing DownTube on the internet exposes its sign-in page. Only Google accounts on your allowlist can get past it, but if you do not need access from outside, keep it on your LAN or a VPN such as Tailscale and skip steps 4.3 to 4.5.", "warn");

H2("4.1 Before you start");
TABLE(
  ["You need", "Notes"],
  [
    ["Synology NAS with DSM 7.2+", "Container Manager installed from Package Center; SSH enabled (Control Panel > Terminal & SNMP)."],
    ["A computer with SSH and Git", "Linux, macOS or Windows (WSL). Key-based SSH login to the NAS."],
    ["A domain name", "For example `example.com` at any DNS provider, plus Synology DDNS (or a fixed public IP)."],
    ["A Google account", "The one whose likes and subscriptions you want to archive."],
    ["Disk space", "4K video uses roughly 2 to 6 GB per hour. Plan for growth; DownTube keeps 100 GB free by default."],
    ["About 1 hour", "Most of it is Google Cloud and DSM clicks."],
  ],
  [2600, 6400],
);

H2("4.2 Create the Google Cloud project and OAuth client");
P("DownTube signs you in with Google and reads your YouTube data with your permission. For that it needs its own OAuth client in a Google Cloud project that you own.");
NUM([
  "Open **https://console.cloud.google.com/**, choose **Select a project > New project**, name it `DownTube` and create it.",
  "Go to **APIs & Services > Library**, search for **YouTube Data API v3** and select **Enable**.",
  "Go to **Google Auth Platform > Branding** (older consoles: OAuth consent screen). Fill in: **App name** `DownTube`; **User support email**: your address; **Application home page**: `https://downtube.example.com`; **Privacy policy link**: for example the repository's `PRIVACY.md` on GitHub; **Authorised domains**: `example.com` and the domain of every link you entered (add `github.com` if the privacy link is on GitHub); **Developer contact**: your address. Select **Save**.",
  "Go to **Audience**. User type **External**. Select **Publish app** and confirm, so the status reads **In production**.",
  "Go to **Clients > Create client**. Application type **Web application**, name `DownTube web`. **Authorised JavaScript origins**: `https://downtube.example.com`. **Authorised redirect URIs**: `https://downtube.example.com/api/auth/callback`. Select **Create**.",
  "Copy the **Client ID** and the **Client secret** from the dialog (or choose **Download JSON**) and keep them somewhere safe. You will paste them into DownTube's setup page in step 4.9.",
]);
NOTE("Leave the app **In production**. In Testing mode Google expires the sign-in after 7 days and DownTube would stop syncing. Production does not mean public: only accounts on DownTube's allowlist can use it. Google shows an \"unverified app\" warning at sign-in; that is expected for a personal app.", "warn");
P("Optional: to unsubscribe and remove likes from DownTube, go to **Data access > Add or remove scopes**, add `https://www.googleapis.com/auth/youtube.force-ssl` and save. DownTube asks for it only when a user grants it in Settings.");
NOTE("The new Google Auth Platform shows the client secret only once. If you lose it, add a new secret to the client and enter it in DownTube under Settings > Google sign-in & access.", "note");

H2("4.3 Point a hostname at your home");
NUM([
  "Make sure your NAS has a DDNS name: **Control Panel > External Access > DDNS** (for example `yourname.synology.me`).",
  "At your DNS provider, add a **CNAME** record: name `downtube`, value `yourname.synology.me`, TTL 1 hour.",
  "Check it from your computer: `dig +short downtube.example.com` should print the DDNS name and your public IP address.",
]);

H2("4.4 Forward ports 80 and 443 to the NAS");
P("In your router, forward **TCP 80** and **TCP 443** to the NAS LAN address (for example `192.168.1.50`). Port 80 is needed for Let's Encrypt renewals and the HTTP to HTTPS redirect. Never forward 8092.");
NOTE("With **double NAT** (an ISP router in front of your own router) forward 80 and 443 on **every** router in the chain: ISP router to your router's WAN address, then your router to the NAS.", "warn");
P("If these ports are already forwarded for another site on the same NAS, there is nothing to do: the DSM reverse proxy routes by hostname.");

H2("4.5 Get a certificate in DSM");
NUM([
  "**Control Panel > Security > Certificate > Add > Add a new certificate**.",
  "Choose **Get a certificate from Let's Encrypt**, domain `downtube.example.com`, your email. Select **Done**. (DNS from step 4.3 must already resolve.)",
  "Keep the certificate; you will assign it to the reverse proxy rule in step 4.8.",
]);

H2("4.6 Prepare the NAS");
P("Over SSH, find the user and group IDs that will own the files, and create the folders. The examples use a user with uid 1026 and gid 100 (users).");
CODE(`ssh admin@nas.example.lan
id                      # uid=1026(admin) gid=100(users) ...
sudo mkdir -p /volume1/docker/downtube/{data,logs,temp} /volume1/video/DownTube
sudo chown -R 1026:100 /volume1/docker/downtube /volume1/video/DownTube`);
P("DSM shared folders such as `video` often grant write access through the **administrators** group ACL (gid 101). A container only has the groups you give it, so add `group_add: [\"101\"]` to the service if the health check later reports `archive_writable: false`.");

H2("4.7 Configure and deploy the container");
P("Clone the repository on your computer and create two files for your NAS: a compose file and an environment template. Keep both outside the repository (they contain your hostnames).");
CODE(`git clone https://github.com/JasonEamesLamarca/downtube.git
mkdir -p ~/downtube-nas && cd ~/downtube-nas`);
P("**docker-compose.yml** (based on the repository's file, with NAS paths and a pinned version):");
CODE(`name: downtube
services:
  downtube:
    image: ghcr.io/jasoneameslamarca/downtube:${VERSION}
    container_name: downtube
    restart: unless-stopped
    ports:
      - "127.0.0.1:8092:8092"          # only the DSM reverse proxy can reach it
    env_file: [.env]
    user: "1026:100"
    group_add: ["101"]                 # administrators: write access to the video share
    volumes:
      - ./data:/data
      - ./logs:/logs
      - ./temp:/temp
      - /volume1/video/DownTube:/archive
    read_only: true
    tmpfs: ["/tmp:size=256m,mode=1777"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    networks: [downtube_net]
    mem_limit: 2g                      # no cpus: DSM kernels refuse CPU quotas
networks:
  downtube_net:
    name: downtube_net`);
P("**env.template** (merged into the `.env` on the NAS; the secret key is generated for you):");
CODE(`DOWNTUBE_SECRET_KEY=
DOWNTUBE_BASE_URL=https://downtube.example.com
DOWNTUBE_TRUSTED_PROXIES=172.16.0.0/12
DOWNTUBE_STORAGE_MOUNTS=/archive
DOWNTUBE_ARCHIVE_ROOT=/archive
DOWNTUBE_HOST_ARCHIVE_PATH=/volume1/video/DownTube
TZ=Europe/Madrid
# Leave these empty: you enter them in the setup page.
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
DOWNTUBE_ALLOWED_EMAILS=`);
P("Run the deploy script from the repository. It creates the folders, copies the compose file over SSH (DSM has no scp), merges `.env` (generating the secret key, mode 600, never printed), pulls the image, starts the container, waits for health, checks that the container can reach Google and YouTube, and prints the setup code.");
CODE(`cd ~/downtube   # the repository
NAS=admin@nas.example.lan \\
NAS_DIR=/volume1/docker/downtube \\
ARCHIVE_DIR=/volume1/video/DownTube \\
COMPOSE_FILE=~/downtube-nas/docker-compose.yml \\
ENV_TEMPLATE=~/downtube-nas/env.template \\
IMAGE=ghcr.io/jasoneameslamarca/downtube:${VERSION} \\
  deploy/deploy_nas.sh`);
P("Expected end of the output:");
CODE(`==> Waiting for health
{"status":"ok","version":"${VERSION}","db":true,"worker":true,"scheduler":true,"archive_writable":true}
==> Checking outbound DNS/HTTPS from inside the container
    ok
==> Setup required: open https://downtube.example.com/setup and enter the setup code: ABCD-EFGH-JKLM`);
TABLE(
  ["If you see", "Do this"],
  [
    ["`NanoCPUs can not be set`", "Remove any `cpus:` line; keep `mem_limit`."],
    ["`archive_writable: false`", "Add `group_add: [\"101\"]` (or the group that has access to the share) and run the script again."],
    ["The DNS/HTTPS check fails (exit code 2)", "Add `dns: [1.1.1.1, 8.8.8.8]` to the service and run the script again."],
    ["The image pull fails", "Check the NAS can reach ghcr.io, or use `SHIP=1` to build locally and stream the image over SSH."],
  ],
  [3400, 5600],
);
NOTE("Prefer a graphical setup? In **Container Manager > Project > Create**, point the project at `/volume1/docker/downtube`, paste the compose file and create `.env` in that folder yourself (generate the key with `openssl rand -hex 32`, then `chmod 600 .env`). The result is the same.", "tip");

H2("4.8 Create the DSM reverse proxy rules");
P("Open **Control Panel > Login Portal > Advanced > Reverse Proxy** and create two rules:");
TABLE(
  ["Setting", "Rule 1: downtube-https", "Rule 2: downtube-http"],
  [
    ["Source protocol", "HTTPS", "HTTP"],
    ["Source hostname", "downtube.example.com", "downtube.example.com"],
    ["Source port", "443", "80"],
    ["Enable HSTS", "Yes", "(not available)"],
    ["Destination", "HTTP · localhost · 8092", "HTTP · localhost · 8092"],
    ["Advanced > proxy read timeout", "120 seconds", "default"],
  ],
  [2800, 3100, 3100],
);
P("Then go to **Control Panel > Security > Certificate > Settings**, find the service `downtube.example.com` and choose the certificate from step 4.5. Without this DSM serves its default certificate and browsers warn.");
P("DownTube answers plain HTTP with a permanent redirect (308) to HTTPS, and DSM keeps answering the Let's Encrypt challenges on port 80.");

H2("4.9 Make the name work inside your home network");
P("Many ISP routers cannot reach their own public IP from inside the network (no **NAT loopback**). Devices at home then time out on `downtube.example.com` even though it works from a phone on mobile data.");
NUM([
  "Add a **local DNS record** on your LAN resolver (Pi-hole: Local DNS > DNS Records, or `misc.dnsmasq_lines` in Pi-hole v6; or DSM DNS Server) pointing `downtube.example.com` to the NAS LAN address.",
  "Make every device use **only** that resolver: in your router's DHCP settings, hand out just the local resolver. If DHCP also hands out the router, the ISP or 1.1.1.1, devices switch between them and the name works only sometimes.",
  "Reconnect a device and test: `nslookup downtube.example.com` should return the NAS LAN address.",
]);

H2("4.10 First-run setup and sign-in");
IMAGE("diagrams/first-run.png", "First run: from container start to the dashboard.");
NUM([
  "Open **https://downtube.example.com/setup**.",
  "Enter the **setup code** printed by the deploy script. To see it again: `ssh admin@nas.example.lan 'docker logs downtube 2>&1 | grep \"setup code\" | tail -1'`. A new code is printed at every start until setup is complete; 20 wrong attempts disable it until the next restart.",
  "Check that the **Authorized redirect URI** shown on the page is exactly the one registered in Google (step 4.2).",
  "Paste the **Client ID** and **Client secret**.",
  "Add your Google account under **Allowed accounts** (press Enter) and tick it as **Administrator**. Add family members if you like; they get their own likes and rules.",
  "Select **Complete setup**, then **Sign in with Google**.",
  "Google warns that it has not verified the app: choose **Advanced > Go to DownTube (unsafe)**, then allow read-only YouTube access.",
  "Read and accept DownTube's rights acknowledgement. You land on the dashboard.",
]);
IMAGE("screenshots/setup.png", "The setup page (demo values).", 520, 700);

H2("4.11 Choose what to archive");
NUM([
  "**Subscriptions > Refresh subscriptions.** Your channels appear within seconds (about one quota unit per 50 channels).",
  "**Settings > Likes monitoring:** keep _Future likes only_ to start clean, or choose _All_ to archive existing likes too. Choose the quality for liked videos.",
  "**Liked videos > Rescan** records the baseline (in future-only mode nothing is queued).",
  "On **Subscriptions**, give the channels you care about a mode and a maximum resolution. Start with a few channels; backfills of large channels can be thousands of videos.",
  "**Settings > Downloads:** set concurrency (1 or 2 on a NAS), and optionally a bandwidth limit and a night-time download window.",
  "Watch the first downloads on **Downloads** and check the result on **Library**.",
]);

H2("4.12 Verify the deployment");
TABLE(
  ["Check", "Expected"],
  [
    ["`https://downtube.example.com` from a phone on mobile data", "Sign-in page with a valid padlock"],
    ["`http://downtube.example.com`", "Redirect (308) to https"],
    ["`curl -sI https://downtube.example.com/api/health`", "200 and a `Strict-Transport-Security` header"],
    ["`https://downtube.example.com/api/docs` while signed out", "401"],
    ["`http://<NAS LAN IP>:8092` from another device", "Connection refused (bound to 127.0.0.1)"],
    ["A Google account that is not on the allowlist", "\"not on the allowed list\""],
    ["`docker ps` on the NAS", "`downtube` healthy, roughly 80 MB of memory when idle"],
  ],
  [4800, 4200],
);
NOTE("To test from outside without a phone, use a multi-location checker (for example check-host.net). Some single online fetch services are blocked by home connections and report \"connection refused\" even when the site works.", "tip");

H2("4.13 Plex or Jellyfin (optional)");
P("Choose the library type by how you want descriptions shown. Plex reads `.nfo` files only with its NFO agent, which works in **Movie** and **TV** libraries (Plex Media Server 1.43.1 or later). An **Other Videos** library uses the tags embedded in the MP4 instead, which only files downloaded with DownTube 0.3.0 or later have. Jellyfin, Emby and Kodi read the `.nfo` files in any library.");
NUM([
  "In Plex, **Add Library**, type **Movies** (with the Plex NFO agent) or **Other Videos**, folder `/volume1/video/DownTube`.",
  "Under **Advanced**, enable automatic scanning and partial scans on change.",
  "For videos archived before 0.3.0, run **Settings > Media server files > Write .nfo files for videos already archived** once, then refresh the Plex library metadata.",
  "Some clients cannot play 4K VP9 or AV1 directly and Plex transcodes, which is slow on a NAS CPU. Settings > Quality & formats > _Compatibility profile_ prefers H.264/AAC at the same resolution (YouTube offers H.264 only up to 1080p).",
]);

// ---- 5. Operations
H1("5. Running DownTube");
H2("Upgrading");
NUM([
  "Read the CHANGELOG for the new version.",
  "Change the image tag in your compose file (for example `:0.2.1` to `:0.3.0`).",
  "Run the deploy script again. It keeps `.env` and the database; migrations run automatically.",
]);
P("Downloads that are running during the restart are put back in the queue and resume from their partial files; the restart does not count as a failed attempt. This was verified during the 0.2.0 to 0.2.1 upgrade: two running jobs stopped, restarted 8 seconds later and completed normally.");
P("Upgrading to 0.3.0: optionally add the `youtube.force-ssl` scope in Google Cloud (step 4.2), grant the permission in Settings if you want to unsubscribe and remove likes from DownTube, and write `.nfo` files for videos archived earlier from Settings > Media server files.");
P("Update regularly even without a DownTube release: every image ships a current yt-dlp, and yt-dlp must keep up with changes on YouTube.");
H2("Backups");
BUL([
  "DownTube copies its database every day into `/data/backups` and keeps seven copies.",
  "Back up `/volume1/docker/downtube` (database, backups and `.env`) with Hyper Backup or similar. Without the same `DOWNTUBE_SECRET_KEY` the stored tokens cannot be decrypted; you would only need to sign in again.",
  "The archive itself is ordinary files; back it up like any other media.",
]);
H2("Logs and health");
TABLE(
  ["What", "Where"],
  [
    ["Health", "`/api/health`: status, version, database, worker, scheduler, archive writable"],
    ["Container log", "`docker logs --tail 100 downtube`"],
    ["Application log", "`/volume1/docker/downtube/logs/downtube.log` (10 MB × 5 files); secrets are redacted"],
    ["In the app", "Settings > Logs (administrators)"],
  ],
  [2400, 6600],
);
H2("Troubleshooting");
TABLE(
  ["Symptom", "Cause and fix"],
  [
    ["Sign-in stops working after a week", "The Google app is in Testing. Publish it (In production) and sign in again."],
    ["`redirect_uri_mismatch` at sign-in", "The redirect URI in Google differs from `DOWNTUBE_BASE_URL` + `/api/auth/callback`. They must match exactly. New clients can take a few minutes to work."],
    ["Google rejects the branding page", "A link's domain is not an authorised domain. Add it (for example `github.com`)."],
    ["Works from mobile data, not from home", "No NAT loopback. Add the local DNS record and hand out only that DNS server by DHCP (step 4.9)."],
    ["Browser shows a certificate warning", "Assign the downtube certificate in Certificate > Settings."],
    ["Downloads wait with `disk_space`", "Free space is below the configured floor. Free space or lower the floor in Settings > Disk safety."],
    ["Many downloads fail with network errors", "Usually temporary throttling; jobs retry with backoff. If it persists, upgrade to get a newer yt-dlp."],
    ["Quota exhausted", "Syncs pause until midnight Pacific Time. Reduce backfills or raise the budget if your Google project allows."],
    ["SSH tunnel to port 8092 does nothing", "DSM forbids SSH port forwarding. Use the reverse proxy."],
  ],
  [3200, 5800],
);

// ---- 6. Development
H1("6. For developers");
H2("Repository layout");
TABLE(
  ["Path", "Contents"],
  [
    ["`backend/downtube/`", "FastAPI app: `api/`, `auth/`, `sync/`, `downloads/` (policy, adapter, validator, manager, jobs), `storage/`, `security/`, models and Alembic migrations"],
    ["`backend/tests/`", "pytest suite (180+ tests), fake YouTube API with respx, ffmpeg-based media tests"],
    ["`frontend/`", "React + TypeScript + Vite + Tailwind; vitest tests"],
    ["`deploy/deploy_nas.sh`", "SSH deployment script for Synology and Linux Docker hosts"],
    ["`docs/`", "Installation, Synology, operations, quality policy, contracts (`architecture/CONTRACTS.md`), this guide and its images"],
    ["`tools/`", "Generators for this guide, the diagrams and the screenshots (demo data)"],
  ],
  [2600, 6400],
);
H2("Quality gates");
P("Every pull request runs ruff (lint and format), mypy, pytest, the frontend lint/typecheck/tests/build, pip-audit, npm audit, gitleaks, and a Docker build that boots the image and checks `/api/health`. Pushing a `v*` tag builds and publishes the multi-stage image to GitHub Container Registry.");
H2("Release process");
NUM([
  "Merge the change through a pull request with green CI.",
  "Bump the version in `backend/pyproject.toml`, `backend/downtube/__init__.py` and `frontend/package.json`, and add a CHANGELOG entry.",
  "Tag `vX.Y.Z` and push the tag; the release workflow publishes `ghcr.io/jasoneameslamarca/downtube:X.Y.Z`.",
  "Deploy by bumping the tag in the NAS compose file and running the deploy script.",
]);

// ---- Appendix
H1("Appendix A. Environment variables");
TABLE(
  ["Variable", "Default", "Purpose"],
  [
    ["`DOWNTUBE_SECRET_KEY`", "required", "At least 32 random characters; derives the encryption and session keys. Keep it with your backups."],
    ["`DOWNTUBE_BASE_URL`", "`http://localhost:8092`", "Public origin; the OAuth redirect is this plus `/api/auth/callback`."],
    ["`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`", "empty", "Optional. If set, they override the setup page and are read-only in the UI."],
    ["`DOWNTUBE_ALLOWED_EMAILS`, `DOWNTUBE_ADMIN_EMAILS`", "empty", "Optional comma-separated lists; if set, they override the setup page."],
    ["`DOWNTUBE_TRUSTED_PROXIES`", "`127.0.0.1/32`", "Proxies whose forwarded headers are trusted. Behind DSM: `172.16.0.0/12`."],
    ["`DOWNTUBE_BIND`, `DOWNTUBE_PORT`", "`0.0.0.0`, `8092`", "Where compose publishes the port. Use `127.0.0.1` behind a reverse proxy."],
    ["`DOWNTUBE_STORAGE_MOUNTS`, `DOWNTUBE_ARCHIVE_ROOT`", "`/archive`", "Folders DownTube may write to, and the initial archive folder."],
    ["`DOWNTUBE_HOST_ARCHIVE_PATH`", "empty", "Host path shown in the UI, for example `/volume1/video/DownTube`."],
    ["`DOWNTUBE_LOG_LEVEL`", "`INFO`", "Log verbosity."],
    ["`TZ`", "`UTC`", "Time zone for the download window and logs."],
  ],
  [3400, 1900, 3700],
);
H1("Appendix B. Glossary", false);
TABLE(
  ["Term", "Meaning"],
  [
    ["Backfill", "Archiving a channel's existing uploads, not only new ones."],
    ["Baseline", "In future-only likes mode, the likes that existed when monitoring started; they are recorded but not downloaded."],
    ["Stream copy / remux", "Putting the original audio and video streams into a new container (MP4) without decoding them, so quality is unchanged."],
    ["Superseded", "An older, lower-resolution file replaced by an upgrade; kept as `*.superseded.mp4` by default."],
    ["Quota unit", "Google's measure of YouTube Data API usage; 10,000 per project per day by default."],
    ["NAT loopback (hairpin)", "A router's ability to let LAN devices reach the network's own public IP. Without it, use split DNS."],
    ["PKCE", "Proof Key for Code Exchange, an OAuth protection that binds the sign-in response to the browser session that started it."],
  ],
  [2600, 6400],
);

// ================================================================== DOCUMENT
const doc = new Document({
  creator: "DownTube project",
  title: "DownTube — end-to-end guide",
  description: "What DownTube is, how it works, and how to deploy and run it.",
  features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 22, color: INK }, paragraph: { spacing: { line: 288 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 36, bold: true, color: RED }, paragraph: { spacing: { before: 240, after: 200 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 28, bold: true, color: INK }, paragraph: { spacing: { before: 300, after: 120 }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 24, bold: true, color: MUTED }, paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 2 } },
    ],
  },
  numbering: { config: numberingConfigs },
  sections: [
    {
      properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } }, titlePage: true },
      headers: {
        default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: `DownTube ${VERSION} — end-to-end guide`, size: 16, color: MUTED })] })] }),
      },
      footers: {
        default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 16, color: MUTED })] })] }),
      },
      children: body,
    },
  ],
});

const out = path.join(REPO, "docs", "DownTube-Guide.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(out, buf);
  console.log(`wrote ${out} (${(buf.length / 1e6).toFixed(1)} MB, ${figure} figures)`);
});
