# Video quality and format policy

DownTube archives the **best original stream** it can, up to a maximum you choose. This document
describes the rules exactly as the code implements them. The rules are absolute: changes that
break them are not accepted (see [CONTRIBUTING.md](../CONTRIBUTING.md#quality-policy-invariants)).

Source of truth: `backend/downtube/downloads/policy.py` (selection), `validator.py` (checks),
`jobs.py` (cap and upgrade logic). Contract summary: [architecture/CONTRACTS.md](architecture/CONTRACTS.md) §5.

## 1. The resolution cap

```
cap = min(requested or 2160, 2160)
```

- `requested` is the per-rule maximum, or the likes maximum for a liked video, or the default maximum.
  Unset means "best available".
- The cap can never exceed 2160. An 8K source is never selected.
- Resolution is measured on the **short side**: `short_side = min(width, height)`.
  A portrait 1080 × 1920 video counts as **1080p**. A 2160 × 3840 portrait counts as 2160p.

| Setting | Effective cap |
|---|---|
| Unset (best available) | 2160 |
| 1080 | 1080 |
| 4320 requested (not a valid setting; the maximum is 2160) | 2160 |

## 2. Selection order

Streams are considered only if they are:

- not DRM-protected;
- not storyboards, MHTML or other non-media formats;
- a video stream with known dimensions, or an audio-only stream for the audio track.

The algorithm then runs in this order:

1. **Limit.** Keep video streams with `short_side ≤ cap`.
   If none exist, the result is `Unsupported` with code `no_stream_within_limit`.
2. **Best resolution.** Take the highest `short_side` among those streams. This is never traded for codec.
3. **Tier ordering.** Sort the streams at that resolution, best first, by:
   1. HDR match: a stream matching `prefer_hdr` (default: SDR) comes first;
   2. higher frame rate (for example 60 fps beats 30 fps at the same resolution);
   3. video-only streams before combined streams (separate DASH video is usually higher quality);
   4. codec rank (see §3);
   5. higher bitrate.
4. **MP4-compatible combination.** Walk the sorted tier and take the first stream that
   can be placed in MP4 by stream copy:
   - a combined stream whose audio is MP4-compatible; or
   - a video-only stream plus the best MP4-compatible audio track (see §4).
   - If the video has no audio track anywhere on the video, the result is a silent MP4.
5. **Fallback.** If nothing at that resolution works:
   - with `mkv_fallback` on, store the best stream as MKV;
   - otherwise return `Unsupported` with code `no_mp4_combination`. DownTube does not drop the resolution silently.

## 3. Codec preferences

Codec only breaks ties **within one resolution**.

| Family | Codec IDs | MP4-compatible |
|---|---|---|
| H.264 | `avc1`, `h264` | Yes |
| HEVC | `hev1`, `hvc1`, `hevc` | Yes |
| VP9 | `vp09`, `vp9` | Yes |
| AV1 | `av01`, `av1` | Yes |
| VP8 | `vp8` | No (not in MP4 allow-list) |

Rank order:

| Profile | Order (best first) |
|---|---|
| Default (quality) | VP9 > AV1 > H.264 > HEVC |
| Compatibility profile on | H.264 > VP9 > AV1 > HEVC |

YouTube offers H.264 only up to 1080p. Above that, a stream is VP9 or AV1, whatever the
profile says. The compatibility profile therefore never lowers the resolution to get H.264:
a 2160p source is still archived as VP9 or AV1 under that profile.

## 4. Audio

Audio is selected only when the video stream has no audio of its own. Within the audio candidates:

1. **Original language first.** A track whose format note says "original" or whose language
   preference is at least 10 comes first.
2. Higher `language_preference`.
3. Codec match with `audio_preference`:
   - `aac` (default) prefers `mp4a`;
   - `opus` prefers `opus`;
   - the other MP4-compatible codec is the fallback (for example Opus when AAC is absent).
4. Higher bitrate (`abr`, then `tbr`).

MP4 audio allow-list: `mp4a`/AAC, Opus, AC-3, E-AC-3, FLAC, MP3.

## 5. MP4 compatibility

- **MP4 is the default container.** Streams are muxed with ffmpeg stream copy (`-c copy`).
  Nothing is re-encoded.
- **Opus in MP4** is supported by current ffmpeg builds. It is still less widely decoded than AAC
  by media clients (see §9).
- **VP9 and AV1 in MP4** are stored as-is. Some clients must transcode them.
- **MKV fallback** (`mkv_fallback`, default off) is used only when no MP4-compatible combination
  exists at the best resolution. It is recorded as container `mkv`. It is never used to reach a higher resolution.

## 6. Compatibility profile

`compatibility_profile` (default off) changes two things only:

1. Codec ranking within a resolution: H.264 first (see §3).
2. Nothing else. It does not lower resolution, change the container, or change audio rules.

Use it if your media clients struggle with VP9 or AV1 and your library is mostly 1080p or below.
For 4K sources, DownTube still archives VP9 or AV1.

## 7. Validation

A file is placed in the archive only if ffprobe confirms every check below. Any failure marks the
job `validation_failed`. That error is retried once, then the job fails.

| Check | Rule |
|---|---|
| Video stream | Must exist and have dimensions |
| Container | MP4 family names for `mp4`; Matroska or WebM for `mkv` |
| Resolution cap | Output short side ≤ effective cap |
| No upscale | Output short side ≤ selected source short side |
| Audio | Present when an audio stream was selected |
| Duration | Within the larger of 2 seconds or 2 % of the expected duration |
| File | Exists and is non-empty |

## 8. Upgrades and superseded copies

A video is upgraded only when all of these are true:

- the effective cap is higher than the cap the current file was downloaded with; and
- the current file is shorter than `min(best available, effective cap)`.

So raising the default maximum, or liking a video whose like-maximum is higher than the rule
that archived it, can queue an `upgrade` job. Lowering a cap never removes or downgrades files.

The replacement follows these steps:

1. Download and validate the new file in temp storage.
2. Place the new file in the archive atomically (`*.downtube-partial`, then rename) and mark it as the current file in the database.
3. Handle the old file according to `keep_superseded_copies`:
   - **on (default):** rename it to `<name>.superseded.<ext>`;
   - **off:** delete it.

The old file is never removed before the new one is in place. Unknown files at a target path are never overwritten;
DownTube adds ` (2)`, ` (3)` and so on to the new name instead.

## 9. Plex and client playback

Plex direct play depends on the client's decoders, not on DownTube.

| Stream | Typical direct-play support |
|---|---|
| H.264 + AAC in MP4 | Almost everywhere. The safest choice. |
| VP9 in MP4 | Most modern clients and Plex Web. Some older devices transcode. |
| AV1 in MP4 | Depends on hardware decode. Many clients transcode. |
| Opus audio in MP4 | Inconsistent. Some clients transcode audio only. |

Transcoding on a NAS is slow. Plan for it if you archive 4K.

## 10. Mandatory resolution examples

These examples are checked by automated tests. The "Selected" column is the short-side height of the chosen stream.

| Source offers (short side) | Limit | Selected | Note |
|---|---|---|---|
| 480p | 2160 | 480p | Never upscaled |
| 720p | 2160 | 720p | |
| 1080p | 2160 | 1080p | |
| 2160p | 2160 | 2160p | Only VP9 or AV1 exists at this height |
| 2160p | 720 | 720p | Limit applies |
| 4320p (8K) | 2160 | 2160p | 8K never selected |
| 1440p | 2160 | 1440p | |
| 1440p | 1080 | 1080p | Limit applies |
| 360p | 1080 | 360p | Never upscaled |
| 2160p | best available | 2160p | Still hard-capped at 4K |
| 4320p (8K) | best available | 2160p | Still hard-capped at 4K |

Portrait example: a 1080 × 1920 video under a 1080 limit is selected at its full size, 1080 × 1920.
A 2160 × 3840 portrait under a 1080 limit is not selected at 2160; it falls to the best stream with a
short side of 1080 or less.

## 11. Worked example: a 4K-capable video

Source offers 2160p VP9 (video-only, 60 fps), 2160p AV1, 1080p H.264 and 1080p AAC audio. Rule: best available,
compatibility profile off.

1. Cap = 2160.
2. Best resolution = 2160p.
3. Tier ordering: 60 fps VP9 is first (higher fps before codec rank).
4. VP9 is MP4-compatible and video-only, so the best audio track is chosen (original language, then AAC).
5. Result: MP4 with 2160p VP9 video and AAC audio, stream copy.

The same video with the compatibility profile on still gets 2160p VP9, because H.264 stops at 1080p.
