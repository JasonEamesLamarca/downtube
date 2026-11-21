# DownTube Phase-1 Feasibility Research

Date: 2026-11-21. Method: official docs / yt-dlp GitHub fetched today. "UNVERIFIED" = not confirmed from a source I could read.

## A. YouTube Data API v3

### 1. Liked videos
- Fact: `videos.list?myRating=like` needs an authorized request; docs do not name the scope (samples use youtube.readonly; scope-per-method not stated on page). maxResults 1-50 (default 5), works with myRating. "The API limits the returned result set to a maximum of 1000 videos"; pageToken only reaches the most recent 1000 even if totalResults is larger. Cost 1 unit/call.
- Source: https://developers.google.com/youtube/v3/docs/videos/list
- Implication: myRating cannot enumerate a full like history; only newest 1000. Poll often and persist incrementally so older likes are captured before they fall off.
- Fact: `channels.list` still documents `contentDetails.relatedPlaylists.likes` (liked-videos playlist, "LL"). favorites is deprecated/may be empty. watchHistory/watchLater return 403 (`watchHistoryNotAccessible`, `watchLaterNotAccessible`) from playlistItems.list.
- Source: https://developers.google.com/youtube/v3/docs/channels ; https://developers.google.com/youtube/v3/docs/playlistItems/list
- Fact: whether LL via playlistItems.list is uncapped, and whether `snippet.publishedAt` equals the like time (vs video publish time): UNVERIFIED. Docs do not say; searches found nothing. playlistItems.list docs list no size cap, but liked-list behaviour is untested.
- Implication: do NOT rely on a like timestamp from the API. Record `first_seen_at` locally when a new like appears and treat it as approximate. Phase-2 spike: call LL with a real account, compare item count to the YouTube UI, inspect publishedAt vs video publish date.

### 2. Subscriptions and channels
- Fact: `subscriptions.list` mine=true: 1 unit, maxResults 0-50 (default 5), pageToken/nextPageToken, order alphabetical|relevance|unread, parts contentDetails, id, snippet, subscriberSnippet. Only one filter per request. Scope not named on page (youtube.readonly used in samples).
- Source: https://developers.google.com/youtube/v3/docs/subscriptions/list
- Fact: `contentDetails.totalItemCount`/`newItemCount` in the contentDetails part: UNVERIFIED on this page (fetched summary omitted the field list). snippet carries title/description/thumbnails/resourceId.channelId (from knowledge, UNVERIFIED today).
- Fact: `channels.list` `statistics.videoCount` = PUBLIC videos only, even for owners; `contentDetails.relatedPlaylists.uploads` gives the uploads playlist id.
- Source: https://developers.google.com/youtube/v3/docs/channels
- Implication: batch channels.list (id=comma list of up to 50, UNVERIFIED limit but standard) to get uploads playlist ids and counts. Uploads id equals channel id with "UC" replaced by "UU" (community-known; still prefer reading it from the API).

### 3. Uploads playlist and UULF/UUSH/UULV
- Fact: playlistItems.list: 1 unit, maxResults 0-50 (default 5), pageToken pagination, no stated cap on playlist size. Errors: 403 playlistItemsNotAccessible, 404 playlistNotFound.
- Source: https://developers.google.com/youtube/v3/docs/playlistItems/list
- Fact: UULF (long-form), UULV (live), UUSH (shorts) prefixes are UNDOCUMENTED by Google. A Qiita write-up reports they worked as of end of March 2026; nothing later found. UC->UU swap is the standard documented route.
- Source: https://qiita.com/Shota-Sakitsu/items/5171b9ca6fe52875ef7a (community); no official source.
- Implication: use UU as the baseline; treat UULF/UUSH/UULV as an optional optimization with runtime fallback (empty/404 -> UU + filter by duration from videos.list). Do not make shorts filtering depend on them. Cost for a full sync = ceil(N/50) units per channel.
- Caveat (UNVERIFIED today, long-standing community knowledge): UU playlist may omit some very old or unlisted items; verify against videoCount.

