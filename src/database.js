const path = require('path');
const fs   = require('fs');
const { app } = require('electron');

const dbPath = path.join(app.getPath('userData'), 'karaoke.db');
let db;

function save() {
  fs.writeFileSync(dbPath, Buffer.from(db.export()));
}

function all(sql, params = []) {
  const stmt = db.prepare(sql);
  if (params.length) stmt.bind(params);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}

function get(sql, params = []) {
  return all(sql, params)[0] || null;
}

function run(sql, params = []) {
  db.run(sql, params.length ? params : undefined);
  const lastInsertRowid = (get('SELECT last_insert_rowid() AS id') || {}).id || 0;
  return { lastInsertRowid, changes: db.getRowsModified() };
}

async function initialize() {
  const initSqlJs = require('sql.js');
  const SQL = await initSqlJs();
  const buf = fs.existsSync(dbPath) ? fs.readFileSync(dbPath) : null;
  db = buf ? new SQL.Database(buf) : new SQL.Database();

  db.run('PRAGMA foreign_keys=ON');

  db.run(`CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    artist      TEXT    NOT NULL,
    file_path   TEXT    UNIQUE NOT NULL,
    audio_path  TEXT,
    file_format TEXT    NOT NULL,
    duration    REAL    DEFAULT 0,
    language    TEXT    DEFAULT '',
    genre       TEXT    DEFAULT '',
    decade      TEXT    DEFAULT '',
    year        INTEGER,
    date_added  TEXT    DEFAULT (datetime('now')),
    play_count  INTEGER DEFAULT 0,
    last_played TEXT,
    rating      INTEGER DEFAULT 0,
    is_favorite INTEGER DEFAULT 0,
    is_active   INTEGER DEFAULT 1,
    notes       TEXT    DEFAULT ''
  )`);

  db.run(`CREATE TABLE IF NOT EXISTS singers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT UNIQUE NOT NULL,
    created_at       TEXT DEFAULT (datetime('now')),
    total_songs_sung INTEGER DEFAULT 0,
    last_seen        TEXT
  )`);

  db.run(`CREATE TABLE IF NOT EXISTS queue_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL,
    singer_id   INTEGER,
    position    INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'pending',
    added_at    TEXT    DEFAULT (datetime('now')),
    started_at  TEXT,
    finished_at TEXT
  )`);

  // Migration: add device_id if not present (safe to run repeatedly)
  try { db.run(`ALTER TABLE queue_entries ADD COLUMN device_id TEXT DEFAULT ''`); } catch {}

  db.run(`CREATE TABLE IF NOT EXISTS play_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id   INTEGER,
    singer_id INTEGER,
    played_at TEXT DEFAULT (datetime('now'))
  )`);

  db.run(`CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
  )`);

  save();
}

// ── Songs ─────────────────────────────────────────────────────────────────────
function searchSongs(q = '', limit = 20) {
  const like = `%${q}%`;
  return all(
    `SELECT * FROM songs WHERE is_active=1 AND (title LIKE ? OR artist LIKE ?)
     ORDER BY artist, title LIMIT ?`,
    [like, like, limit]
  );
}

function getSongs({ q='', genre='', language='', decade='', fmt='', rating=0,
                    favorites=false, sort='artist', page=1, pageSize=50 } = {}) {
  const where = ['is_active=1'];
  const params = [];
  if (q)         { where.push('(title LIKE ? OR artist LIKE ?)'); const l=`%${q}%`; params.push(l,l); }
  if (genre)     { where.push('genre LIKE ?');    params.push(`%${genre}%`); }
  if (language)  { where.push('language LIKE ?'); params.push(`%${language}%`); }
  if (decade)    { where.push('decade = ?');      params.push(decade); }
  if (fmt)       { where.push('file_format = ?'); params.push(fmt); }
  if (rating)    { where.push('rating >= ?');     params.push(+rating); }
  if (favorites) { where.push('is_favorite = 1'); }

  const orderMap = { artist:'artist', title:'title', date_added:'date_added DESC',
                     play_count:'play_count DESC', rating:'rating DESC' };
  const order = orderMap[sort] || 'artist';
  const w = where.join(' AND ');

  const total = (get(`SELECT COUNT(*) AS n FROM songs WHERE ${w}`, params) || {n:0}).n;
  const songs = all(`SELECT * FROM songs WHERE ${w} ORDER BY ${order} LIMIT ? OFFSET ?`,
                    [...params, pageSize, (page-1)*pageSize]);

  const genres    = all(`SELECT DISTINCT genre    FROM songs WHERE is_active=1 AND genre!=''    ORDER BY genre`).map(r=>r.genre);
  const languages = all(`SELECT DISTINCT language FROM songs WHERE is_active=1 AND language!='' ORDER BY language`).map(r=>r.language);
  const decades   = all(`SELECT DISTINCT decade   FROM songs WHERE is_active=1 AND decade!=''   ORDER BY decade`).map(r=>r.decade);
  const formats   = all(`SELECT DISTINCT file_format FROM songs WHERE is_active=1 ORDER BY file_format`).map(r=>r.file_format);

  return { songs, total, page, pages: Math.ceil(total/pageSize), genres, languages, decades, formats };
}

