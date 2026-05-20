/**
 * local-fs.js — Browser File System Access API manager.
 * Lets the karaoke app read music files directly from the user's local drive
 * without uploading anything. The server only stores metadata.
 */

const _DB_NAME = 'karaoke-local-fs';
const _STORE  = 'handles';
let _rootHandle = null;

// ── IndexedDB helpers ────────────────────────────────────────────────────────

function _openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_DB_NAME, 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore(_STORE);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

async function _saveHandle(handle) {
  const db = await _openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_STORE, 'readwrite');
    tx.objectStore(_STORE).put(handle, 'root');
    tx.oncomplete = resolve;
    tx.onerror    = reject;
  });
}

async function _loadHandle() {
  const db = await _openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_STORE, 'readonly');
    const req = tx.objectStore(_STORE).get('root');
    req.onsuccess = e => resolve(e.target.result || null);
    req.onerror   = reject;
  });
}

// ── Public API ───────────────────────────────────────────────────────────────

/** Show the OS folder picker and remember the selection. */
async function localFsPick() {
  if (!window.showDirectoryPicker) {
    alert('Your browser does not support the File System Access API.\nPlease use Chrome or Edge.');
    return null;
  }
  try {
    const handle = await window.showDirectoryPicker({ mode: 'read' });
    _rootHandle = handle;
    await _saveHandle(handle);
    return handle;
  } catch (e) {
    if (e.name !== 'AbortError') console.error(e);
    return null;
  }
}

/** Restore the saved handle and ask for permission if needed. */
async function localFsRestore() {
  if (_rootHandle) return _rootHandle;
  const saved = await _loadHandle();
  if (!saved) return null;
  try {
    const perm = await saved.queryPermission({ mode: 'read' });
    if (perm === 'granted') { _rootHandle = saved; return saved; }
    const req = await saved.requestPermission({ mode: 'read' });
    if (req === 'granted') { _rootHandle = saved; return saved; }
  } catch (_) {}
  return null;
}

/** Return the root handle name (e.g. "Music") or null. */
function localFsRootName() {
  return _rootHandle ? _rootHandle.name : null;
}

/** Walk a directory recursively, yielding { name, relPath, handle }. */
async function* localFsWalk(dirHandle, base = '') {
  for await (const [name, handle] of dirHandle.entries()) {
    const relPath = base ? `${base}/${name}` : name;
    if (handle.kind === 'file') {
      yield { name, relPath, handle };
    } else {
      yield* localFsWalk(handle, relPath);
    }
  }
}

/** Get a File object by relative path (e.g. "ABBA/Dancing Queen.cdg"). */
async function localFsGetFile(relPath) {
  const root = _rootHandle || await localFsRestore();
  if (!root) return null;
  const parts = relPath.split('/');
  let cur = root;
  for (let i = 0; i < parts.length - 1; i++) {
    try { cur = await cur.getDirectoryHandle(parts[i]); }
    catch (_) { return null; }
  }
  try {
    const fh = await cur.getFileHandle(parts[parts.length - 1]);
    return fh.getFile();
  } catch (_) { return null; }
}

/** Create a temporary blob URL for a local file. Caller must revoke it. */
async function localFsBlobUrl(relPath) {
  const file = await localFsGetFile(relPath);
  return file ? URL.createObjectURL(file) : null;
}

/** Read a local file as ArrayBuffer. */
async function localFsArrayBuffer(relPath) {
  const file = await localFsGetFile(relPath);
  return file ? file.arrayBuffer() : null;
}

// ── Client-side scanner ──────────────────────────────────────────────────────

const _VIDEO_EXT = new Set(['.mp4', '.avi', '.webm', '.mkv', '.m4v', '.mpeg', '.mpg']);
const _AUDIO_EXT = new Set(['.mp3', '.ogg', '.m4a', '.flac', '.wav']);
const _KAR_EXT   = new Set(['.kar', '.mid']);

function _stem(relPath) {
  return relPath.slice(0, relPath.lastIndexOf('.'));
}

function _ext(name) {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function _parseName(raw) {
  const m = raw.match(/^(.+?)\s*[-–]\s*(.+)$/);
  return m ? { artist: m[1].trim(), title: m[2].trim() }
           : { artist: 'Unknown', title: raw.trim() };
}

async function _importSong(meta) {
  await fetch('/api/songs/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  });
}

/**
 * Scan the selected directory and import all karaoke songs to the server DB.
 * @param {function} onProgress - called with (done, total, added)
 */
async function localFsScan(onProgress) {
  const root = _rootHandle || await localFsRestore();
  if (!root) return { error: 'No folder selected' };

  // Collect all files first
  const allFiles = [];
  for await (const entry of localFsWalk(root)) allFiles.push(entry);

  const total = allFiles.length;
  let done = 0, added = 0;

  // Build lookup maps for CDG ↔ audio pairing
  const cdgMap   = new Map(); // stem → relPath
  const audioMap = new Map(); // stem → relPath

  for (const f of allFiles) {
    const e = _ext(f.name);
    const s = _stem(f.relPath);
    if (e === '.cdg')          cdgMap.set(s.toLowerCase(), f.relPath);
    else if (_AUDIO_EXT.has(e)) audioMap.set(s.toLowerCase(), f.relPath);
  }

  for (const f of allFiles) {
    done++;
    const e = _ext(f.name);
    const s = _stem(f.relPath);
    const baseName = f.name.slice(0, f.name.lastIndexOf('.'));
    const { artist, title } = _parseName(baseName);

    try {
      if (e === '.cdg') {
        const audioPath = audioMap.get(s.toLowerCase());
        if (!audioPath) { onProgress(done, total, added); continue; }
        await _importSong({ title, artist, file_path: f.relPath, audio_path: audioPath, file_format: 'cdg' });
        added++;
      } else if (_VIDEO_EXT.has(e)) {
        await _importSong({ title, artist, file_path: f.relPath, file_format: e.slice(1) });
        added++;
      } else if (_KAR_EXT.has(e)) {
        await _importSong({ title, artist, file_path: f.relPath, file_format: 'kar' });
        added++;
      }
    } catch (_) {}

    onProgress(done, total, added);
  }

  return { done, total, added };
}
