import { afterEach, describe, expect, it } from 'vitest';
import { DEFAULT_OPTIONS, loadOptions, saveOptions, ytDlpCommand } from './ytdlp';

describe('ytDlpCommand', () => {
  afterEach(() => localStorage.clear());

  it('uses the defaults without keyring or runtime flags', () => {
    const cmd = ytDlpCommand('dQw4w9WgXcQ', 1080, DEFAULT_OPTIONS);
    expect(cmd.startsWith('yt-dlp --cookies-from-browser chrome -S "res:1080" ')).toBe(true);
    expect(cmd).not.toContain('--js-runtimes');
    expect(cmd.endsWith('"https://www.youtube.com/watch?v=dQw4w9WgXcQ"')).toBe(true);
  });

  it('adds the keyring only for Chromium-based browsers', () => {
    expect(ytDlpCommand('dQw4w9WgXcQ', 720, { browser: 'brave', keyring: 'kwallet6', js: 'bun' }))
      .toContain('--cookies-from-browser brave+kwallet6 --js-runtimes bun');
    expect(ytDlpCommand('dQw4w9WgXcQ', 720, { browser: 'firefox', keyring: 'gnomekeyring', js: 'deno' }))
      .toContain('--cookies-from-browser firefox -S');
  });

  it('remembers valid choices and ignores junk', () => {
    saveOptions({ browser: 'chromium', keyring: 'gnomekeyring', js: 'node' });
    expect(loadOptions()).toEqual({ browser: 'chromium', keyring: 'gnomekeyring', js: 'node' });
    localStorage.setItem('downtube.ytdlp-options', '{"browser":"netscape","keyring":"x","js":"node"}');
    expect(loadOptions()).toEqual({ browser: 'chrome', keyring: 'auto', js: 'node' });
    localStorage.setItem('downtube.ytdlp-options', 'not json');
    expect(loadOptions()).toEqual(DEFAULT_OPTIONS);
  });
});
