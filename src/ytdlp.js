const path     = require('path');
const fs       = require('fs');
const { app }  = require('electron');
const { execFile, spawn } = require('child_process');

const BIN_DIR  = app.getPath('userData');
const BIN_PATH = path.join(BIN_DIR, 'yt-dlp.exe');
const BIN_URL  = 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe';

let _ensurePromise = null;

async function ensureYtDlp(onProgress) {
  if (_ensurePromise) return _ensurePromise;
  _ensurePromise = _doEnsure(onProgress);
  return _ensurePromise;
}

async function _doEnsure(onProgress) {
  if (fs.existsSync(BIN_PATH)) {
    // YouTube changes its bot-detection frequently; yt-dlp ships near-daily fixes.
    // Self-update once per app launch so a stale binary doesn't start failing silently.
    await new Promise((resolve) => execFile(BIN_PATH, ['-U'], { timeout: 30000 }, () => resolve()));
    return BIN_PATH;
  }

  onProgress?.({ stage: 'downloading', pct: 0 });

  const resp = await fetch(BIN_URL);
  if (!resp.ok) throw new Error(`yt-dlp download failed: HTTP ${resp.status}`);

  const total = parseInt(resp.headers.get('content-length') || '0');
  const reader = resp.body.getReader();
  const chunks = [];
  let loaded = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    if (total) onProgress?.({ stage: 'downloading', pct: Math.round(loaded / total * 100) });
  }

  await fs.promises.writeFile(BIN_PATH, Buffer.concat(chunks.map(c => Buffer.from(c))));
  onProgress?.({ stage: 'ready' });
  return BIN_PATH;
}

function cookiesArgs({ file, browser } = {}) {
  if (file)    return ['--cookies', file];
  if (browser) return ['--cookies-from-browser', browser];
  return [];
}

// Avoids YouTube's "Sign in to confirm you're not a bot" check on the deprecated
// android_sdkless client without needing cookies. See yt-dlp/yt-dlp#15865.
const CLIENT_ARGS = ['--extractor-args', 'youtube:player_client=default,-android_sdkless'];

function _execFile(bin, args, opts) {
  return new Promise((resolve, reject) => {
    execFile(bin, args, opts, (err, stdout) => err ? reject(err) : resolve(stdout));
  });
}

// Cookies are opportunistic, never required: if a configured cookie source fails
// (e.g. browser cookie DB unreadable), retry once without cookies rather than
// hard-failing a search/stream that would otherwise have worked.
async function _runWithCookieFallback(bin, baseArgs, cookieOpts, opts) {
  const withCookies = cookiesArgs(cookieOpts);
  if (!withCookies.length) return _execFile(bin, baseArgs, opts);
  try {
    return await _execFile(bin, [...baseArgs, ...withCookies], opts);
  } catch {
    return _execFile(bin, baseArgs, opts);
  }
}

async function search(query, limit = 10, cookieOpts) {
  const bin = await ensureYtDlp();
  const searchQuery = `ytsearch${limit}:${query} karaoke`;

  const stdout = await _runWithCookieFallback(bin, [
    searchQuery,
    '--flat-playlist',
    '--dump-single-json',
    '--no-warnings',
    '--no-playlist',
    ...CLIENT_ARGS,
  ], cookieOpts, { timeout: 30000 });

  try {
    const data = JSON.parse(stdout.trim());
    return (data.entries || []).map(e => ({
      id:        e.id,
      title:     e.title || '',
      uploader:  e.uploader || e.channel || '',
      duration:  e.duration || 0,
      thumbnail: e.thumbnail || `https://i.ytimg.com/vi/${e.id}/hqdefault.jpg`,
    }));
  } catch (e) {
    throw new Error('Failed to parse yt-dlp output: ' + e.message);
  }
}

async function getStreamUrl(videoId, cookieOpts) {
  const bin = await ensureYtDlp();
  const url  = `https://www.youtube.com/watch?v=${videoId}`;

  const stdout = await _runWithCookieFallback(bin, [
    '-g',
    '-f', 'best[height<=720][ext=mp4]/best[height<=720]/best',
    '--no-playlist',
    '--no-warnings',
    ...CLIENT_ARGS,
    url,
  ], cookieOpts, { timeout: 20000 });

  const streamUrl = stdout.trim().split('\n')[0];
  if (!streamUrl) throw new Error('No stream URL returned');
  return streamUrl;
}

function fmtDuration(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

module.exports = { ensureYtDlp, search, getStreamUrl, fmtDuration };