### 4. videos.list and private/deleted items
- Fact: parts include contentDetails (duration, ISO 8601), liveStreamingDetails, status, snippet, statistics, etc. 1 unit/call. The doc page gives no max for `id` count; 50 per call is the standard batch (UNVERIFIED on page; playlistItems and myRating cap at 50).
- Source: https://developers.google.com/youtube/v3/docs/videos/list
- Fact: playlistItems "Private video"/"Deleted video" titles and status.privacyStatus values: the playlistItems page does not address them (UNVERIFIED; widely observed behaviour). Deleted/private ids are simply absent from a following videos.list response.
- Implication: after each playlistItems page, call videos.list with the ids (part=snippet,contentDetails,status,liveStreamingDetails); any id missing from the response = unavailable. Detect placeholders by title and by absence rather than by title alone.

### 5. Quota
- Fact: default 10,000 units/day for all endpoints except search.list and videos.insert (separate 100/day buckets per the page). Daily reset midnight Pacific Time. Invalid requests still cost >=1 unit. list calls cost 1 each.
- Source: https://developers.google.com/youtube/v3/determine_quota_cost
- Fact: quota exceeded = HTTP 403, reason `quotaExceeded`.
- Source: https://developers.google.com/youtube/v3/docs/errors
- Implication: a 500-channel sync is about 500-2000 units; budget fine. Track units locally, stop on `quotaExceeded`, resume after 00:00 PT. Never use search.list.

## B. Google OAuth

