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

// Canonical karaoke providers. Matched by searching each channel's own "/search"
// tab directly (see _searchChannel), not by display name — e.g. "TheKARAOKEChannel"
// is the handle users know it by, though the channel now displays as "Stingray Karaoke".
const PROVIDERS = {
  singking: { label: 'Sing King',         handle: '@singkingkaraoke' },
  karafun:  { label: 'KaraFun',           handle: '@karafun' },
  stingray: { label: 'TheKARAOKEChannel', handle: '@StingrayKaraoke' },
};

const LANGUAGES = {
  et: 'Estonian',
  fi: 'Finnish',
  sv: 'Swedish',
  de: 'German',
  ru: 'Russian',
};

// Reads the youtube_provider_<key> / youtube_lang_<key> settings into the
// { providers, languages } shape `search()` expects. Providers default to
// enabled (unset = 'true'); languages default to disabled (unset = 'false').
function filterOptsFromSettings(getSetting) {
  return {
    providers: Object.keys(PROVIDERS).filter(k => getSetting(`youtube_provider_${k}`) !== 'false'),
    languages: Object.keys(LANGUAGES).filter(k => getSetting(`youtube_lang_${k}`) === 'true'),
  };
}

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

function _toResult(e) {
  return {
    id:        e.id,
    title:     e.title || '',
    uploader:  e.uploader || e.channel || '',
    duration:  e.duration || 0,
    thumbnail: e.thumbnail || `https://i.ytimg.com/vi/${e.id}/hqdefault.jpg`,
  };
}

// A broad search has no trusted-channel guarantee (unlike _searchChannel), so
// YouTube's fuzzy relevance ranking alone lets plain music/lyric videos slip in
// even though "karaoke" is in the query — require it in the actual title too.
// Includes the Cyrillic spelling since Russian karaoke channels often use it.
const KARAOKE_TITLE_RE = /karaoke|караоке/i;

async function _searchBroad(bin, searchQuery, limit, cookieOpts) {
  const stdout = await _runWithCookieFallback(bin, [
    `ytsearch${limit}:${searchQuery}`,
    '--flat-playlist',
    '--dump-single-json',
    '--no-warnings',
    '--no-playlist',
    ...CLIENT_ARGS,
  ], cookieOpts, { timeout: 30000 });
  const data = JSON.parse(stdout.trim());
  return (data.entries || [])
    .filter(e => KARAOKE_TITLE_RE.test(e.title || ''))
    .map(_toResult);
}

// Searches *within* a single channel via its own "/search" tab, so results are
// guaranteed to come from that provider — not just ranked highly in a broad search.
async function _searchChannel(bin, provider, query, cookieOpts) {
  const url = `https://www.youtube.com/${provider.handle}/search?query=${encodeURIComponent(query)}`;
  const stdout = await _runWithCookieFallback(bin, [
    url,
    '--flat-playlist',
    '--dump-single-json',
    '--no-warnings',
    '--playlist-items', '1-8',
    ...CLIENT_ARGS,
  ], cookieOpts, { timeout: 30000 });
  const data = JSON.parse(stdout.trim());
  // Exclude playlist/tab entries (ie_key "YoutubeTab") — keep actual videos only.
  return (data.entries || []).filter(e => e.ie_key === 'Youtube').map(_toResult);
}

async function search(query, limit = 10, cookieOpts, filterOpts) {
  const bin = await ensureYtDlp();

  if (!filterOpts) {
    return _searchBroad(bin, `${query} karaoke`, limit, cookieOpts);
  }

  const { providers = [], languages = [] } = filterOpts;
  if (!providers.length && !languages.length) return [];

  const tasks = [
    ...providers.filter(k => PROVIDERS[k]).map(k => _searchChannel(bin, PROVIDERS[k], query, cookieOpts)),
    // Ask for more than `limit` raw results since the title filter above discards some.
    ...languages.filter(k => LANGUAGES[k]).map(k => _searchBroad(bin, `${query} karaoke ${LANGUAGES[k]}`, Math.max(15, limit * 2), cookieOpts)),
  ];

  const settled = await Promise.allSettled(tasks);
  const lists = settled.filter(r => r.status === 'fulfilled').map(r => r.value);

  // Interleave round-robin across sources so one provider/language can't crowd
  // out the others just by returning more results.
  const seen = new Set();
  const merged = [];
  for (let i = 0; merged.length < limit && lists.some(l => i < l.length); i++) {
    for (const list of lists) {
      if (merged.length >= limit) break;
      const entry = list[i];
      if (!entry || seen.has(entry.id)) continue;
      seen.add(entry.id);
      merged.push(entry);
    }
  }
  return merged;
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

module.exports = { ensureYtDlp, search, getStreamUrl, fmtDuration, filterOptsFromSettings, PROVIDERS, LANGUAGES };
