import eventlet
eventlet.monkey_patch()

import os, glob, uuid, json, sqlite3
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024
app.secret_key = os.environ.get('SECRET_KEY', 'karaoke-secret-42')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', '/app/library')
DB_PATH     = os.environ.get('DB_PATH', '/app/karaoke.db')
os.makedirs(LIBRARY_DIR, exist_ok=True)

AUDIO_EXT = {'.mp3', '.m4a', '.wav', '.ogg'}
VIDEO_EXT = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.m4v'}
ALLOWED   = AUDIO_EXT | VIDEO_EXT | {'.cdg'}

# ── Database ───────────────────────────────────────────────────────────────────

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS songs (
                base    TEXT PRIMARY KEY,
                display TEXT NOT NULL,
                type    TEXT NOT NULL,
                mp3     TEXT, cdg TEXT, video TEXT, audio TEXT
            );
            CREATE TABLE IF NOT EXISTS queue (
                id        TEXT PRIMARY KEY,
                position  INTEGER NOT NULL,
                song_json TEXT NOT NULL,
                singer    TEXT NOT NULL,
                guest_id  TEXT NOT NULL DEFAULT ""
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
        ''')
        c.execute("INSERT OR IGNORE INTO settings VALUES ('song_limit','2')")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('queue_locked','0')")

def sync_library():
    by_base = {}
    for path in glob.glob(os.path.join(LIBRARY_DIR, '*')):
        fn  = os.path.basename(path)
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ALLOWED:
            continue
        base = os.path.splitext(fn)[0]
        by_base.setdefault(base, {})[ext] = fn

    rows = []
    for base, files in by_base.items():
        cdg = files.get('.cdg')
        mp3 = files.get('.mp3') or next((files[e] for e in AUDIO_EXT if e in files), None)
        mp4 = next((files[e] for e in VIDEO_EXT if e in files), None)
        display = base.replace('_', ' ').strip()
        if cdg and mp3:
            rows.append((base, display, 'cdg',   mp3, cdg,  None, None))
        elif mp4:
            rows.append((base, display, 'video', None, None, mp4, None))
        elif mp3:
            rows.append((base, display, 'audio', mp3, None, None, mp3))

    with db() as c:
        c.execute('DELETE FROM songs')
        c.executemany(
            'INSERT INTO songs(base,display,type,mp3,cdg,video,audio) VALUES(?,?,?,?,?,?,?)',
            rows
        )

def get_library():
    with db() as c:
        rows = c.execute('SELECT * FROM songs ORDER BY lower(display)').fetchall()
    out = []
    for r in rows:
        if r['type'] == 'cdg':
            out.append({'display': r['display'], 'type': 'cdg',   'mp3': r['mp3'], 'cdg': r['cdg']})
        elif r['type'] == 'video':
            out.append({'display': r['display'], 'type': 'video', 'video': r['video']})
        else:
            out.append({'display': r['display'], 'type': 'audio', 'audio': r['audio']})
    return out

def save_queue():
    with db() as c:
        c.execute('DELETE FROM queue')
        c.executemany(
            'INSERT INTO queue(id,position,song_json,singer,guest_id) VALUES(?,?,?,?,?)',
            [(it['id'], i, json.dumps(it['song']), it['singer'], it['guest_id'])
             for i, it in enumerate(queue)]
        )

def save_settings():
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES('song_limit',?)",  (str(settings['song_limit']),))
        c.execute("INSERT OR REPLACE INTO settings VALUES('queue_locked',?)", ('1' if settings['queue_locked'] else '0',))

def load_state():
    global queue, settings
    with db() as c:
        for row in c.execute('SELECT key,value FROM settings'):
            if row['key'] == 'song_limit':
                settings['song_limit'] = int(row['value'])
            elif row['key'] == 'queue_locked':
                settings['queue_locked'] = row['value'] == '1'
        queue = [
            {'id': r['id'], 'song': json.loads(r['song_json']),
             'singer': r['singer'], 'guest_id': r['guest_id']}
            for r in c.execute('SELECT * FROM queue ORDER BY position')
        ]

# ── In-memory state ────────────────────────────────────────────────────────────

queue       = []
now_playing = None
settings    = {'song_limit': 2, 'queue_locked': False}

def get_state():
    return {'now_playing': now_playing, 'queue': queue, 'settings': settings}

def advance_queue():
    global now_playing
    if queue:
        now_playing = queue.pop(0)
        save_queue()
        socketio.emit('load_song', now_playing['song'])
    else:
        now_playing = None
        socketio.emit('stop')
    socketio.emit('queue_update', get_state())

# ── Guest Interface ────────────────────────────────────────────────────────────

GUEST_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Karaoke</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0a0a12;
    --surface: #13131f;
    --border: #1e1e30;
    --accent: #7c3aed;
    --accent2: #60a5fa;
    --text: #e2e2f0;
    --muted: #4a4a6a;
    --green: #22c55e;
    --red: #ef4444;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    overflow: hidden;
  }

  .app { display: flex; flex-direction: column; height: 100dvh; }

  /* Header */
  .header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0.8rem 1rem 0.6rem;
    flex-shrink: 0;
  }
  .logo {
    font-size: 1.1rem;
    font-weight: 800;
    background: linear-gradient(120deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.55rem;
    letter-spacing: 0.02em;
  }
  .name-row { display: flex; gap: 0.5rem; margin-bottom: 0.45rem; }
  .field {
    width: 100%;
    padding: 0.6rem 0.85rem;
    background: var(--bg);
    border: 1.5px solid var(--border);
    border-radius: 0.55rem;
    color: var(--text);
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.15s;
  }
  .field:focus { border-color: var(--accent); }
  .field::placeholder { color: var(--muted); }

  /* Tabs */
  .tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .tab {
    flex: 1;
    padding: 0.7rem 0.5rem;
    text-align: center;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--muted);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
    user-select: none;
  }
  .tab.on { color: #a78bfa; border-color: #a78bfa; }
  .badge {
    display: inline-block;
    background: var(--accent);
    color: #fff;
    font-size: 0.65rem;
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
    margin-left: 0.3rem;
    vertical-align: middle;
  }

  /* Scroll pane */
  .pane { flex: 1; overflow-y: auto; padding: 0.65rem; }
  .pane.hidden { display: none; }

  /* Song rows */
  .song-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 0.9rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.6rem;
    margin-bottom: 0.35rem;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    -webkit-tap-highlight-color: transparent;
  }
  .song-row:active { background: #1a1a2e; border-color: var(--accent); }
  .song-icon { font-size: 1.15rem; flex-shrink: 0; }
  .song-name { flex: 1; font-size: 0.9rem; font-weight: 500; }
  .song-tag {
    font-size: 0.65rem;
    color: var(--muted);
    background: var(--border);
    padding: 0.15rem 0.4rem;
    border-radius: 0.3rem;
    flex-shrink: 0;
  }
  .add-btn {
    flex-shrink: 0;
    padding: 0.38rem 0.8rem;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 0.4rem;
    font-size: 0.78rem;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
  }

  /* Queue */
  .now-card {
    background: #0b1f10;
    border: 1.5px solid var(--green);
    border-radius: 0.65rem;
    padding: 0.85rem 1rem;
    margin-bottom: 0.5rem;
  }
  .now-label { font-size: 0.68rem; font-weight: 700; color: var(--green); text-transform: uppercase; letter-spacing: 0.07em; }
  .now-title { font-size: 0.95rem; font-weight: 700; color: var(--green); margin-top: 0.2rem; }
  .now-singer { font-size: 0.78rem; color: #86efac; margin-top: 0.15rem; }

  .q-item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.65rem 0.85rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.6rem;
    margin-bottom: 0.35rem;
  }
  .q-pos { font-size: 1.1rem; font-weight: 800; color: var(--accent); min-width: 1.6rem; text-align: center; flex-shrink: 0; }
  .q-info { flex: 1; min-width: 0; }
  .q-singer { font-size: 0.74rem; font-weight: 600; color: #a78bfa; }
  .q-song { font-size: 0.86rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .locked-msg {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    background: #1a0a0a;
    border: 1px solid #5a1a1a;
    border-radius: 0.6rem;
    padding: 0.85rem 1rem;
    color: #f87171;
    font-size: 0.86rem;
    margin-bottom: 0.5rem;
  }

  .empty { text-align: center; color: var(--muted); padding: 3rem 1rem; font-size: 0.9rem; }

  /* Toast */
  .toast {
    position: fixed;
    bottom: 1.5rem;
    left: 50%;
    transform: translateX(-50%) translateY(0.5rem);
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 0.6rem 1.2rem;
    border-radius: 0.5rem;
    font-size: 0.85rem;
    z-index: 99;
    opacity: 0;
    transition: opacity 0.2s, transform 0.2s;
    pointer-events: none;
    white-space: nowrap;
    max-width: 88vw;
    text-align: center;
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  .toast.ok { border-color: var(--green); color: var(--green); }
  .toast.err { border-color: var(--red); color: var(--red); }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <div class="logo">&#127908; Karaoke</div>
    <div class="name-row">
      <input id="name" class="field" type="text" placeholder="Dein Name&#8230;" maxlength="30"
             oninput="localStorage.setItem('kname',this.value)">
    </div>
    <input id="search" class="field" type="search" placeholder="Song suchen&#8230;" oninput="filterSongs()" style="display:none">
  </div>

  <div class="tabs">
    <div id="t-songs" class="tab on" onclick="showTab('songs')">&#127925; Songs</div>
    <div id="t-queue" class="tab" onclick="showTab('queue')">&#9654; Warteschlange</div>
  </div>

  <div id="p-songs" class="pane">
    <div class="empty">Wird geladen&#8230;</div>
  </div>
  <div id="p-queue" class="pane hidden"></div>
</div>

<div id="toast" class="toast"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
const socket = io();

// Stable guest ID stored in localStorage
let gid = localStorage.getItem('kgid');
if (!gid) { gid = Date.now().toString(36) + Math.random().toString(36).slice(2); localStorage.setItem('kgid', gid); }

let allSongs = [];
let state = { now_playing: null, queue: [], settings: { song_limit: 2, queue_locked: false } };
let activeTab = 'songs';

// ── Library ──────────────────────────────────────────────────────────────────
fetch('/library').then(r => r.json()).then(songs => {
  allSongs = songs;
  renderSongs(songs);
  document.getElementById('search').style.display = '';
});

function filterSongs() {
  const q = document.getElementById('search').value.toLowerCase();
  renderSongs(q ? allSongs.filter(s => s.display.toLowerCase().includes(q)) : allSongs);
}

function renderSongs(songs) {
  const el = document.getElementById('p-songs');
  if (!songs.length) { el.innerHTML = '<div class="empty">Keine Songs gefunden.</div>'; return; }
  const icons = { cdg: '&#127908;', video: '&#127916;', audio: '&#127925;' };
  el.innerHTML = songs.map(s => {
    const d = JSON.stringify(s).replace(/'/g, '&#39;');
    return `<div class="song-row" onclick='tap(${d})'>
      <span class="song-icon">${icons[s.type]}</span>
      <span class="song-name">${esc(s.display)}</span>
      <span class="song-tag">${s.type.toUpperCase()}</span>
      <button class="add-btn" onclick='event.stopPropagation();tap(${d})'>+</button>
    </div>`;
  }).join('');
}

function tap(song) {
  const name = document.getElementById('name').value.trim();
  if (!name) { toast('Bitte erst deinen Namen eingeben.', 'err'); return; }
  if (state.settings.queue_locked) { toast('Warteschlange ist gerade geschlossen.', 'err'); return; }
  const mine = state.queue.filter(i => i.guest_id === gid).length;
  if (mine >= state.settings.song_limit) {
    toast('Limit erreicht – max. ' + state.settings.song_limit + ' Songs.', 'err');
    return;
  }
  socket.emit('queue_add', { song, singer: name, guest_id: gid });
}

// ── Queue tab ────────────────────────────────────────────────────────────────
function renderQueue() {
  const el = document.getElementById('p-queue');
  const { now_playing: np, queue, settings } = state;
  let html = '';

  if (settings.queue_locked)
    html += '<div class="locked-msg">&#128274; Warteschlange vorübergehend geschlossen.</div>';

  if (np)
    html += `<div class="now-card">
      <div class="now-label">Spielt gerade</div>
      <div class="now-title">&#9654; ${esc(np.song.display)}</div>
      <div class="now-singer">&#127908; ${esc(np.singer)}</div>
    </div>`;

  if (!queue.length) {
    html += '<div class="empty">' + (np ? 'Keine weiteren Songs in der Queue.' : 'Queue ist leer.') + '</div>';
    el.innerHTML = html; return;
  }
  html += queue.map((it, i) => `<div class="q-item">
    <div class="q-pos">${i + 1}</div>
    <div class="q-info">
      <div class="q-singer">&#127908; ${esc(it.singer)}</div>
      <div class="q-song">${esc(it.song.display)}</div>
    </div>
  </div>`).join('');
  el.innerHTML = html;
}

function updateQueueBadge() {
  const n = state.queue.length + (state.now_playing ? 1 : 0);
  const t = document.getElementById('t-queue');
  t.innerHTML = '&#9654; Warteschlange' + (n ? `<span class="badge">${n}</span>` : '');
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
function showTab(t) {
  activeTab = t;
  document.getElementById('t-songs').classList.toggle('on', t === 'songs');
  document.getElementById('t-queue').classList.toggle('on', t === 'queue');
  document.getElementById('p-songs').classList.toggle('hidden', t !== 'songs');
  document.getElementById('p-queue').classList.toggle('hidden', t !== 'queue');
}

// ── Socket ───────────────────────────────────────────────────────────────────
socket.on('connect', () => socket.emit('get_state'));

socket.on('state', s => { state = s; renderQueue(); updateQueueBadge(); });

socket.on('queue_update', s => {
  state = s;
  renderQueue();
  updateQueueBadge();
});

socket.on('add_result', r => {
  if (r.ok) { toast('Song wurde hinzugefügt!', 'ok'); showTab('queue'); }
  else toast(r.error, 'err');
});

// ── Toast ────────────────────────────────────────────────────────────────────
let toastT;
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  clearTimeout(toastT);
  toastT = setTimeout(() => el.className = 'toast', 2800);
}

function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

document.getElementById('name').value = localStorage.getItem('kname') || '';
</script>
</body>
</html>"""

# ── Admin Interface ────────────────────────────────────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin – Karaoke</title>
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0a0a12;
    --surface: #13131f;
    --border: #1e1e30;
    --accent: #7c3aed;
    --text: #e2e2f0;
    --muted: #4a4a6a;
    --green: #22c55e;
    --amber: #f59e0b;
    --blue: #3b82f6;
    --red: #ef4444;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    overflow: hidden;
  }

  /* Layout */
  .root { display: flex; flex-direction: column; height: 100vh; }
  .topbar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 1.25rem;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .topbar-logo {
    font-size: 1.1rem;
    font-weight: 800;
    background: linear-gradient(120deg, #f472b6, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .chip {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    background: var(--accent);
    color: #fff;
    letter-spacing: 0.05em;
  }
  .topbar a {
    margin-left: auto;
    font-size: 0.78rem;
    color: var(--muted);
    text-decoration: none;
    padding: 0.3rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: 0.4rem;
    transition: color 0.15s, border-color 0.15s;
  }
  .topbar a:hover { color: #a78bfa; border-color: var(--accent); }

  .body { display: flex; flex: 1; overflow: hidden; }

  /* Left column – queue */
  .col-queue {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
    border-right: 1px solid var(--border);
  }

  /* Now Playing */
  .now-wrap {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .now-empty { color: var(--muted); font-size: 0.84rem; padding: 0.3rem 0; }
  .now-card {
    background: #0b1f10;
    border: 1.5px solid var(--green);
    border-radius: 0.65rem;
    padding: 0.85rem 1rem;
  }
  .now-song { font-size: 1rem; font-weight: 700; color: var(--green); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .now-singer { font-size: 0.78rem; color: #86efac; margin-top: 0.2rem; }
  .controls { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.7rem; flex-wrap: wrap; }
  .btn {
    padding: 0.38rem 0.85rem;
    border: none;
    border-radius: 0.4rem;
    font-size: 0.8rem;
    font-weight: 700;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.1s;
  }
  .btn:active { transform: scale(0.96); }
  .btn:hover { opacity: 0.85; }
  .btn-play  { background: var(--green); color: #000; }
  .btn-pause { background: var(--blue);  color: #fff; }
  .btn-skip  { background: var(--amber); color: #000; }
  .btn-key   { background: #2a2a40; color: var(--text); padding: 0.38rem 0.55rem; min-width: 2rem; }
  .key-val   { font-size: 0.78rem; color: var(--muted); min-width: 2.2rem; text-align: center; }

  /* Queue list */
  .queue-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.6rem 1rem;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .queue-header h2 { font-size: 0.72rem; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; }
  .q-count { font-size: 0.72rem; font-weight: 700; color: var(--accent); }
  .queue-scroll { flex: 1; overflow-y: auto; padding: 0.5rem 0.75rem; }

  .q-item {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.6rem 0.75rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.6rem;
    margin-bottom: 0.3rem;
    transition: border-color 0.15s;
  }
  .q-item:hover { border-color: #3a3a60; }
  .sortable-ghost { opacity: 0.3; background: #2a2a40; }
  .drag-handle {
    color: var(--muted);
    font-size: 1rem;
    cursor: grab;
    flex-shrink: 0;
    user-select: none;
    padding: 0.1rem 0.15rem;
  }
  .drag-handle:active { cursor: grabbing; }
  .q-info { flex: 1; min-width: 0; }
  .q-singer-wrap { display: flex; align-items: center; gap: 0.3rem; }
  .q-singer {
    font-size: 0.73rem;
    font-weight: 600;
    color: #a78bfa;
    cursor: pointer;
    border-bottom: 1px dashed transparent;
    transition: border-color 0.15s;
  }
  .q-singer:hover { border-color: var(--accent); }
  .q-singer-input {
    font-size: 0.73rem;
    font-weight: 600;
    color: #a78bfa;
    background: transparent;
    border: none;
    border-bottom: 1.5px solid var(--accent);
    outline: none;
    width: 100%;
  }
  .q-song { font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 0.1rem; }
  .q-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }
  .icon-btn {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.3rem 0.35rem;
    border-radius: 0.35rem;
    font-size: 0.9rem;
    line-height: 1;
    transition: background 0.15s;
    color: var(--muted);
  }
  .icon-btn:hover { background: var(--border); }
  .btn-top:hover { color: var(--blue); }
  .btn-del:hover { color: var(--red); }
  .q-empty { text-align: center; color: var(--muted); padding: 3rem; font-size: 0.88rem; }

  /* Right column – sidebar */
  .col-side {
    width: 280px;
    flex-shrink: 0;
    overflow-y: auto;
    padding: 0.85rem;
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    padding: 0.9rem;
  }
  .card h3 {
    font-size: 0.7rem;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 0.8rem;
  }

  /* Settings */
  .setting {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .setting:last-child { margin-bottom: 0; }
  .setting-label { font-size: 0.82rem; }
  .setting-label small { display: block; font-size: 0.7rem; color: var(--muted); margin-top: 0.1rem; }
  .num-field {
    width: 56px;
    padding: 0.32rem 0.45rem;
    background: var(--bg);
    border: 1.5px solid var(--border);
    border-radius: 0.4rem;
    color: var(--text);
    font-size: 0.85rem;
    text-align: center;
    outline: none;
  }
  .num-field:focus { border-color: var(--accent); }
  .toggle { position: relative; display: inline-block; width: 40px; height: 22px; flex-shrink: 0; }
  .toggle input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute; inset: 0;
    background: var(--border);
    border-radius: 11px;
    cursor: pointer;
    transition: background 0.2s;
  }
  .slider::before {
    content: '';
    position: absolute;
    width: 16px; height: 16px;
    left: 3px; top: 3px;
    background: #fff;
    border-radius: 50%;
    transition: transform 0.2s;
  }
  input:checked + .slider { background: var(--accent); }
  input:checked + .slider::before { transform: translateX(18px); }

  /* Upload */
  .dropzone {
    border: 2px dashed var(--border);
    border-radius: 0.6rem;
    padding: 1rem;
    text-align: center;
    cursor: pointer;
    font-size: 1.5rem;
    transition: border-color 0.2s, background 0.2s;
  }
  .dropzone:hover, .dropzone.over { border-color: var(--accent); background: #1a1a2e; }
  .dropzone p { font-size: 0.73rem; color: var(--muted); margin-top: 0.3rem; }
  .prog-wrap { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; margin: 0.4rem 0; }
  .prog-bar { height: 100%; background: linear-gradient(90deg, #a78bfa, #60a5fa); width: 0; transition: width 0.2s; }
  .upload-log { font-size: 0.72rem; color: var(--muted); max-height: 80px; overflow-y: auto; }
  .upload-log div { padding: 0.15rem 0; border-bottom: 1px solid var(--bg); display: flex; justify-content: space-between; gap: 0.5rem; }
</style>
</head>
<body>
<div class="root">
  <div class="topbar">
    <span class="topbar-logo">&#127908; Karaoke</span>
    <span class="chip">ADMIN</span>
    <a href="/" target="_blank">&#128100; Gäste-Ansicht</a>
  </div>

  <div class="body">
    <!-- Queue column -->
    <div class="col-queue">
      <div class="now-wrap" id="now-wrap">
        <div class="now-empty">&#9899; Nichts spielt gerade.</div>
      </div>
      <div class="queue-header">
        <h2>Warteschlange</h2>
        <span class="q-count" id="q-count"></span>
      </div>
      <div class="queue-scroll" id="queue-scroll">
        <div class="q-empty">Warteschlange ist leer.</div>
      </div>
    </div>

    <!-- Sidebar -->
    <div class="col-side">
      <div class="card">
        <h3>Einstellungen</h3>
        <div class="setting">
          <div class="setting-label">
            Songs-Limit pro Gast
            <small>Max. Einträge gleichzeitig</small>
          </div>
          <input type="number" class="num-field" id="s-limit" min="1" max="20" value="2" onchange="saveSetting()">
        </div>
        <div class="setting">
          <div class="setting-label">
            Queue sperren
            <small>Gäste können nicht hinzufügen</small>
          </div>
          <label class="toggle">
            <input type="checkbox" id="s-locked" onchange="saveSetting()">
            <span class="slider"></span>
          </label>
        </div>
      </div>

      <div class="card">
        <h3>Songs hochladen</h3>
        <div class="dropzone" id="dz"
             onclick="document.getElementById('fi').click()"
             ondragover="event.preventDefault();this.classList.add('over')"
             ondragleave="this.classList.remove('over')"
             ondrop="onDrop(event)">
          &#128190;
          <p>MP3 + CDG &nbsp;/&nbsp; MP4<br>Klicken oder Drag &amp; Drop</p>
          <input type="file" id="fi" multiple accept=".mp3,.cdg,.mp4,.webm,.m4v,.wav" style="display:none" onchange="uploadAll(this.files)">
        </div>
        <div class="prog-wrap"><div class="prog-bar" id="pbar"></div></div>
        <div class="upload-log" id="ulog"></div>
      </div>
    </div>
  </div>
</div>

<script>
const socket = io();
let state = { now_playing: null, queue: [], settings: { song_limit: 2, queue_locked: false } };
let keyOffset = 0;
let sortable = null;

socket.on('connect',      () => socket.emit('get_state'));
socket.on('state',        s  => { state = s; render(); });
socket.on('queue_update', s  => { state = s; render(); });

function render() {
  renderNow();
  renderQueue();
  renderSettings();
}

// ── Now Playing ───────────────────────────────────────────────────────────────
function renderNow() {
  const np = state.now_playing;
  const el = document.getElementById('now-wrap');
  if (!np) { el.innerHTML = '<div class="now-empty">&#9899; Nichts spielt gerade.</div>'; return; }
  el.innerHTML = `
    <div class="now-card">
      <div class="now-song">&#9654; ${esc(np.song.display)}</div>
      <div class="now-singer">&#127908; ${esc(np.singer)}</div>
      <div class="controls">
        <button class="btn btn-play"  onclick="socket.emit('play')">&#9654; Play</button>
        <button class="btn btn-pause" onclick="socket.emit('pause')">&#9646;&#9646; Pause</button>
        <button class="btn btn-skip"  onclick="socket.emit('skip')">&#9197; Skip</button>
        <button class="btn btn-key"   onclick="changeKey(-1)">&#9660;</button>
        <span class="key-val" id="key-val">${fmt(keyOffset)}</span>
        <button class="btn btn-key"   onclick="changeKey(1)">&#9650;</button>
      </div>
    </div>`;
}

// ── Queue ─────────────────────────────────────────────────────────────────────
function renderQueue() {
  const el = document.getElementById('queue-scroll');
  const qs = state.queue;
  document.getElementById('q-count').textContent = qs.length ? '(' + qs.length + ')' : '';

  if (!qs.length) {
    el.innerHTML = '<div class="q-empty">Warteschlange ist leer.</div>';
    if (sortable) { sortable.destroy(); sortable = null; }
    return;
  }

  el.innerHTML = qs.map(it => `
    <div class="q-item" data-id="${it.id}">
      <span class="drag-handle">&#8801;</span>
      <div class="q-info">
        <div class="q-singer-wrap">
          <span class="q-singer" onclick="editSinger(this,'${it.id}')">${esc(it.singer)}</span>
        </div>
        <div class="q-song">${esc(it.song.display)}</div>
      </div>
      <div class="q-actions">
        <button class="icon-btn btn-top" title="An erste Stelle" onclick="moveTop('${it.id}')">&#11014;</button>
        <button class="icon-btn btn-del" title="Entfernen"       onclick="remove('${it.id}')">&#10005;</button>
      </div>
    </div>`).join('');

  if (sortable) sortable.destroy();
  sortable = Sortable.create(el, {
    animation: 150,
    handle: '.drag-handle',
    ghostClass: 'sortable-ghost',
    onEnd() {
      const order = [...el.querySelectorAll('[data-id]')].map(n => n.dataset.id);
      socket.emit('queue_reorder', { order });
    }
  });
}

// ── Settings ──────────────────────────────────────────────────────────────────
function renderSettings() {
  document.getElementById('s-limit').value  = state.settings.song_limit;
  document.getElementById('s-locked').checked = state.settings.queue_locked;
}

function saveSetting() {
  socket.emit('settings_update', {
    song_limit:   parseInt(document.getElementById('s-limit').value) || 2,
    queue_locked: document.getElementById('s-locked').checked
  });
}

// ── Controls ──────────────────────────────────────────────────────────────────
function changeKey(d) {
  keyOffset += d;
  const el = document.getElementById('key-val');
  if (el) el.textContent = fmt(keyOffset);
  socket.emit('key_change', { offset: keyOffset });
}

function moveTop(id)  { socket.emit('queue_move_top',  { id }); }
function remove(id)   { socket.emit('queue_remove',    { id }); }

function editSinger(el, id) {
  const old = el.textContent;
  const inp = document.createElement('input');
  inp.className = 'q-singer-input';
  inp.value = old;
  el.replaceWith(inp);
  inp.focus();
  inp.select();
  const done = () => socket.emit('queue_edit_singer', { id, singer: inp.value.trim() || old });
  inp.addEventListener('blur', done);
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); if (e.key === 'Escape') { inp.value = old; inp.blur(); } });
}

// ── Upload ────────────────────────────────────────────────────────────────────
function onDrop(e) {
  e.preventDefault();
  document.getElementById('dz').classList.remove('over');
  uploadAll(e.dataTransfer.files);
}

function uploadAll(files) { [...files].forEach(uploadOne); }

async function uploadOne(file) {
  const log = document.getElementById('ulog');
  const row = document.createElement('div');
  row.innerHTML = `<span>${file.name.slice(0, 28)}</span><span>&#8230;</span>`;
  log.prepend(row);
  const fd = new FormData(); fd.append('file', file);
  const xhr = new XMLHttpRequest(); xhr.open('POST', '/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) document.getElementById('pbar').style.width = (e.loaded / e.total * 100) + '%';
  };
  await new Promise((ok, fail) => { xhr.onload = () => xhr.status === 200 ? ok() : fail(); xhr.onerror = fail; xhr.send(fd); });
  const ok = xhr.status === 200;
  row.querySelector('span:last-child').textContent = ok ? '&#10003;' : '&#10007;';
  row.querySelector('span:last-child').style.color = ok ? '#22c55e' : '#ef4444';
  document.getElementById('pbar').style.width = '0%';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmt(n) { return (n >= 0 ? '+' : '') + n; }
</script>
</body>
</html>"""

# ── Player Interface ───────────────────────────────────────────────────────────

PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Karaoke Player</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; background: #000; overflow: hidden; }

  #standby {
    position: fixed; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    background: #000;
    font-family: 'Segoe UI', sans-serif;
  }
  #standby h1 {
    font-size: 6rem;
    font-weight: 900;
    letter-spacing: 0.15em;
    background: linear-gradient(135deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  #sb-now  { font-size: 1.1rem; color: #22c55e; margin-top: 1.5rem; }
  #sb-next { font-size: 0.85rem; color: #333; margin-top: 0.5rem; }

  #cdg-wrap { display: none; position: fixed; inset: 0; background: #000; align-items: center; justify-content: center; }
  #cdg-wrap.on { display: flex; }
  canvas { image-rendering: pixelated; image-rendering: crisp-edges; display: block; }

  #vid-wrap { display: none; position: fixed; inset: 0; background: #000; }
  #vid-wrap.on { display: block; }
  video { width: 100%; height: 100%; object-fit: contain; }

  audio { position: fixed; bottom: 0; left: 0; right: 0; width: 100%; display: none; }
  audio.on { display: block; }

  .hud {
    position: fixed;
    bottom: 0.6rem;
    font-family: monospace;
    font-size: 0.72rem;
    color: rgba(255,255,255,0.2);
    pointer-events: none;
  }
  #hud-sync { left: 0.8rem; }
  #hud-key  { right: 0.8rem; }
</style>
</head>
<body>

<div id="standby">
  <h1>KARAOKE</h1>
  <div id="sb-now"></div>
  <div id="sb-next"></div>
</div>

<div id="cdg-wrap"><canvas id="cdg" width="300" height="216"></canvas></div>
<div id="vid-wrap"><video id="vid" preload="auto"></video></div>
<audio id="aud" preload="auto" controls></audio>

<div class="hud" id="hud-sync"></div>
<div class="hud" id="hud-key"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
// ── CDG Decoder ───────────────────────────────────────────────────────────────
class CDGPlayer {
  constructor(canvas) {
    this.cv  = canvas;
    this.ctx = canvas.getContext('2d');
    this.reset();
  }

  reset() {
    this.pkts     = [];
    this.pixels   = new Uint8Array(300 * 216);
    this.clr      = new Uint32Array(16);
    this.img      = this.ctx.createImageData(300, 216);
    this.idx      = 0;
    this.ctx.fillStyle = '#000';
    this.ctx.fillRect(0, 0, 300, 216);
  }

  load(buf) {
    this.reset();
    const u8 = new Uint8Array(buf);
    for (let i = 0; i + 24 <= u8.length; i += 24)
      this.pkts.push({ cmd: u8[i] & 0x3F, inst: u8[i+1] & 0x3F, data: u8.slice(i+4, i+20) });
  }

  seek(sec) {
    const t = Math.floor(sec * 75);
    if (t < this.idx) { this.pixels.fill(0); this.clr.fill(0); this.idx = 0; }
    let dirty = false;
    while (this.idx < t && this.idx < this.pkts.length) {
      const p = this.pkts[this.idx++];
      if (p.cmd === 0x09) { this.exec(p); dirty = true; }
    }
    if (dirty) this.paint();
  }

  exec({ inst, data: d }) {
    switch (inst) {
      case 0x01: // Memory preset
        if ((d[1] & 0xF) === 0) this.pixels.fill(d[0] & 0xF);
        break;
      case 0x02: { // Border preset
        const c = d[0] & 0xF;
        for (let x = 0; x < 300; x++) {
          for (let y = 0;   y < 12;  y++) this.pixels[y*300+x] = c;
          for (let y = 204; y < 216; y++) this.pixels[y*300+x] = c;
        }
        for (let y = 12; y < 204; y++) {
          for (let x = 0;   x < 6;   x++) this.pixels[y*300+x] = c;
          for (let x = 294; x < 300; x++) this.pixels[y*300+x] = c;
        }
        break;
      }
      case 0x06: case 0x26: { // Tile block (normal / XOR)
        const c0 = d[0] & 0xF, c1 = d[1] & 0xF;
        const row = (d[2] & 0x1F) * 12, col = (d[3] & 0x3F) * 6;
        const xor = inst === 0x26;
        for (let r = 0; r < 12; r++) {
          const bits = d[4+r] & 0x3F;
          for (let c = 0; c < 6; c++) {
            const bit = (bits >> (5-c)) & 1, i = (row+r)*300 + (col+c);
            this.pixels[i] = xor ? this.pixels[i] ^ (bit ? c1 : c0) : (bit ? c1 : c0);
          }
        }
        break;
      }
      case 0x1E: case 0x1F: { // Load colour table
        const off = inst === 0x1F ? 8 : 0;
        for (let i = 0; i < 8; i++) {
          const hi = d[i*2] & 0x3F, lo = d[i*2+1] & 0x3F;
          const r = ((hi >> 2) & 0xF) * 17;
          const g = (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17;
          const b = (lo & 0xF) * 17;
          this.clr[off+i] = (255 << 24) | (b << 16) | (g << 8) | r;
        }
        break;
      }
      case 0x14: case 0x15: { // Scroll
        const vCmd = (d[2] >> 4) & 3, fill = inst === 0x14, color = d[0] & 0xF;
        const tmp = new Uint8Array(this.pixels);
        if (vCmd === 2) {
          for (let y = 0; y < 204; y++) this.pixels.copyWithin(y*300, (y+12)*300, (y+13)*300);
          for (let y = 204; y < 216; y++) this.pixels.fill(fill ? color : tmp[y*300], y*300, (y+1)*300);
        } else if (vCmd === 1) {
          for (let y = 215; y >= 12; y--) this.pixels.copyWithin(y*300, (y-12)*300, (y-11)*300);
          for (let y = 0; y < 12; y++) this.pixels.fill(fill ? color : tmp[(204+y)*300], y*300, (y+1)*300);
        }
        break;
      }
    }
  }

  paint() {
    const px = this.img.data, clr = this.clr, buf = this.pixels;
    for (let i = 0; i < buf.length; i++) {
      const rgb = clr[buf[i]];
      px[i*4]   = rgb & 0xFF;
      px[i*4+1] = (rgb >> 8)  & 0xFF;
      px[i*4+2] = (rgb >> 16) & 0xFF;
      px[i*4+3] = 255;
    }
    this.ctx.putImageData(this.img, 0, 0);
  }
}

// ── Canvas scaling ────────────────────────────────────────────────────────────
const cv = document.getElementById('cdg');
function scaleCanvas() {
  const s = Math.min(innerWidth / 300, innerHeight / 216);
  cv.style.width  = (300 * s) + 'px';
  cv.style.height = (216 * s) + 'px';
}
addEventListener('resize', scaleCanvas); scaleCanvas();

// ── Player setup ──────────────────────────────────────────────────────────────
const socket = io({ transports: ['polling', 'websocket'] });
const aud    = document.getElementById('aud');
const vid    = document.getElementById('vid');
const cdgW   = document.getElementById('cdg-wrap');
const vidW   = document.getElementById('vid-wrap');
const stnby  = document.getElementById('standby');
const cdg    = new CDGPlayer(cv);
let   raf    = null;
let   sync   = 0;

function showStandby() {
  stnby.style.display = 'flex';
  cdgW.classList.remove('on');
  vidW.classList.remove('on');
  aud.classList.remove('on');
}

function cdgLoop() {
  cdg.seek(Math.max(0, aud.currentTime + sync));
  raf = requestAnimationFrame(cdgLoop);
}

function setSync(v) {
  sync = v;
  document.getElementById('hud-sync').textContent = sync !== 0 ? 'sync ' + (sync > 0 ? '+' : '') + sync.toFixed(1) + 's' : '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') setSync(parseFloat((sync + 0.5).toFixed(1)));
  if (e.key === 'ArrowLeft')  setSync(parseFloat((sync - 0.5).toFixed(1)));
});

// Notify server when song finishes so queue auto-advances
aud.addEventListener('ended', () => socket.emit('song_ended'));
vid.addEventListener('ended', () => socket.emit('song_ended'));

// Tell server this is a player so it can replay current song on reconnect
socket.on('connect', () => socket.emit('player_connect'));

// ── Socket events ─────────────────────────────────────────────────────────────
socket.on('load_song', async song => {
  cancelAnimationFrame(raf);
  aud.pause(); vid.pause();
  showStandby();
  setSync(0);

  if (song.type === 'cdg') {
    stnby.style.display = 'none';
    cdgW.classList.add('on');
    aud.classList.add('on');
    aud.src = '/files/' + encodeURIComponent(song.mp3);
    const buf = await fetch('/files/' + encodeURIComponent(song.cdg)).then(r => r.arrayBuffer());
    cdg.load(buf);
    raf = requestAnimationFrame(cdgLoop);
    aud.play().catch(() => {});

  } else if (song.type === 'video') {
    stnby.style.display = 'none';
    vidW.classList.add('on');
    vid.src = '/files/' + encodeURIComponent(song.video);
    vid.play().catch(() => {});

  } else {
    stnby.style.display = 'none';
    aud.classList.add('on');
    aud.src = '/files/' + encodeURIComponent(song.audio);
    aud.play().catch(() => {});
  }
});

socket.on('play',  () => { aud.play().catch(() => {}); vid.play().catch(() => {}); });
socket.on('pause', () => { aud.pause(); vid.pause(); });
socket.on('stop',  () => {
  cancelAnimationFrame(raf);
  aud.pause(); vid.pause();
  aud.src = ''; vid.src = '';
  showStandby();
  document.getElementById('sb-now').textContent  = '';
  document.getElementById('sb-next').textContent = '';
});

socket.on('key_change', ({ offset }) => {
  document.getElementById('hud-key').textContent = offset !== 0 ? 'key ' + (offset > 0 ? '+' : '') + offset : '';
});

socket.on('queue_update', ({ now_playing, queue }) => {
  if (stnby.style.display !== 'none') {
    document.getElementById('sb-now').textContent  = now_playing ? '▶ ' + now_playing.singer + ' – ' + now_playing.song.display : '';
    const next = queue[0];
    document.getElementById('sb-next').textContent = next ? 'Als nächstes: ' + next.singer + ' – ' + next.song.display : '';
  }
});
</script>
</body>
</html>"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def guest():
    return GUEST_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/admin')
def admin():
    return ADMIN_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/player')
def player():
    return PLAYER_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/library')
def library():
    return jsonify(get_library())

@app.route('/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Keine Datei'}), 400
    fn  = secure_filename(f.filename)
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ALLOWED:
        return jsonify({'error': f'Format nicht unterstützt: {ext}'}), 400
    f.save(os.path.join(LIBRARY_DIR, fn))
    sync_library()
    return jsonify({'ok': True, 'filename': fn})

@app.route('/files/<filename>')
def serve_file(filename):
    path = os.path.join(LIBRARY_DIR, secure_filename(filename))
    if not os.path.exists(path):
        return '', 404
    return send_file(path, conditional=True)

# ── WebSocket handlers ────────────────────────────────────────────────────────

@socketio.on('get_state')
def on_get_state():
    emit('state', get_state())

@socketio.on('player_connect')
def on_player_connect():
    if now_playing:
        emit('load_song', now_playing['song'])
    emit('queue_update', get_state())

@socketio.on('queue_add')
def on_queue_add(data):
    global now_playing
    if settings['queue_locked']:
        emit('add_result', {'ok': False, 'error': 'Warteschlange ist geschlossen.'})
        return
    gid   = data.get('guest_id', '')
    count = sum(1 for it in queue if it['guest_id'] == gid)
    if count >= settings['song_limit']:
        emit('add_result', {'ok': False, 'error': f'Limit von {settings["song_limit"]} Songs erreicht.'})
        return
    item = {
        'id':       str(uuid.uuid4()),
        'song':     data['song'],
        'singer':   str(data.get('singer', 'Gast'))[:30],
        'guest_id': gid,
    }
    queue.append(item)
    save_queue()
    emit('add_result', {'ok': True})
    if now_playing is None:
        advance_queue()
    else:
        socketio.emit('queue_update', get_state())

@socketio.on('queue_remove')
def on_queue_remove(data):
    queue[:] = [it for it in queue if it['id'] != data.get('id')]
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_reorder')
def on_queue_reorder(data):
    order  = data.get('order', [])
    by_id  = {it['id']: it for it in queue}
    queue[:] = [by_id[oid] for oid in order if oid in by_id]
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_move_top')
def on_queue_move_top(data):
    tid = data.get('id')
    idx = next((i for i, it in enumerate(queue) if it['id'] == tid), None)
    if idx:
        queue.insert(0, queue.pop(idx))
        save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_edit_singer')
def on_queue_edit_singer(data):
    tid  = data.get('id')
    name = str(data.get('singer', ''))[:30]
    for it in queue:
        if it['id'] == tid and name:
            it['singer'] = name
            break
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('settings_update')
def on_settings_update(data):
    settings['song_limit']   = max(1, int(data.get('song_limit', 2)))
    settings['queue_locked'] = bool(data.get('queue_locked', False))
    save_settings()
    socketio.emit('queue_update', get_state())

@socketio.on('skip')
def on_skip():
    advance_queue()

@socketio.on('song_ended')
def on_song_ended():
    advance_queue()

@socketio.on('play')
def on_play():
    socketio.emit('play', broadcast=True)

@socketio.on('pause')
def on_pause():
    socketio.emit('pause', broadcast=True)

@socketio.on('key_change')
def on_key_change(data):
    socketio.emit('key_change', data, broadcast=True)

# ── Startup ───────────────────────────────────────────────────────────────────

init_db()
sync_library()
load_state()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