function importSong(meta) {
  const existing = get('SELECT id FROM songs WHERE file_path=?', [meta.file_path]);
  if (existing) return { id: existing.id, status: 'exists' };
  const r = run(
    `INSERT INTO songs (title,artist,file_path,audio_path,file_format,duration,genre,language,decade,year)
     VALUES (?,?,?,?,?,?,?,?,?,?)`,
    [meta.title, meta.artist, meta.file_path, meta.audio_path||null, meta.file_format,
     meta.duration||0, meta.genre||'', meta.language||'', meta.decade||'', meta.year||null]
  );
  save();
  return { id: r.lastInsertRowid, status: 'created' };
}

function editSong(id, data) {
  run(`UPDATE songs SET title=?,artist=?,language=?,genre=?,decade=?,notes=? WHERE id=?`,
      [data.title, data.artist, data.language||'', data.genre||'', data.decade||'', data.notes||'', id]);
  save();
  return { ok: true };
}

function deleteSong(id)       { run('DELETE FROM songs WHERE id=?', [id]); save(); return { ok: true }; }
function rateSong(id, rating) { run('UPDATE songs SET rating=? WHERE id=?', [rating, id]); save(); return { ok: true }; }

function toggleFavorite(id) {
  const s = get('SELECT is_favorite FROM songs WHERE id=?', [id]);
  const v = s ? (s.is_favorite ? 0 : 1) : 0;
  run('UPDATE songs SET is_favorite=? WHERE id=?', [v, id]);
  save();
  return { is_favorite: !!v };
}

// ── Queue ─────────────────────────────────────────────────────────────────────
function getQueue() {
  return all(`
    SELECT q.*, s.title, s.artist, s.file_path, s.audio_path, s.file_format, s.duration,
           sg.name AS singer_name
    FROM queue_entries q
    JOIN songs s ON q.song_id = s.id
    LEFT JOIN singers sg ON q.singer_id = sg.id
    WHERE q.status IN ('pending','playing')
    ORDER BY CASE WHEN q.status='playing' THEN 0 ELSE 1 END, q.position
  `);
}

function getHistory(limit = 30) {
  return all(`
    SELECT q.id, q.song_id, q.finished_at,
           s.title, s.artist, s.file_format, s.duration,
           sg.name AS singer_name
    FROM queue_entries q
    JOIN songs s ON q.song_id = s.id
    LEFT JOIN singers sg ON q.singer_id = sg.id
    WHERE q.status = 'done'
    ORDER BY q.finished_at DESC
    LIMIT ?
  `, [limit]);
}

function getCurrentSong() {
  return get(`
    SELECT q.id AS queue_id, q.song_id, q.status,
           s.title, s.artist, s.file_path, s.audio_path, s.file_format, s.duration,
           sg.name AS singer_name
    FROM queue_entries q
    JOIN songs s ON q.song_id = s.id
    LEFT JOIN singers sg ON q.singer_id = sg.id
    WHERE q.status = 'playing'
    LIMIT 1
  `) || null;
}

function _getOrCreateSinger(name) {
  name = (name||'').trim();
  if (!name) return null;
  const existing = get('SELECT id FROM singers WHERE name=?', [name]);
  if (existing) return existing.id;
  return run('INSERT INTO singers (name) VALUES (?)', [name]).lastInsertRowid;
}

function _reorder() {
  const entries = all(`SELECT id FROM queue_entries WHERE status='pending' ORDER BY position`);
  entries.forEach((e, i) => run('UPDATE queue_entries SET position=? WHERE id=?', [i, e.id]));
}

function addToQueue(songId, singerName, deviceId = '') {
  const singerId = _getOrCreateSinger(singerName);
  const maxPos   = (get(`SELECT COUNT(*) AS n FROM queue_entries WHERE status='pending'`) || {n:0}).n;
  const r = run(
    `INSERT INTO queue_entries (song_id,singer_id,position,device_id) VALUES (?,?,?,?)`,
    [songId, singerId, maxPos, deviceId || '']
  );
  save();
  return { id: r.lastInsertRowid, position: maxPos };
}

