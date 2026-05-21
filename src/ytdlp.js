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
  if (fs.existsSync(BIN_PATH)) return BIN_PATH;

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

async function search(query, limit = 10) {
  const bin = await ensureYtDlp();
  const searchQuery = `ytsearch${limit}:${query} karaoke`;

  return new Promise((resolve, reject) => {
    execFile(bin, [
      searchQuery,
      '--flat-playlist',
      '--dump-single-json',
      '--no-warnings',
      '--no-playlist',
    ], { timeout: 30000 }, (err, stdout) => {
      if (err) return reject(err);
      try {
        const data = JSON.parse(stdout.trim());
        const entries = (data.entries || []).map(e => ({
          id:        e.id,
          title:     e.title || '',
          uploader:  e.uploader || e.channel || '',
          duration:  e.duration || 0,
          thumbnail: e.thumbnail || `https://i.ytimg.com/vi/${e.id}/hqdefault.jpg`,
        }));
        resolve(entries);
      } catch (e) {
        reject(new Error('Failed to parse yt-dlp output: ' + e.message));
      }
    });
  });
}

async function getStreamUrl(videoId) {
  const bin = await ensureYtDlp();
  const url  = `https://www.youtube.com/watch?v=${videoId}`;

  return new Promise((resolve, reject) => {
    execFile(bin, [
      '-g',
      '-f', 'best[height<=720][ext=mp4]/best[height<=720]/best',
      '--no-playlist',
      '--no-warnings',
      url,
    ], { timeout: 20000 }, (err, stdout) => {
      if (err) return reject(err);
      const streamUrl = stdout.trim().split('\n')[0];
      if (!streamUrl) return reject(new Error('No stream URL returned'));
      resolve(streamUrl);
    });
  });
}

function fmtDuration(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

module.exports = { ensureYtDlp, search, getStreamUrl, fmtDuration };
