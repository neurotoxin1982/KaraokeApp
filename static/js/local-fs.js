/**
 * local-fs.js — Local file access for all browsers.
 *
 * Strategy:
 *  • Chrome / Edge  → File System Access API (showDirectoryPicker)
 *                     Persistent across sessions via IndexedDB.
 *  • Safari / Firefox → <input webkitdirectory> fallback.
 *                     Files cached in memory for the current session only.
 *                     User must re-select the folder after a page reload.
 */

// ── State ─────────────────────────────────────────────────────────────────────

const _DB_NAME = 'karaoke-local-fs';
const _STORE   = 'handles';

let _rootHandle = null;          // FileSystemDirectoryHandle (Chrome/Edge)
let _fileCache  = new Map();     // relPath → File  (Safari/Firefox fallback)
let _rootName   = null;          // display name of the selected root folder

// ── IndexedDB (for Chrome/Edge persistence) ───────────────────────────────────

function _openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_DB_NAME, 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore(_STORE);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

async function _saveHandle(handle) {
  try {
    const db = await _openDB();
    await new Promise((res, rej) => {
      const tx = db.transaction(_STORE, 'readwrite');
      tx.objectStore(_STORE).put(handle, 'root');
      tx.oncomplete = res; tx.onerror = rej;
    });
  } catch (_) {}
}

async function _loadHandle() {
  try {
    const db = await _openDB();
    return await new Promise((res, rej) => {
      const tx  = db.transaction(_STORE, 'readonly');
      const req = tx.objectStore(_STORE).get('root');
      req.onsuccess = e => res(e.target.result || null);
      req.onerror   = rej;
    });
  } catch (_) { return null; }
}

// ── webkitdirectory fallback ──────────────────────────────────────────────────