// Returns null if not recently played, or remaining seconds to wait if it was
function songPlayedRecently(songId, hours) {
  const h = parseInt(hours) || 0;
  if (h <= 0) return null;
  const row = get(
    `SELECT CAST((${h * 3600} - (strftime('%s','now') - strftime('%s', played_at))) AS INTEGER) AS remaining_sec
     FROM play_history
     WHERE song_id=? AND played_at > datetime('now', '-${h} hours')
     ORDER BY played_at DESC LIMIT 1`,
    [songId]
  );
  if (!row) return null;
  return Math.max(1, row.remaining_sec);
}

function getDeviceQueueCount(deviceId) {
  if (!deviceId) return 0;
  const row = get(
    `SELECT COUNT(*) AS n FROM queue_entries WHERE device_id=? AND status IN ('pending','playing')`,
    [deviceId]
  );
  return (row?.n || 0);
}

function removeFromQueue(id) {
  run('DELETE FROM queue_entries WHERE id=?', [id]);
  _reorder();
  save();
  return { ok: true };
}

function skipEntry(id) {
  run(`UPDATE queue_entries SET status='skipped', finished_at=datetime('now') WHERE id=?`, [id]);
  _reorder();
  save();
  return { ok: true };
}

function moveEntry(id, direction) {
  const entries = all(`SELECT id,position FROM queue_entries WHERE status='pending' ORDER BY position`);
  const idx = entries.findIndex(e => e.id === id);
  if (idx < 0) return { ok: false };
  const swap = direction === 'up' ? idx-1 : idx+1;
  if (swap < 0 || swap >= entries.length) return { ok: true };
  run('UPDATE queue_entries SET position=? WHERE id=?', [entries[swap].position, entries[idx].id]);
  run('UPDATE queue_entries SET position=? WHERE id=?', [entries[idx].position, entries[swap].id]);
  save();
  return { ok: true };
}

function moveToPosition(id, newIdx) {
  const entries = all(`SELECT id,position FROM queue_entries WHERE status='pending' ORDER BY position`);
  const fromIdx = entries.findIndex(e => e.id === id);
  if (fromIdx < 0) return { ok: false };
  newIdx = Math.max(0, Math.min(entries.length - 1, newIdx));
  if (fromIdx === newIdx) return { ok: true };
  const [entry] = entries.splice(fromIdx, 1);
  entries.splice(newIdx, 0, entry);
  entries.forEach((e, i) => run('UPDATE queue_entries SET position=? WHERE id=?', [i, e.id]));
  save();
  return { ok: true };
}

function nextSong() {
  const playing = get(`SELECT * FROM queue_entries WHERE status='playing' LIMIT 1`);
  if (playing) {
    run(`UPDATE queue_entries SET status='done', finished_at=datetime('now') WHERE id=?`, [playing.id]);
    run(`INSERT INTO play_history (song_id,singer_id) VALUES (?,?)`, [playing.song_id, playing.singer_id]);
    run(`UPDATE songs SET play_count=play_count+1, last_played=datetime('now') WHERE id=?`, [playing.song_id]);
    if (playing.singer_id) {
      run(`UPDATE singers SET total_songs_sung=total_songs_sung+1, last_seen=datetime('now') WHERE id=?`, [playing.singer_id]);
    }
  }
  const next = get(`SELECT * FROM queue_entries WHERE status='pending' ORDER BY position LIMIT 1`);
  if (next) {
    run(`UPDATE queue_entries SET status='playing', started_at=datetime('now') WHERE id=?`, [next.id]);
    save();
    return getCurrentSong();
  }
  save();
  return null;
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function getStats() {
  return {
    total_songs:   (get(`SELECT COUNT(*) AS n FROM songs WHERE is_active=1`) || {n:0}).n,
    total_singers: (get(`SELECT COUNT(*) AS n FROM singers`) || {n:0}).n,
    total_plays:   (get(`SELECT COUNT(*) AS n FROM play_history`) || {n:0}).n,
    top_songs:     all(`SELECT title,artist,play_count FROM songs WHERE play_count>0 ORDER BY play_count DESC LIMIT 10`),
    top_singers:   all(`SELECT name,total_songs_sung FROM singers WHERE total_songs_sung>0 ORDER BY total_songs_sung DESC LIMIT 10`),
  };
}

// ── Settings ──────────────────────────────────────────────────────────────────
function getSettings() {
  return Object.fromEntries(all('SELECT key,value FROM settings').map(r => [r.key, r.value]));
}

function setSetting(key, value) {
  run('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', [key, value]);
  save();
  return { ok: true };
}

module.exports = {
  initialize,
  searchSongs, getSongs, importSong, editSong, deleteSong, rateSong, toggleFavorite,
  getQueue, getHistory, getCurrentSong, addToQueue, getDeviceQueueCount, songPlayedRecently, removeFromQueue, skipEntry, moveEntry, moveToPosition, nextSong,
  getStats, getSettings, setSetting,
};
