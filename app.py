import eventlet
eventlet.monkey_patch()

import os, glob, uuid, sqlite3
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', '/app/library')
DB_PATH     = os.environ.get('DB_PATH',     '/app/karaoke.db')
os.makedirs(LIBRARY_DIR, exist_ok=True)

AUDIO_EXT = {'.mp3', '.m4a', '.wav', '.ogg'}
VIDEO_EXT = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.m4v'}
ALLOWED   = AUDIO_EXT | VIDEO_EXT | {'.cdg'}

# ── Database (library only) ───────────────────────────────────────────────────

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS songs (
            base TEXT PRIMARY KEY, display TEXT NOT NULL, type TEXT NOT NULL,
            mp3 TEXT, cdg TEXT, video TEXT, audio TEXT
        )''')

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
        cdg  = files.get('.cdg')
        mp3  = files.get('.mp3') or next((files[e] for e in AUDIO_EXT if e in files), None)
        mp4  = next((files[e] for e in VIDEO_EXT if e in files), None)
        disp = base.replace('_', ' ').strip()
        if cdg and mp3:  rows.append((base, disp, 'cdg',   mp3, cdg,  None, None))
        elif mp4:        rows.append((base, disp, 'video', None, None, mp4,  None))
        elif mp3:        rows.append((base, disp, 'audio', mp3,  None, None, mp3))

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
        if   r['type'] == 'cdg':   out.append({'display': r['display'], 'type': 'cdg',   'mp3': r['mp3'], 'cdg': r['cdg']})
        elif r['type'] == 'video': out.append({'display': r['display'], 'type': 'video', 'video': r['video']})
        else:                      out.append({'display': r['display'], 'type': 'audio', 'audio': r['audio']})
    return out

# ── Queue state (in-memory) ───────────────────────────────────────────────────

queue       = []   # list of {id, song, singer}
now_playing = None

def get_state():
    return {'now_playing': now_playing, 'queue': queue}

def advance():
    global now_playing
    if queue:
        now_playing = queue.pop(0)
        socketio.emit('load_song', now_playing['song'])
    else:
        now_playing = None
        socketio.emit('stop')
    socketio.emit('queue_update', get_state())

# ── Guest page ────────────────────────────────────────────────────────────────

GUEST_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Karaoke</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a12;
    color: #e2e2f0;
    height: 100dvh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Header */
  header {
    background: #12121e;
    border-bottom: 1px solid #1e1e30;
    padding: .85rem 1rem .7rem;
    flex-shrink: 0;
  }
  header h1 {
    font-size: 1.15rem;
    font-weight: 800;
    background: linear-gradient(120deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: .55rem;
  }
  input.field {
    width: 100%;
    padding: .62rem .9rem;
    background: #0a0a12;
    border: 1.5px solid #1e1e30;
    border-radius: .55rem;
    color: #e2e2f0;
    font-size: .95rem;
    outline: none;
  }
  input.field:focus { border-color: #7c3aed; }
  input.field::placeholder { color: #3a3a52; }
  #search { margin-top: .45rem; }

  /* Tabs */
  nav {
    display: flex;
    border-bottom: 1px solid #1e1e30;
    flex-shrink: 0;
  }
  nav button {
    flex: 1;
    padding: .7rem .5rem;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: #44445a;
    font-size: .83rem;
    font-weight: 600;
    cursor: pointer;
  }
  nav button.active { color: #a78bfa; border-color: #7c3aed; }
  .badge {
    display: inline-block;
    background: #7c3aed;
    color: #fff;
    font-size: .63rem;
    padding: .08rem .38rem;
    border-radius: 999px;
    margin-left: .25rem;
    vertical-align: middle;
  }

  /* Panels */
  .panel { flex: 1; overflow-y: auto; padding: .65rem; }
  .panel.hidden { display: none; }

  /* Song rows */
  .song {
    display: flex;
    align-items: center;
    gap: .6rem;
    padding: .75rem .9rem;
    background: #12121e;
    border: 1px solid #1e1e30;
    border-radius: .6rem;
    margin-bottom: .32rem;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .song:active { border-color: #7c3aed; background: #18182a; }
  .song-name { flex: 1; font-size: .9rem; font-weight: 500; }
  .song-tag {
    font-size: .63rem;
    color: #44445a;
    background: #1e1e30;
    padding: .12rem .4rem;
    border-radius: .3rem;
  }
  .add-btn {
    padding: .38rem .8rem;
    background: #7c3aed;
    color: #fff;
    border: none;
    border-radius: .4rem;
    font-size: .8rem;
    font-weight: 700;
    cursor: pointer;
    flex-shrink: 0;
  }

  /* Queue */
  .now-card {
    background: #0a1c0e;
    border: 1.5px solid #22c55e;
    border-radius: .65rem;
    padding: .85rem 1rem;
    margin-bottom: .5rem;
  }
  .now-label { font-size: .66rem; font-weight: 700; color: #22c55e; text-transform: uppercase; letter-spacing: .07em; }
  .now-title { font-size: .95rem; font-weight: 700; color: #22c55e; margin-top: .2rem; }
  .now-singer { font-size: .78rem; color: #86efac; margin-top: .15rem; }

  .q-row {
    display: flex;
    align-items: center;
    gap: .55rem;
    padding: .65rem .85rem;
    background: #12121e;
    border: 1px solid #1e1e30;
    border-radius: .6rem;
    margin-bottom: .32rem;
  }
  .q-num { font-size: 1.05rem; font-weight: 800; color: #7c3aed; min-width: 1.5rem; text-align: center; }
  .q-info { flex: 1; min-width: 0; }
  .q-singer { font-size: .72rem; font-weight: 600; color: #a78bfa; }
  .q-song { font-size: .86rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .empty { text-align: center; color: #44445a; padding: 3rem 1rem; font-size: .9rem; }

  /* Toast */
  #toast {
    position: fixed;
    bottom: 1.5rem;
    left: 50%;
    transform: translateX(-50%) translateY(.4rem);
    background: #12121e;
    border: 1px solid #1e1e30;
    color: #e2e2f0;
    padding: .58rem 1.2rem;
    border-radius: .5rem;
    font-size: .85rem;
    z-index: 99;
    opacity: 0;
    transition: opacity .2s, transform .2s;
    pointer-events: none;
    white-space: nowrap;
  }
  #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  #toast.err  { border-color: #ef4444; color: #ef4444; }
  #toast.ok   { border-color: #22c55e; color: #22c55e; }
</style>
</head>
<body>

<header>
  <h1>&#127908; Karaoke</h1>
  <input id="name" class="field" type="text" placeholder="Dein Name&#8230;" maxlength="30"
         oninput="localStorage.setItem('kname', this.value)">
  <input id="search" class="field" type="search" placeholder="Song suchen&#8230;"
         oninput="filterSongs()" style="display:none">
</header>

<nav>
  <button id="btn-songs" class="active" onclick="showTab('songs')">&#127925; Songs</button>
  <button id="btn-queue" onclick="showTab('queue')">&#9654; Warteschlange</button>
</nav>

<div id="panel-songs" class="panel">
  <div class="empty">Lädt&#8230;</div>
</div>
<div id="panel-queue" class="panel hidden"></div>

<div id="toast"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
const socket = io();
let allSongs = [];
let state = { now_playing: null, queue: [] };

// Load library once
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
  const el = document.getElementById('panel-songs');
  if (!songs.length) { el.innerHTML = '<div class="empty">Keine Songs gefunden.</div>'; return; }
  const icons = { cdg: '&#127908;', video: '&#127916;', audio: '&#127925;' };
  el.innerHTML = songs.map(s => {
    const d = JSON.stringify(s).replace(/'/g, '&#39;');
    return `<div class="song" onclick='addSong(${d})'>
      <span style="font-size:1.1rem;flex-shrink:0">${icons[s.type]}</span>
      <span class="song-name">${esc(s.display)}</span>
      <span class="song-tag">${s.type.toUpperCase()}</span>
      <button class="add-btn" onclick="event.stopPropagation();addSong(${d})">+</button>
    </div>`;
  }).join('');
}

function addSong(song) {
  const name = document.getElementById('name').value.trim();
  if (!name) { toast('Bitte erst deinen Namen eingeben.', 'err'); return; }
  socket.emit('queue_add', { song, singer: name });
}

// Socket
socket.on('connect', () => socket.emit('get_state'));
socket.on('state',       s => { state = s; renderQueue(); updateBadge(); });
socket.on('queue_update',s => { state = s; renderQueue(); updateBadge(); });
socket.on('add_result',  r => {
  if (r.ok) { toast('Song hinzugefügt!', 'ok'); showTab('queue'); }
  else toast(r.error, 'err');
});

function renderQueue() {
  const el = document.getElementById('panel-queue');
  const { now_playing: np, queue } = state;
  let html = '';
  if (np) html += `<div class="now-card">
    <div class="now-label">Spielt gerade</div>
    <div class="now-title">&#9654; ${esc(np.song.display)}</div>
    <div class="now-singer">&#127908; ${esc(np.singer)}</div>
  </div>`;
  if (!queue.length) {
    html += '<div class="empty">' + (np ? 'Keine weiteren Songs.' : 'Queue ist leer.') + '</div>';
  } else {
    html += queue.map((it, i) => `<div class="q-row">
      <div class="q-num">${i + 1}</div>
      <div class="q-info">
        <div class="q-singer">&#127908; ${esc(it.singer)}</div>
        <div class="q-song">${esc(it.song.display)}</div>
      </div>
    </div>`).join('');
  }
  el.innerHTML = html;
}

function updateBadge() {
  const n = state.queue.length + (state.now_playing ? 1 : 0);
  const btn = document.getElementById('btn-queue');
  btn.innerHTML = '&#9654; Warteschlange' + (n ? `<span class="badge">${n}</span>` : '');
}

function showTab(t) {
  document.getElementById('btn-songs').classList.toggle('active', t === 'songs');
  document.getElementById('btn-queue').classList.toggle('active', t === 'queue');
  document.getElementById('panel-songs').classList.toggle('hidden', t !== 'songs');
  document.getElementById('panel-queue').classList.toggle('hidden', t !== 'queue');
}

let toastTimer;
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = '', 2800);
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

document.getElementById('name').value = localStorage.getItem('kname') || '';
</script>
</body>
</html>"""

# ── Admin page ────────────────────────────────────────────────────────────────

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
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a12;
    color: #e2e2f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Top bar */
  header {
    display: flex;
    align-items: center;
    gap: .75rem;
    padding: .6rem 1.25rem;
    background: #12121e;
    border-bottom: 1px solid #1e1e30;
    flex-shrink: 0;
  }
  header h1 {
    font-size: 1.05rem;
    font-weight: 800;
    background: linear-gradient(120deg, #f472b6, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .chip {
    font-size: .62rem; font-weight: 700;
    padding: .18rem .5rem; border-radius: 999px;
    background: #7c3aed; color: #fff;
  }
  header a {
    margin-left: auto;
    font-size: .76rem; color: #44445a; text-decoration: none;
    padding: .28rem .6rem;
    border: 1px solid #1e1e30; border-radius: .38rem;
  }
  header a:hover { color: #a78bfa; border-color: #7c3aed; }

  /* Layout */
  .layout { display: flex; flex: 1; overflow: hidden; }

  /* Left – queue */
  .col-main {
    display: flex; flex-direction: column;
    flex: 1; overflow: hidden;
    border-right: 1px solid #1e1e30;
  }

  /* Now playing */
  #now-playing {
    padding: .75rem 1rem;
    border-bottom: 1px solid #1e1e30;
    flex-shrink: 0;
  }
  .np-empty { color: #44445a; font-size: .84rem; }
  .np-card {
    background: #0a1c0e;
    border: 1.5px solid #22c55e;
    border-radius: .65rem;
    padding: .8rem 1rem;
  }
  .np-song { font-size: 1rem; font-weight: 700; color: #22c55e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .np-singer { font-size: .77rem; color: #86efac; margin-top: .2rem; }
  .controls { display: flex; align-items: center; gap: .38rem; margin-top: .65rem; flex-wrap: wrap; }

  .btn {
    padding: .36rem .82rem;
    border: none; border-radius: .4rem;
    font-size: .78rem; font-weight: 700;
    cursor: pointer;
  }
  .btn:hover { opacity: .85; }
  .btn:active { transform: scale(.95); }
  .btn-play  { background: #22c55e; color: #000; }
  .btn-pause { background: #3b82f6; color: #fff; }
  .btn-skip  { background: #f59e0b; color: #000; }
  .btn-key   { background: #1e1e30; color: #e2e2f0; padding: .36rem .55rem; min-width: 1.9rem; }
  .key-val   { font-size: .76rem; color: #44445a; min-width: 2rem; text-align: center; }

  /* Queue list */
  .queue-header {
    display: flex; align-items: center; gap: .5rem;
    padding: .55rem 1rem;
    border-bottom: 1px solid #1e1e30;
    flex-shrink: 0;
  }
  .queue-header h2 { font-size: .7rem; font-weight: 700; color: #44445a; text-transform: uppercase; letter-spacing: .07em; }
  .q-count { font-size: .7rem; font-weight: 700; color: #7c3aed; }

  #queue-list { flex: 1; overflow-y: auto; padding: .45rem .7rem; }

  .q-item {
    display: flex; align-items: center; gap: .5rem;
    padding: .58rem .72rem;
    background: #12121e;
    border: 1px solid #1e1e30;
    border-radius: .58rem;
    margin-bottom: .28rem;
  }
  .q-item:hover { border-color: #2a2a42; }
  .sortable-ghost { opacity: .25; }
  .handle { color: #44445a; cursor: grab; flex-shrink: 0; font-size: 1rem; padding: .1rem; user-select: none; }
  .handle:active { cursor: grabbing; }
  .q-info { flex: 1; min-width: 0; }
  .q-singer-text {
    font-size: .71rem; font-weight: 600; color: #a78bfa;
    cursor: pointer; display: inline;
    border-bottom: 1px dashed transparent;
  }
  .q-singer-text:hover { border-color: #7c3aed; }
  .q-singer-input {
    font-size: .71rem; font-weight: 600; color: #a78bfa;
    background: transparent; border: none;
    border-bottom: 1.5px solid #7c3aed;
    outline: none; width: 100%;
  }
  .q-song-name { font-size: .83rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: .08rem; }
  .q-actions { display: flex; gap: .28rem; flex-shrink: 0; }
  .icon-btn {
    background: none; border: none; cursor: pointer;
    padding: .28rem .32rem; border-radius: .32rem;
    font-size: .9rem; line-height: 1; color: #44445a;
  }
  .icon-btn:hover { background: #1e1e30; }
  .btn-top:hover { color: #3b82f6; }
  .btn-del:hover { color: #ef4444; }
  .q-empty { text-align: center; color: #44445a; padding: 3rem; font-size: .86rem; }

  /* Right – sidebar */
  .col-side {
    width: 268px; flex-shrink: 0;
    overflow-y: auto; padding: .8rem;
    display: flex; flex-direction: column; gap: .8rem;
  }
  .card {
    background: #12121e; border: 1px solid #1e1e30;
    border-radius: .68rem; padding: .85rem;
  }
  .card h3 { font-size: .68rem; font-weight: 700; color: #44445a; text-transform: uppercase; letter-spacing: .07em; margin-bottom: .75rem; }

  /* Upload */
  .dropzone {
    border: 2px dashed #1e1e30; border-radius: .58rem;
    padding: .9rem; text-align: center; cursor: pointer;
    font-size: 1.4rem;
  }
  .dropzone:hover, .dropzone.over { border-color: #7c3aed; background: #18182a; }
  .dropzone p { font-size: .71rem; color: #44445a; margin-top: .28rem; }
  .prog-wrap { height: 3px; background: #1e1e30; border-radius: 2px; overflow: hidden; margin: .38rem 0; }
  .prog-fill { height: 100%; background: linear-gradient(90deg, #a78bfa, #60a5fa); width: 0; transition: width .2s; }
  .upload-log { font-size: .7rem; color: #44445a; max-height: 80px; overflow-y: auto; }
  .log-row { padding: .13rem 0; border-bottom: 1px solid #0a0a12; display: flex; justify-content: space-between; gap: .4rem; }
</style>
</head>
<body>

<header>
  <h1>&#127908; Karaoke</h1>
  <span class="chip">ADMIN</span>
  <a href="/" target="_blank">&#128100; Gäste-Ansicht</a>
</header>

<div class="layout">
  <!-- Queue column -->
  <div class="col-main">
    <div id="now-playing">
      <div class="np-empty">&#9899; Nichts spielt gerade.</div>
    </div>
    <div class="queue-header">
      <h2>Warteschlange</h2>
      <span class="q-count" id="q-count"></span>
    </div>
    <div id="queue-list">
      <div class="q-empty">Warteschlange ist leer.</div>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="col-side">
    <div class="card">
      <h3>Songs hochladen</h3>
      <div class="dropzone" id="dropzone"
           onclick="document.getElementById('file-input').click()"
           ondragover="event.preventDefault(); this.classList.add('over')"
           ondragleave="this.classList.remove('over')"
           ondrop="onDrop(event)">
        &#128190;
        <p>MP3 + CDG &nbsp;/&nbsp; MP4<br>Klicken oder Drag &amp; Drop</p>
        <input type="file" id="file-input" multiple accept=".mp3,.cdg,.mp4,.webm,.m4v,.wav"
               style="display:none" onchange="uploadFiles(this.files)">
      </div>
      <div class="prog-wrap"><div class="prog-fill" id="prog-fill"></div></div>
      <div class="upload-log" id="upload-log"></div>
    </div>
  </div>
</div>

<script>
const socket = io();
let state = { now_playing: null, queue: [] };
let keyOffset = 0;
let sortable = null;

socket.on('connect',      () => socket.emit('get_state'));
socket.on('state',        s  => { state = s; render(); });
socket.on('queue_update', s  => { state = s; render(); });

function render() {
  renderNowPlaying();
  renderQueue();
}

// Now Playing
function renderNowPlaying() {
  const el = document.getElementById('now-playing');
  const np = state.now_playing;
  if (!np) {
    el.innerHTML = '<div class="np-empty">&#9899; Nichts spielt gerade.</div>';
    return;
  }
  el.innerHTML = `<div class="np-card">
    <div class="np-song">&#9654; ${esc(np.song.display)}</div>
    <div class="np-singer">&#127908; ${esc(np.singer)}</div>
    <div class="controls">
      <button class="btn btn-play"  onclick="socket.emit('play')">&#9654; Play</button>
      <button class="btn btn-pause" onclick="socket.emit('pause')">&#9646;&#9646; Pause</button>
      <button class="btn btn-skip"  onclick="socket.emit('skip')">&#9197; Skip</button>
      <button class="btn btn-key"   onclick="changeKey(-1)">&#9660;</button>
      <span class="key-val" id="key-val">${keyOffset >= 0 ? '+' : ''}${keyOffset}</span>
      <button class="btn btn-key"   onclick="changeKey(1)">&#9650;</button>
    </div>
  </div>`;
}

// Queue
function renderQueue() {
  const el = document.getElementById('queue-list');
  const qs = state.queue;
  document.getElementById('q-count').textContent = qs.length ? `(${qs.length})` : '';

  if (!qs.length) {
    el.innerHTML = '<div class="q-empty">Warteschlange ist leer.</div>';
    if (sortable) { sortable.destroy(); sortable = null; }
    return;
  }

  el.innerHTML = qs.map(it => `
    <div class="q-item" data-id="${it.id}">
      <span class="handle">&#8801;</span>
      <div class="q-info">
        <span class="q-singer-text" onclick="editSinger(this, '${it.id}')">${esc(it.singer)}</span>
        <div class="q-song-name">${esc(it.song.display)}</div>
      </div>
      <div class="q-actions">
        <button class="icon-btn btn-top" title="An erste Stelle" onclick="moveTop('${it.id}')">&#11014;</button>
        <button class="icon-btn btn-del" title="Entfernen"       onclick="remove('${it.id}')">&#10005;</button>
      </div>
    </div>`).join('');

  if (sortable) sortable.destroy();
  sortable = Sortable.create(el, {
    animation: 150,
    handle: '.handle',
    ghostClass: 'sortable-ghost',
    onEnd() {
      const order = [...el.querySelectorAll('[data-id]')].map(n => n.dataset.id);
      socket.emit('queue_reorder', { order });
    }
  });
}

function changeKey(d) {
  keyOffset += d;
  const el = document.getElementById('key-val');
  if (el) el.textContent = (keyOffset >= 0 ? '+' : '') + keyOffset;
  socket.emit('key_change', { offset: keyOffset });
}

function moveTop(id) { socket.emit('queue_move_top', { id }); }
function remove(id)   { socket.emit('queue_remove',   { id }); }

function editSinger(el, id) {
  const old = el.textContent;
  const inp = document.createElement('input');
  inp.className = 'q-singer-input';
  inp.value = old;
  el.replaceWith(inp);
  inp.focus(); inp.select();
  const save = () => socket.emit('queue_edit_singer', { id, singer: inp.value.trim() || old });
  inp.addEventListener('blur', save);
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') inp.blur();
    if (e.key === 'Escape') { inp.value = old; inp.blur(); }
  });
}

// Upload
function onDrop(e) {
  e.preventDefault();
  document.getElementById('dropzone').classList.remove('over');
  uploadFiles(e.dataTransfer.files);
}
function uploadFiles(files) { [...files].forEach(uploadOne); }
async function uploadOne(file) {
  const log = document.getElementById('upload-log');
  const row = document.createElement('div');
  row.className = 'log-row';
  row.innerHTML = `<span>${file.name.slice(0, 28)}</span><span>&#8230;</span>`;
  log.prepend(row);
  const fd = new FormData();
  fd.append('file', file);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable)
      document.getElementById('prog-fill').style.width = (e.loaded / e.total * 100) + '%';
  };
  await new Promise((ok, fail) => {
    xhr.onload  = () => xhr.status === 200 ? ok() : fail();
    xhr.onerror = fail;
    xhr.send(fd);
  });
  const ok = xhr.status === 200;
  row.querySelector('span:last-child').textContent = ok ? '✓' : '✗';
  row.querySelector('span:last-child').style.color = ok ? '#22c55e' : '#ef4444';
  document.getElementById('prog-fill').style.width = '0%';
}

function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
</script>
</body>
</html>"""

# ── Player page ───────────────────────────────────────────────────────────────

PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Karaoke Player</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; background: #000; overflow: hidden; }

  /* Unlock – shown first, hides after one tap */
  #unlock {
    position: fixed; inset: 0; z-index: 10;
    background: #000;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    cursor: pointer;
    font-family: 'Segoe UI', sans-serif;
  }
  #unlock h1 {
    font-size: 5.5rem; font-weight: 900; letter-spacing: .15em;
    background: linear-gradient(135deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  #unlock p { font-size: 1rem; color: #333; margin-top: 1rem; }

  /* Standby – shown between songs */
  #standby {
    position: fixed; inset: 0;
    background: #000;
    display: none;
    flex-direction: column; align-items: center; justify-content: center;
    font-family: 'Segoe UI', sans-serif;
  }
  #standby h1 {
    font-size: 5.5rem; font-weight: 900; letter-spacing: .15em;
    background: linear-gradient(135deg, #a78bfa, #60a5fa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  #sb-now  { font-size: 1.05rem; color: #22c55e; margin-top: 1.5rem; max-width: 80vw; text-align: center; }
  #sb-next { font-size: .82rem;  color: #222;    margin-top: .5rem;  max-width: 80vw; text-align: center; }

  /* CDG */
  #cdg-wrap { display: none; position: fixed; inset: 0; background: #000; align-items: center; justify-content: center; }
  #cdg-wrap.on { display: flex; }
  canvas { image-rendering: pixelated; display: block; }

  /* Video */
  #vid-wrap { display: none; position: fixed; inset: 0; background: #000; }
  #vid-wrap.on { display: block; }
  video { width: 100%; height: 100%; object-fit: contain; }

  /* Audio (shown as bar at bottom) */
  audio { position: fixed; bottom: 0; left: 0; right: 0; width: 100%; display: none; }
  audio.on { display: block; }

  /* HUD */
  .hud { position: fixed; font-family: monospace; font-size: .7rem; color: rgba(255,255,255,.15); pointer-events: none; }
  #hud-sync { bottom: .6rem; left: .8rem; }
  #hud-key  { bottom: .6rem; right: .8rem; }
</style>
</head>
<body>

<div id="unlock">
  <h1>KARAOKE</h1>
  <p>Einmal tippen zum Starten</p>
</div>

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
// ── CDG decoder ───────────────────────────────────────────────────────────────
class CDGPlayer {
  constructor(canvas) {
    this.cv = canvas; this.ctx = canvas.getContext('2d'); this.reset();
  }
  reset() {
    this.pkts = []; this.px = new Uint8Array(300 * 216);
    this.clr  = new Uint32Array(16); this.img = this.ctx.createImageData(300, 216);
    this.idx  = 0;
    this.ctx.fillStyle = '#000'; this.ctx.fillRect(0, 0, 300, 216);
  }
  load(buf) {
    this.reset();
    const u8 = new Uint8Array(buf);
    for (let i = 0; i + 24 <= u8.length; i += 24)
      this.pkts.push({ cmd: u8[i] & 63, inst: u8[i+1] & 63, d: u8.slice(i+4, i+20) });
  }
  seek(sec) {
    const t = Math.floor(sec * 75);
    if (t < this.idx) { this.px.fill(0); this.clr.fill(0); this.idx = 0; }
    let dirty = false;
    while (this.idx < t && this.idx < this.pkts.length) {
      const p = this.pkts[this.idx++];
      if (p.cmd === 9) { this.exec(p); dirty = true; }
    }
    if (dirty) this.paint();
  }
  exec({ inst, d }) {
    switch (inst) {
      case 1: if ((d[1]&15)===0) this.px.fill(d[0]&15); break;
      case 2: {
        const c = d[0]&15;
        for (let x=0;x<300;x++) { for(let y=0;y<12;y++) this.px[y*300+x]=c; for(let y=204;y<216;y++) this.px[y*300+x]=c; }
        for (let y=12;y<204;y++) { for(let x=0;x<6;x++) this.px[y*300+x]=c; for(let x=294;x<300;x++) this.px[y*300+x]=c; }
        break;
      }
      case 6: case 38: {
        const c0=d[0]&15, c1=d[1]&15, row=(d[2]&31)*12, col=(d[3]&63)*6, xor=inst===38;
        for (let r=0;r<12;r++) { const bits=d[4+r]&63; for(let c=0;c<6;c++) { const b=(bits>>(5-c))&1,i=(row+r)*300+(col+c); this.px[i]=xor?this.px[i]^(b?c1:c0):(b?c1:c0); } }
        break;
      }
      case 30: case 31: {
        const off=inst===31?8:0;
        for (let i=0;i<8;i++) { const hi=d[i*2]&63,lo=d[i*2+1]&63; this.clr[off+i]=(255<<24)|(((lo&15)*17)<<16)|((((hi&3)<<2)|((lo>>4)&3))*17<<8)|((hi>>2&15)*17); }
        break;
      }
      case 20: case 21: {
        const vc=(d[2]>>4)&3, fill=inst===20, color=d[0]&15, tmp=new Uint8Array(this.px);
        if (vc===2) { for(let y=0;y<204;y++) this.px.copyWithin(y*300,(y+12)*300,(y+13)*300); for(let y=204;y<216;y++) this.px.fill(fill?color:tmp[y*300],y*300,(y+1)*300); }
        else if (vc===1) { for(let y=215;y>=12;y--) this.px.copyWithin(y*300,(y-12)*300,(y-11)*300); for(let y=0;y<12;y++) this.px.fill(fill?color:tmp[(204+y)*300],y*300,(y+1)*300); }
        break;
      }
    }
  }
  paint() {
    const px=this.img.data, clr=this.clr, buf=this.px;
    for (let i=0;i<buf.length;i++) { const rgb=clr[buf[i]]; px[i*4]=rgb&255; px[i*4+1]=(rgb>>8)&255; px[i*4+2]=(rgb>>16)&255; px[i*4+3]=255; }
    this.ctx.putImageData(this.img,0,0);
  }
}

// ── Scale canvas ──────────────────────────────────────────────────────────────
const cv = document.getElementById('cdg');
function scale() {
  const s = Math.min(innerWidth / 300, innerHeight / 216);
  cv.style.width = (300*s)+'px'; cv.style.height = (216*s)+'px';
}
addEventListener('resize', scale); scale();

// ── State ─────────────────────────────────────────────────────────────────────
const socket  = io({ transports: ['polling', 'websocket'] });
const aud     = document.getElementById('aud');
const vid     = document.getElementById('vid');
const cdgWrap = document.getElementById('cdg-wrap');
const vidWrap = document.getElementById('vid-wrap');
const standby = document.getElementById('standby');
const unlock  = document.getElementById('unlock');
const cdgP    = new CDGPlayer(cv);
let raf       = null;
let sync      = 0;
let pending   = null;  // song waiting for unlock

// ── Unlock ────────────────────────────────────────────────────────────────────
unlock.addEventListener('click', () => {
  unlock.style.display = 'none';
  standby.style.display = 'flex';
  if (pending) { doLoad(pending); pending = null; }
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function showStandby() {
  standby.style.display = 'flex';
  cdgWrap.classList.remove('on');
  vidWrap.classList.remove('on');
  aud.classList.remove('on');
}

function setSync(v) {
  sync = v;
  document.getElementById('hud-sync').textContent = v !== 0 ? 'sync '+(v>0?'+':'')+v.toFixed(1)+'s' : '';
}

function cdgLoop() {
  cdgP.seek(Math.max(0, aud.currentTime + sync));
  raf = requestAnimationFrame(cdgLoop);
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') setSync(parseFloat((sync + 0.5).toFixed(1)));
  if (e.key === 'ArrowLeft')  setSync(parseFloat((sync - 0.5).toFixed(1)));
});

aud.addEventListener('ended', () => socket.emit('song_ended'));
vid.addEventListener('ended', () => socket.emit('song_ended'));

// ── Socket events ─────────────────────────────────────────────────────────────
socket.on('connect', () => socket.emit('player_connect'));

socket.on('load_song', song => {
  if (unlock.style.display !== 'none') { pending = song; return; }
  doLoad(song);
});

async function doLoad(song) {
  cancelAnimationFrame(raf);
  aud.pause(); vid.pause();
  showStandby();
  setSync(0);

  if (song.type === 'cdg') {
    standby.style.display = 'none';
    cdgWrap.classList.add('on');
    aud.classList.add('on');
    aud.src = '/files/' + encodeURIComponent(song.mp3);
    const buf = await fetch('/files/' + encodeURIComponent(song.cdg)).then(r => r.arrayBuffer());
    cdgP.load(buf);
    raf = requestAnimationFrame(cdgLoop);
    aud.play().catch(() => {});

  } else if (song.type === 'video') {
    standby.style.display = 'none';
    vidWrap.classList.add('on');
    vid.src = '/files/' + encodeURIComponent(song.video);
    vid.play().catch(() => {});

  } else {
    standby.style.display = 'none';
    aud.classList.add('on');
    aud.src = '/files/' + encodeURIComponent(song.audio);
    aud.play().catch(() => {});
  }
}

socket.on('play',  () => { aud.play().catch(() => {}); vid.play().catch(() => {}); });
socket.on('pause', () => { aud.pause(); vid.pause(); });
socket.on('stop',  () => {
  cancelAnimationFrame(raf);
  aud.pause(); vid.pause(); aud.src = ''; vid.src = '';
  showStandby();
  document.getElementById('sb-now').textContent  = '';
  document.getElementById('sb-next').textContent = '';
});

socket.on('key_change', ({ offset }) => {
  document.getElementById('hud-key').textContent = offset !== 0 ? 'key '+(offset>0?'+':'')+offset : '';
});

socket.on('queue_update', ({ now_playing, queue }) => {
  if (standby.style.display === 'none') return;
  document.getElementById('sb-now').textContent  = now_playing ? '▶ '+now_playing.singer+' – '+now_playing.song.display : '';
  const next = queue[0];
  document.getElementById('sb-next').textContent = next ? 'Als nächstes: '+next.singer+' – '+next.song.display : '';
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

# ── WebSocket ─────────────────────────────────────────────────────────────────

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
    item = {
        'id':     str(uuid.uuid4()),
        'song':   data['song'],
        'singer': str(data.get('singer', 'Gast'))[:30],
    }
    queue.append(item)
    emit('add_result', {'ok': True})
    if now_playing is None:
        advance()
    else:
        socketio.emit('queue_update', get_state())

@socketio.on('queue_remove')
def on_queue_remove(data):
    queue[:] = [it for it in queue if it['id'] != data.get('id')]
    socketio.emit('queue_update', get_state())

@socketio.on('queue_reorder')
def on_queue_reorder(data):
    by_id = {it['id']: it for it in queue}
    queue[:] = [by_id[oid] for oid in data.get('order', []) if oid in by_id]
    socketio.emit('queue_update', get_state())

@socketio.on('queue_move_top')
def on_queue_move_top(data):
    tid = data.get('id')
    idx = next((i for i, it in enumerate(queue) if it['id'] == tid), None)
    if idx:
        queue.insert(0, queue.pop(idx))
    socketio.emit('queue_update', get_state())

@socketio.on('queue_edit_singer')
def on_queue_edit_singer(data):
    tid, name = data.get('id'), str(data.get('singer', ''))[:30]
    for it in queue:
        if it['id'] == tid and name:
            it['singer'] = name
            break
    socketio.emit('queue_update', get_state())

@socketio.on('skip')
def on_skip():
    advance()

@socketio.on('song_ended')
def on_song_ended():
    advance()

@socketio.on('play')
def on_play():
    socketio.emit('play', broadcast=True)

@socketio.on('pause')
def on_pause():
    socketio.emit('pause', broadcast=True)

@socketio.on('key_change')
def on_key_change(data):
    socketio.emit('key_change', data, broadcast=True)

# ── Start ─────────────────────────────────────────────────────────────────────

init_db()
sync_library()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