function _pickWithInput() {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.multiple = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    input.onchange = () => {
      const files = Array.from(input.files || []);
      document.body.removeChild(input);
      if (!files.length) { resolve(null); return; }

      const root = files[0].webkitRelativePath.split('/')[0];
      _rootName = root;
      _fileCache.clear();
      for (const file of files) {
        const parts = file.webkitRelativePath.split('/');
        const relPath = parts.slice(1).join('/'); // strip root folder name
        _fileCache.set(relPath, file);
        _fileCache.set(relPath.toLowerCase(), file); // case-insensitive lookup
      }
      resolve({ name: root, _fallback: true });
    };

    input.oncancel = () => { document.body.removeChild(input); resolve(null); };
    input.click();
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

const _hasNativeAPI = typeof window !== 'undefined' && !!window.showDirectoryPicker;

/** Open a folder picker (native dialog on all browsers). */
async function localFsPick() {
  if (_hasNativeAPI) {
    try {
      const handle = await window.showDirectoryPicker({ mode: 'read' });
      _rootHandle = handle;
      _rootName   = handle.name;
      _fileCache.clear();
      await _saveHandle(handle);
      return handle;
    } catch (e) {
      if (e.name !== 'AbortError') console.error(e);
      return null;
    }
  } else {
    return _pickWithInput();
  }
}

/**
 * Restore access from a previous session (Chrome/Edge only).
 * On Safari/Firefox the user must re-pick each session.
 */
async function localFsRestore() {
  if (_rootHandle) return _rootHandle;
  if (_fileCache.size > 0) return { name: _rootName, _fallback: true };
  if (!_hasNativeAPI) return null;

  const saved = await _loadHandle();
  if (!saved) return null;
  try {
    const perm = await saved.queryPermission({ mode: 'read' });
    if (perm === 'granted') { _rootHandle = saved; _rootName = saved.name; return saved; }
    const req  = await saved.requestPermission({ mode: 'read' });
    if (req  === 'granted') { _rootHandle = saved; _rootName = saved.name; return saved; }
  } catch (_) {}
  return null;
}

/** Display name of the selected root folder, or null. */
function localFsRootName() { return _rootName; }

/** True if files are available for playback right now. */
function localFsReady() { return !!_rootHandle || _fileCache.size > 0; }

// ── File access ───────────────────────────────────────────────────────────────

/** Get a File object by relative path. */
async function localFsGetFile(relPath) {
  // Fallback cache (Safari/Firefox)
  if (_fileCache.size > 0) {
    return _fileCache.get(relPath) || _fileCache.get(relPath.toLowerCase()) || null;
  }

  // File System Access API
  const root = _rootHandle || await localFsRestore();
  if (!root || root._fallback) return null;
  const parts = relPath.split('/');
  let cur = root;
  for (let i = 0; i < parts.length - 1; i++) {
    try { cur = await cur.getDirectoryHandle(parts[i]); }
    catch (_) { return null; }
  }
  try {
    return (await cur.getFileHandle(parts[parts.length - 1])).getFile();
  } catch (_) { return null; }
}

/** Create a blob URL for a local file. Caller must revoke it. */
async function localFsBlobUrl(relPath) {
  const f = await localFsGetFile(relPath);
  return f ? URL.createObjectURL(f) : null;
}

/** Read a local file as ArrayBuffer. */
async function localFsArrayBuffer(relPath) {
  const f = await localFsGetFile(relPath);
  return f ? f.arrayBuffer() : null;
}

// ── Directory walk ────────────────────────────────────────────────────────────

async function* _walkHandle(dirHandle, base) {
  for await (const [name, handle] of dirHandle.entries()) {
    const relPath = base ? `${base}/${name}` : name;
    if (handle.kind === 'file') yield { name, relPath };
    else yield* _walkHandle(handle, relPath);
  }
}

async function* localFsWalk() {
  if (_fileCache.size > 0) {
    const seen = new Set();
    for (const relPath of _fileCache.keys()) {
      if (seen.has(relPath.toLowerCase())) continue; // skip duplicate lower-case key
      seen.add(relPath.toLowerCase());
      const name = relPath.split('/').pop();
      yield { name, relPath };
    }
    return;
  }
  const root = _rootHandle || await localFsRestore();
  if (!root || root._fallback) return;
  yield* _walkHandle(root, '');
}

// ── Client-side scanner ───────────────────────────────────────────────────────

const _VIDEO = new Set(['.mp4', '.avi', '.webm', '.mkv', '.m4v', '.mpeg', '.mpg']);
const _AUDIO = new Set(['.mp3', '.ogg', '.m4a', '.flac', '.wav']);
const _KAR   = new Set(['.kar', '.mid']);

function _ext(name) {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function _stem(relPath) { return relPath.slice(0, relPath.lastIndexOf('.')); }

function _parseName(raw) {
  const m = raw.match(/^(.+?)\s*[-–]\s*(.+)$/);
  return m ? { artist: m[1].trim(), title: m[2].trim() }
           : { artist: 'Unknown', title: raw.trim() };
}

async function _post(meta) {
  await fetch('/api/songs/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  });
}

/**
 * Scan the selected folder and import song metadata to the server.
 * @param {function} onProgress  called with (done, total, added)
 */
async function localFsScan(onProgress) {
  if (!localFsReady()) return { error: 'No folder selected' };

  const allFiles = [];
  for await (const entry of localFsWalk()) allFiles.push(entry);

  const total = allFiles.length;
  let done = 0, added = 0;

  const audioMap = new Map();
  for (const f of allFiles) {
    if (_AUDIO.has(_ext(f.name)))
      audioMap.set(_stem(f.relPath).toLowerCase(), f.relPath);
  }

  for (const f of allFiles) {
    done++;
    const e = _ext(f.name);
    const s = _stem(f.relPath);
    const base = f.name.slice(0, f.name.lastIndexOf('.'));
    const { artist, title } = _parseName(base);

    try {
      if (e === '.cdg') {
        const audio = audioMap.get(s.toLowerCase());
        if (audio) {
          await _post({ title, artist, file_path: f.relPath, audio_path: audio, file_format: 'cdg' });
          added++;
        }
      } else if (_VIDEO.has(e)) {
        await _post({ title, artist, file_path: f.relPath, file_format: e.slice(1) });
        added++;
      } else if (_KAR.has(e)) {
        await _post({ title, artist, file_path: f.relPath, file_format: 'kar' });
        added++;
      }
    } catch (_) {}

    onProgress(done, total, added);
  }
  return { done, total, added };
}
