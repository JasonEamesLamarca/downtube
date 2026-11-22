/**
 * The yt-dlp command a user runs on their own computer for videos DownTube cannot fetch
 * (age-restricted, members-only…). It reads the user's browser cookies locally — DownTube itself
 * never receives them — and mirrors DownTube's archive policy: best original quality up to the
 * cap (shortest side), MP4 by stream copy, AAC audio preferred, metadata embedded, and the video
 * ID in brackets so the import folder can match the file.
 */
export const BROWSERS = ['chrome', 'chromium', 'firefox', 'edge', 'brave', 'opera', 'vivaldi', 'safari'] as const;
export type Browser = (typeof BROWSERS)[number];

/** Where Chromium-based browsers keep their cookie key on Linux. `auto` lets yt-dlp guess from the desktop. */
export const KEYRINGS = ['auto', 'gnomekeyring', 'kwallet6', 'kwallet5', 'basictext'] as const;
export type Keyring = (typeof KEYRINGS)[number];

/** yt-dlp needs a JavaScript runtime for YouTube; it uses Deno unless told otherwise. */
export const JS_RUNTIMES = ['deno', 'node', 'bun'] as const;
export type JsRuntime = (typeof JS_RUNTIMES)[number];

export interface CommandOptions {
  browser: Browser;
  keyring: Keyring;
  js: JsRuntime;
}

export const DEFAULT_OPTIONS: CommandOptions = { browser: 'chrome', keyring: 'auto', js: 'deno' };

/** Firefox and Safari do not use a keyring for cookies. */
export const usesKeyring = (b: Browser) => b !== 'firefox' && b !== 'safari';

export const youtubeUrl = (videoId: string) => `https://www.youtube.com/watch?v=${videoId}`;

export function ytDlpCommand(videoId: string, cap: number, opts: CommandOptions): string {
  const cookies = opts.browser + (usesKeyring(opts.browser) && opts.keyring !== 'auto' ? `+${opts.keyring}` : '');
  return [
    'yt-dlp',
    `--cookies-from-browser ${cookies}`,
    ...(opts.js !== 'deno' ? [`--js-runtimes ${opts.js}`] : []),
    `-S "res:${cap}"`,
    '-f "bv*+ba[acodec^=mp4a]/bv*+ba/b"',
    '--merge-output-format mp4',
    '--embed-metadata --no-embed-chapters --no-embed-info-json',
    '-o "%(title).150B [%(id)s].%(ext)s"',
    `"${youtubeUrl(videoId)}"`,
  ].join(' ');
}

const STORAGE_KEY = 'downtube.ytdlp-options';

/** The user's last choices (per browser; falls back to defaults when storage is unavailable). */
export function loadOptions(): CommandOptions {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_OPTIONS;
    const o = JSON.parse(raw) as Partial<CommandOptions>;
    return {
      browser: BROWSERS.includes(o.browser as Browser) ? (o.browser as Browser) : DEFAULT_OPTIONS.browser,
      keyring: KEYRINGS.includes(o.keyring as Keyring) ? (o.keyring as Keyring) : DEFAULT_OPTIONS.keyring,
      js: JS_RUNTIMES.includes(o.js as JsRuntime) ? (o.js as JsRuntime) : DEFAULT_OPTIONS.js,
    };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

export function saveOptions(o: CommandOptions): void {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(o)); } catch { /* storage unavailable: not remembered */ }
}

/** Errors the user can work around by downloading the video with their own account. */
export const SIGN_IN_ERRORS = new Set(['age_restricted', 'login_required', 'members_only']);
