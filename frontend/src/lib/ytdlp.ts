/**
 * The yt-dlp command a user runs on their own computer for videos DownTube cannot fetch
 * (age-restricted, members-only…). It reads the user's browser cookies locally — DownTube itself
 * never receives them — and mirrors DownTube's archive policy: best original quality up to the
 * cap (shortest side), MP4 by stream copy, AAC audio preferred, metadata embedded, and the video
 * ID in brackets so the import folder can match the file.
 */
export const BROWSERS = ['chrome', 'firefox', 'edge', 'brave', 'safari', 'chromium', 'opera', 'vivaldi'] as const;
export type Browser = (typeof BROWSERS)[number];

export const youtubeUrl = (videoId: string) => `https://www.youtube.com/watch?v=${videoId}`;

export function ytDlpCommand(videoId: string, cap: number, browser: Browser): string {
  return [
    'yt-dlp',
    `--cookies-from-browser ${browser}`,
    `-S "res:${cap}"`,
    '-f "bv*+ba[acodec^=mp4a]/bv*+ba/b"',
    '--merge-output-format mp4',
    '--embed-metadata --no-embed-chapters --no-embed-info-json',
    '-o "%(title).150B [%(id)s].%(ext)s"',
    `"${youtubeUrl(videoId)}"`,
  ].join(' ');
}

/** Errors the user can work around by downloading the video with their own account. */
export const SIGN_IN_ERRORS = new Set(['age_restricted', 'login_required', 'members_only']);