### 6. OAuth facts
- Fact: Web server flow docs describe client_secret + `state`; PKCE is NOT mentioned on the web-server page (PKCE is documented for installed/desktop apps, S256 recommended). Whether Google accepts code_challenge for a confidential Web client: UNVERIFIED (a forum thread reports client_secret is still required, which is fine for a confidential client).
- Source: https://developers.google.com/identity/protocols/oauth2/web-server ; https://developers.google.com/youtube/data/guides/auth/installed-apps
- Implication: use standard code flow with client_secret + state (+ add PKCE S256 as harmless extra only if it is accepted in a spike test).
- Fact: sensitive vs restricted classification of youtube.readonly: the scopes page lists youtube.readonly ("View your YouTube account") with no label; UNVERIFIED from docs. Cloud Console Data Access page shows the category automatically. My expectation (not verified): sensitive, not restricted (restricted needs annual third-party security assessment).
- Source: https://developers.google.com/identity/protocols/oauth2/scopes ; https://support.google.com/cloud/answer/13464321
- Fact: Testing status: up to 100 listed test users; authorizations (including offline refresh tokens) "will expire seven days from the time of consent." Still true. Exception only for name/email/profile scopes.
- Source: https://support.google.com/cloud/answer/15549945
- Fact: In production without verification: "Unverified app" warning screen when sensitive/restricted scopes are requested; 100 new users total over project lifetime, cannot be reset.
- Source: same.
- Implication: Single-user self-hosted app: publish the project to "In production" and live with the unverified warning (click Advanced > continue). Refresh tokens then do not have the 7-day expiry (page states no production expiry; still handle `invalid_grant`). Testing mode would force re-consent weekly - not viable. Verification not needed for personal use if under 100 users (cap applies).
- Fact (exact redirect URI rules, current): HTTPS required, except localhost (incl. localhost IP URIs); hosts cannot be raw IP addresses (except localhost IPs); host TLD must be on the public suffix list; no userinfo, fragments, wildcards, path traversal, open redirects; exact match required including trailing slash. Docs do not mention private IPs explicitly, but the raw-IP rule excludes http://192.168.x.x:port.
- Source: https://developers.google.com/identity/protocols/oauth2/web-server#uri-validation
- Implication / workable LAN options:
  1. http://localhost:PORT/callback with an SSH tunnel (`ssh -L PORT:nas:PORT`) from the browser machine: allowed, no domain needed. Fine for one-time consent; the app can refresh tokens server-side afterwards, so tunnel only needed at (re)authorisation.
  2. A real hostname on a public-suffix TLD (e.g. nas.example.com resolving to the LAN IP via local DNS) with HTTPS via reverse proxy (Let's Encrypt DNS-01 challenge works without exposing the NAS). Synology DSM reverse proxy supports this.
  3. Tailscale MagicDNS `host.tailnet.ts.net` with `tailscale cert` HTTPS: satisfies https + a public-suffix-list domain (ts.net PSL membership and Google acceptance: UNVERIFIED; test in Cloud Console).
  4. Do not rely on `.local`, `.lan`, `.home.arpa`, or bare hostnames (not in PSL).
  Recommended: option 1 for MVP (zero infra), option 2 or 3 for convenient UX.

## C. Download tooling

### 7. yt-dlp
- Fact: YouTube downloads require an external JS runtime to solve JS challenges (EJS, replaced the old JSInterp/PhantomJS). Deno recommended, enabled by default, min 2.3.0. Node min 22.0.0 (enable `--js-runtimes node`), QuickJS, Bun (deprecated). Pip/pipx installs need yt-dlp-ejs: `pip install -U "yt-dlp[default]"`; `--remote-components ejs:npm|ejs:github` can fetch it (default: no remote components). README: "If no JavaScript runtime/engine is available, then `web` and `tv_samsung` [clients] are omitted."
- Source: https://github.com/yt-dlp/yt-dlp/wiki/EJS ; https://github.com/yt-dlp/yt-dlp/blob/master/README.md
- Implication: Docker image must include deno (single binary from deno.land / `denoland/deno` copy) plus `yt-dlp[default]`, ffmpeg; Python >= 3.11. Pin/auto-update yt-dlp (nightly via `--pre`) since YouTube breaks extractors often.
- Fact: default player_client is `tv_samsung,visionos,web`; with logged-in cookies `tv_samsung,web_embedded,web` (free) / `web_creator,tv_samsung,web` (premium). Some clients (web_creator, web_music) require a po_token. PO token requirements per client: web needs GVS (and subs); mweb GVS; tv none (but DRM'd without cookies in some cases); android_vr none; Premium accounts need no GVS token. PO Token wiki recommends a PO-token provider plugin with mweb, and says manual extraction is discouraged (tokens bound to video ids). Enforcement is rolling out.
- Source: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide ; README (extractor-args youtube)
- Implication: start with defaults; design config to pass `extractor_args` and cookies file; allow a PO-token provider plugin later. Expect some videos/formats to fail intermittently: retry with alternate clients.
- Fact: format syntax. `-S` sort fields include res, height, fps, vcodec/codec, acodec, ext, br; `res:720` = prefer largest not above 720. Example in README: `-t mp4` alias = `--merge-output-format mp4 --remux-video mp4 -S vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac`. Codec preference order for vcodec is h264 > h265 > vp9 > vp9.2 > av01 > vp8...
- Source: README sections "Sorting Formats", "Format Selection".
- Suggested strings: height cap + mp4-friendly: `-f "bv*[height<=1080]+ba/b[height<=1080]" -S "res:1080,vcodec:h264,acodec:aac"` (note: max H.264 on YouTube is 1080p; 4K is VP9/AV1 only). Best quality regardless of codec: `-S "res:2160,vcodec:av01,acodec:opus"` (or just `res:2160`). `-f` with `height<=N` filter plus `-S` as tie-breaker is the safest combo.
- Fact: `--merge-output-format mp4` is supported; `--remux-video` "If the target container does not support the video/audio codec, remuxing will fail." Merge uses ffmpeg stream copy (README: ffmpeg needed for merging). Whether yt-dlp's mp4 merge with VP9/AV1 + Opus succeeds without re-encoding: see finding 8; verify with a spike.
- Source: README
- Fact: embedding is documented and supported: `yt_dlp.YoutubeDL(opts)` with `progress_hooks`, `logger`; README says programs should avoid parsing stdout and use `-J`/`--print`/`--progress-template` for CLI use.
- Source: https://github.com/yt-dlp/yt-dlp/blob/master/README.md#embedding-yt-dlp
- Implication: use the Python API in a worker thread/process (not the event loop); progress_hooks feed job status; extract_info(download=False) for metadata. Subprocess is more robust to crashes/hangs and eases updating; either works. Recommend a worker process per job.

### 8. MP4 containers via ffmpeg
- Fact: Opus-in-MP4 needed `-strict experimental` in old ffmpeg; the gate was removed by the 2020 commit "movenc: mark Opus encapsulation as stable".
- Source: https://lists.mplayerhq.hu/pipermail/ffmpeg-cvslog/2020-March/121363.html ; https://lists.ffmpeg.org/pipermail/ffmpeg-user/2018-February/038999.html
- Fact: VP9 and AV1 in MP4 stream copy: UNVERIFIED from a fetched source (ffmpeg wiki was blocked). From knowledge: ffmpeg's mov muxer supports VP9 (vp09) and AV1 (av01); Opus (Opus box) supported.
- Implication: use any ffmpeg built after 2020 (Debian bookworm 5.1 / Ubuntu 22.04+ fine). Spike test: download a VP9+Opus and AV1+Opus video with `--merge-output-format mp4` and `ffprobe`. Fallback is MKV (`--merge-output-format mkv`), which is universally safe.

### 9. Plex direct play (summary level)
- Fact: could not fetch Plex support page (fetch failed): UNVERIFIED. From general knowledge: H.264 + AAC in MP4 is the safest direct play everywhere; VP9/AV1 and Opus-in-MP4 depend on the client hardware/decoder (newer Apple TV/Roku/Android TV/Chromecast and Plex Web tend to handle VP9; AV1 hardware decode is limited; Opus in MP4 has patchy support), and failing clients trigger transcoding (and the Synology NAS CPU is weak).
- Source: https://support.plex.tv/articles/203810286-what-media-formats-are-supported/ (not read)
- Implication: offer a per-subscription/global "compat profile": default H.264+AAC MP4 capped at 1080p; optional "max quality" profile (VP9/AV1+Opus in MKV or MP4).

## D. Policy

### 10. ToS and API policies
- Fact: Developer Policies: "must not download, import, back up, cache, or store copies of YouTube audiovisual content without YouTube's prior written approval"; must not make content available for offline playback; must not separate audio/video or scrape.
- Source: https://developers.google.com/youtube/terms/developer-policies
- Fact: Data retention: non-authorized API data max 30 calendar days then delete/refresh; most authorized data (e.g. user's likes/subscriptions) max 30 days unless refreshed; statistics need reconfirmed authorization every 30 days; delete on user request within 7 days; in-app revoke must revoke token immediately and delete authorized data within 7 days; revocation via Google settings -> delete within 30 days; check tokens periodically and delete for users whose tokens can't refresh.
- Source: same.
- Fact: YouTube Terms of Service: bars accessing/downloading Content except as permitted by the Service, with written permission, or as permitted by law; bars automated access (bots/scrapers); allows personal non-commercial viewing. (Page fetched in Spanish.)
- Source: https://www.youtube.com/t/terms
- Fact: API ToS: no licence to reproduce/distribute audiovisual content (16.3); do not exceed/circumvent quotas (s15); YouTube may audit (s6).
- Source: https://developers.google.com/youtube/terms/api-services-terms-of-service
- Implication: downloading with yt-dlp is not an "API Services" feature and is contrary to YouTube's ToS; use the API only for discovery (ids, metadata), and yt-dlp separately. Keep API-derived metadata fresh (re-sync at least every 30 days) or purge it; implement "disconnect Google account" that revokes the token and deletes API data within 7 days. This is a personal-use, no-redistribution tool; legal exposure is the user's own call (jurisdiction-dependent; not legal advice).

## Blockers & recommended alternatives

1. Redirect URI (HIGH). `http://<LAN-IP>:port` is rejected (raw IPs, non-HTTPS). Use `http://localhost` via SSH tunnel for consent (MVP), or HTTPS on a real-domain/ts.net hostname.
2. Testing mode 7-day refresh expiry (HIGH). Publish to "In production" (unverified warning, 100-user lifetime cap; fine for 1 user). Handle `invalid_grant` with a re-auth banner.
3. yt-dlp JS runtime (HIGH). Deno (>=2.3.0) + yt-dlp-ejs are required; without a runtime default `web` and `tv_samsung` clients are dropped. Bake into Docker; auto-update yt-dlp; expect PO-token-related breakage; allow cookies and extractor-args config.
4. Likes API is capped at 1000 newest via myRating and like timestamp is unverified (MEDIUM). Poll regularly, store `first_seen_at`; validate LL playlist behaviour in a spike.
5. API data retention 30-day rule (MEDIUM). Build refresh/purge job, revoke+delete flow.
6. ToS conflict on downloading (MEDIUM, personal-use risk). Keep private, no sharing, no API-based download.
7. Codec/Plex compatibility (MEDIUM). Default to H.264+AAC <=1080p MP4; VP9/AV1 optional; verify VP9/AV1/Opus-in-MP4 mux with ffprobe in a spike.
8. Undocumented UULF/UUSH/UULV (LOW). Optional optimization only; fallback to UU + duration filter.
9. Quota (LOW). 10,000 units/day suffice; persist quota counter; handle 403 quotaExceeded.

Open items for Phase-2 spikes: PKCE acceptance, youtube.readonly category in Cloud Console, LL playlist size/publishedAt, subscriptions contentDetails fields, Plex client matrix, VP9/AV1 MP4 mux.
