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

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS songs (
                base    TEXT PRIMARY KEY,
                display TEXT NOT NULL,
                type    TEXT NOT NULL,
                mp3     TEXT,
                cdg     TEXT,
                video   TEXT,
                audio   TEXT
            );
            CREATE TABLE IF NOT EXISTS queue (
                id        TEXT PRIMARY KEY,
                position  INTEGER NOT NULL,
                song_json TEXT NOT NULL,
                singer    TEXT NOT NULL,
                guest_id  TEXT NOT NULL DEFAULT ""
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        ''')
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('song_limit', '2')")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('queue_locked', '0')")

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
    for base in by_base:
        files = by_base[base]
        cdg = files.get('.cdg')
        mp3 = files.get('.mp3') or next((files[e] for e in AUDIO_EXT if e in files), None)
        mp4 = next((files[e] for e in VIDEO_EXT if e in files), None)
        if cdg and mp3:
            rows.append((base, clean_name(base), 'cdg',   mp3, cdg,  None, None))
        elif mp4:
            rows.append((base, clean_name(base), 'video', None, None, mp4, None))
        elif mp3:
            rows.append((base, clean_name(base), 'audio', mp3, None, None, mp3))

    with get_db() as conn:
        conn.execute('DELETE FROM songs')
        conn.executemany(
            'INSERT INTO songs (base,display,type,mp3,cdg,video,audio) VALUES (?,?,?,?,?,?,?)',
            rows
        )

def get_library():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM songs ORDER BY lower(display)').fetchall()
    songs = []
    for r in rows:
        if r['type'] == 'cdg':
            songs.append({'display': r['display'], 'type': 'cdg',   'mp3': r['mp3'], 'cdg': r['cdg']})
        elif r['type'] == 'video':
            songs.append({'display': r['display'], 'type': 'video', 'video': r['video']})
        else:
            songs.append({'display': r['display'], 'type': 'audio', 'audio': r['audio']})
    return songs

# ── State ─────────────────────────────────────────────────────────────────────
queue       = []
now_playing = None
settings    = {'song_limit': 2, 'queue_locked': False}

def load_state():
    global queue, settings
    with get_db() as conn:
        for row in conn.execute('SELECT key, value FROM settings'):
            if row['key'] == 'song_limit':
                settings['song_limit'] = int(row['value'])
            elif row['key'] == 'queue_locked':
                settings['queue_locked'] = row['value'] == '1'
        queue = [
            {'id': r['id'], 'song': json.loads(r['song_json']),
             'singer': r['singer'], 'guest_id': r['guest_id']}
            for r in conn.execute('SELECT * FROM queue ORDER BY position')
        ]

def save_queue():
    with get_db() as conn:
        conn.execute('DELETE FROM queue')
        conn.executemany(
            'INSERT INTO queue (id,position,song_json,singer,guest_id) VALUES (?,?,?,?,?)',
            [(item['id'], i, json.dumps(item['song']), item['singer'], item['guest_id'])
             for i, item in enumerate(queue)]
        )

def save_settings():
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('song_limit', ?)",
                     (str(settings['song_limit']),))
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('queue_locked', ?)",
                     ('1' if settings['queue_locked'] else '0',))

def clean_name(base):
    return base.replace('_', ' ').strip()

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

# ── Guest HTML ────────────────────────────────────────────────────────────────

GUEST_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Karaoke</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f0f1a;color:#e0e0f0;height:100dvh;display:flex;flex-direction:column;overflow:hidden}
.header{background:#1a1a2e;border-bottom:1px solid #2a2a4a;padding:.75rem 1rem;flex-shrink:0}
.header h1{font-size:1.15rem;font-weight:700;background:linear-gradient(135deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.5rem}
.name-row{display:flex;gap:.5rem;margin-bottom:.45rem}
.inp{flex:1;padding:.6rem .85rem;border-radius:.5rem;border:1px solid #2a2a4a;background:#0f0f1a;color:#e0e0f0;font-size:.95rem;outline:none}
.inp:focus{border-color:#a78bfa}
.search-inp{width:100%;padding:.6rem .85rem;border-radius:.5rem;border:1px solid #2a2a4a;background:#0f0f1a;color:#e0e0f0;font-size:.95rem;outline:none}
.search-inp:focus{border-color:#60a5fa}
.tabs{display:flex;border-bottom:1px solid #2a2a4a;flex-shrink:0}
.tab{flex:1;padding:.65rem;text-align:center;font-size:.82rem;font-weight:600;color:#555;cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s}
.tab.active{color:#a78bfa;border-color:#a78bfa}
.view{flex:1;overflow-y:auto;padding:.6rem}
.song{display:flex;align-items:center;gap:.6rem;padding:.7rem .85rem;border-radius:.6rem;border:1px solid #2a2a4a;margin-bottom:.35rem;cursor:pointer;transition:background .15s;-webkit-tap-highlight-color:transparent}
.song:active{background:#1a1a3a;border-color:#a78bfa}
.song-name{flex:1;font-size:.9rem;font-weight:500}
.song-type{font-size:.7rem;color:#555;flex-shrink:0}
.add-btn{padding:.38rem .75rem;border-radius:.35rem;border:none;background:#7c3aed;color:#fff;font-size:.78rem;font-weight:600;cursor:pointer;white-space:nowrap;flex-shrink:0}
.queue-item{display:flex;align-items:center;gap:.6rem;padding:.65rem .75rem;border-radius:.6rem;border:1px solid #2a2a4a;margin-bottom:.35rem;background:#1a1a2e}
.queue-pos{font-size:1.2rem;font-weight:700;color:#7c3aed;min-width:1.8rem;text-align:center;flex-shrink:0}
.queue-info{flex:1;min-width:0}
.queue-singer{font-size:.74rem;color:#a78bfa;font-weight:600}
.queue-song{font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now-card{background:#0d1f12;border:1px solid #4ade80;border-radius:.6rem;padding:.75rem;margin-bottom:.5rem}
.now-label{font-size:.68rem;color:#4ade80;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.now-song{font-size:.92rem;font-weight:600;color:#4ade80;margin-top:.2rem}
.now-singer{font-size:.78rem;color:#86efac}
.locked-banner{background:#1f0a0a;border:1px solid #7f1d1d;border-radius:.6rem;padding:.85rem;text-align:center;color:#f87171;font-size:.88rem;margin-bottom:.5rem}
.empty{text-align:center;color:#444;padding:2rem;font-size:.88rem}
.toast{position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);background:#1a1a2e;border:1px solid #2a2a4a;color:#e0e0f0;padding:.6rem 1.2rem;border-radius:.5rem;font-size:.85rem;z-index:100;opacity:0;transition:opacity .2s;pointer-events:none;white-space:nowrap;max-width:90vw}
.toast.show{opacity:1}
</style>
</head>
<body>
<div class="header">
  <h1>&#127908; Karaoke</h1>
  <div class="name-row">
    <input class="inp" type="text" id="singer-name" placeholder="Dein Name eingeben&#8230;" maxlength="30" oninput="localStorage.setItem('karaoke_name',this.value)"/>
  </div>
  <input class="search-inp" type="text" id="search" placeholder="Song suchen&#8230;" oninput="filterSongs()" style="display:none"/>
</div>
<div class="tabs">
  <div class="tab active" id="tab-songs" onclick="showTab('songs')">&#127925; Songs</div>
  <div class="tab" id="tab-queue" onclick="showTab('queue')">&#9654; Warteschlange</div>
</div>
<div class="view" id="view-songs">
  <div id="song-list"><div class="empty">Wird geladen&#8230;</div></div>
</div>
<div class="view" id="view-queue" style="display:none">
  <div id="queue-display"><div class="empty">Wird geladen&#8230;</div></div>
</div>
<div class="toast" id="toast"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
const socket = io();
let guestId = localStorage.getItem('kg_id');
if (!guestId) { guestId = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem('kg_id', guestId); }
let allSongs = [];
let appState = {now_playing: null, queue: [], settings: {song_limit: 2, queue_locked: false}};

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
  const el = document.getElementById('song-list');
  if (!songs.length) { el.innerHTML = '<div class="empty">Keine Songs gefunden</div>'; return; }
  el.innerHTML = songs.map(s => {
    const icon = s.type === 'cdg' ? '&#127908;' : s.type === 'video' ? '&#127916;' : '&#127925;';
    const sd = JSON.stringify(s).replace(/'/g, '&#39;');
    return `<div class="song" onclick='addSong(${sd})'>
      <span style="font-size:1.1rem;flex-shrink:0">${icon}</span>
      <span class="song-name">${esc(s.display)}</span>
      <span class="song-type">${s.type.toUpperCase()}</span>
      <button class="add-btn" onclick='event.stopPropagation();addSong(${sd})'>+ Queue</button>
    </div>`;
  }).join('');
}

function addSong(song) {
  const singer = document.getElementById('singer-name').value.trim();
  if (!singer) { showToast('Bitte gib zuerst deinen Namen ein!'); return; }
  if (appState.settings.queue_locked) { showToast('Die Warteschlange ist vorübergehend geschlossen.'); return; }
  const myCount = appState.queue.filter(i => i.guest_id === guestId).length;
  if (myCount >= appState.settings.song_limit) {
    showToast('Limit erreicht! Max. ' + appState.settings.song_limit + ' Songs gleichzeitig.');
    return;
  }
  socket.emit('queue_add', {song, singer, guest_id: guestId});
}

socket.on('connect', () => socket.emit('get_state'));
socket.on('state', s => { appState = s; renderQueue(); updateQueueTab(); });
socket.on('queue_update', s => { appState = s; renderQueue(); updateQueueTab(); });
socket.on('add_result', r => { if (r.ok) { showToast('Song hinzugefuegt!'); showTab('queue'); } else showToast(r.error); });

function renderQueue() {
  const el = document.getElementById('queue-display');
  const {now_playing, queue, settings} = appState;
  let html = '';
  if (settings.queue_locked) html += '<div class="locked-banner">&#128274; Die Warteschlange ist vorübergehend geschlossen</div>';
  if (now_playing) html += `<div class="now-card"><div class="now-label">Jetzt</div><div class="now-song">&#9654; ${esc(now_playing.song.display)}</div><div class="now-singer">&#127908; ${esc(now_playing.singer)}</div></div>`;
  if (!queue.length) {
    html += '<div class="empty">' + (now_playing ? 'Keine weiteren Songs in der Warteschlange' : 'Warteschlange ist leer') + '</div>';
    el.innerHTML = html; return;
  }
  html += queue.map((item, i) => `<div class="queue-item"><div class="queue-pos">${i + 1}</div><div class="queue-info"><div class="queue-singer">&#127908; ${esc(item.singer)}</div><div class="queue-song">${esc(item.song.display)}</div></div></div>`).join('');
  el.innerHTML = html;
}

function updateQueueTab() {
  const count = appState.queue.length + (appState.now_playing ? 1 : 0);
  document.getElementById('tab-queue').innerHTML = '&#9654; Warteschlange' + (count ? ' <span style="background:#7c3aed;color:#fff;font-size:.68rem;padding:.1rem .4rem;border-radius:999px">' + count + '</span>' : '');
}

function showTab(t) {
  document.getElementById('tab-songs').classList.toggle('active', t === 'songs');
  document.getElementById('tab-queue').classList.toggle('active', t === 'queue');
  document.getElementById('view-songs').style.display = t === 'songs' ? '' : 'none';
  document.getElementById('view-queue').style.display = t === 'queue' ? '' : 'none';
}

function esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

let toastTimer;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

document.getElementById('singer-name').value = localStorage.getItem('karaoke_name') || '';
</script>
</body>
</html>"""

# ── Admin HTML ────────────────────────────────────────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Karaoke Admin</title>
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f0f1a;color:#e0e0f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.header{background:#1a1a2e;border-bottom:1px solid #2a2a4a;padding:.65rem 1.5rem;display:flex;align-items:center;gap:.75rem;flex-shrink:0}
.header h1{font-size:1.15rem;font-weight:700;background:linear-gradient(135deg,#f472b6,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{background:#7c3aed;color:#fff;font-size:.68rem;padding:.15rem .55rem;border-radius:999px;font-weight:700;letter-spacing:.03em}
.guest-link{margin-left:auto;font-size:.78rem;color:#555;text-decoration:none;padding:.3rem .6rem;border:1px solid #2a2a4a;border-radius:.35rem}
.guest-link:hover{color:#a78bfa;border-color:#a78bfa}
.layout{display:flex;flex:1;overflow:hidden}
.col-main{flex:1;display:flex;flex-direction:column;overflow:hidden;border-right:1px solid #2a2a4a}
.col-side{width:290px;flex-shrink:0;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:1rem}
/* Now Playing */
.now-wrap{padding:.75rem;border-bottom:1px solid #2a2a4a;flex-shrink:0}
.now-empty{color:#444;font-size:.85rem;padding:.5rem 0}
.now-card{background:#0d1f12;border:1px solid #4ade80;border-radius:.65rem;padding:.85rem 1rem}
.now-song-title{font-size:1rem;font-weight:700;color:#4ade80;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now-singer-name{font-size:.8rem;color:#86efac;margin-top:.2rem}
.controls{display:flex;gap:.4rem;margin-top:.7rem;flex-wrap:wrap;align-items:center}
.btn{padding:.38rem .85rem;border-radius:.4rem;border:none;font-size:.8rem;font-weight:600;cursor:pointer;transition:opacity .15s}
.btn:hover{opacity:.82}
.btn-play{background:#16a34a;color:#fff}
.btn-pause{background:#2563eb;color:#fff}
.btn-skip{background:#f59e0b;color:#000}
.btn-key{background:#374151;color:#e0e0f0;padding:.38rem .6rem;min-width:2.2rem}
.key-val{font-size:.78rem;color:#888;min-width:2.4rem;text-align:center}
/* Queue */
.queue-hdr{display:flex;align-items:center;padding:.65rem 1rem;border-bottom:1px solid #2a2a4a;flex-shrink:0;gap:.5rem}
.queue-hdr h2{font-size:.78rem;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em}
.q-count{font-size:.78rem;color:#7c3aed;font-weight:700}
.queue-body{flex:1;overflow-y:auto;padding:.5rem .75rem}
.q-item{display:flex;align-items:center;gap:.55rem;padding:.6rem .75rem;border-radius:.6rem;border:1px solid #2a2a4a;margin-bottom:.3rem;background:#1a1a2e;transition:border-color .15s}
.q-item:hover{border-color:#7c3aed}
.drag-handle{cursor:grab;color:#444;font-size:1.1rem;flex-shrink:0;user-select:none;padding:.1rem}
.drag-handle:active{cursor:grabbing}
.q-info{flex:1;min-width:0}
.q-singer{font-size:.75rem;color:#a78bfa;font-weight:600;cursor:pointer;display:inline-block}
.q-singer:hover{text-decoration:underline}
.q-singer-input{background:transparent;border:none;border-bottom:1px solid #7c3aed;color:#a78bfa;font-size:.75rem;font-weight:600;outline:none;width:100%;display:block}
.q-song{font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#e0e0f0}
.q-actions{display:flex;gap:.3rem;flex-shrink:0}
.icon-btn{background:none;border:none;cursor:pointer;padding:.3rem .35rem;border-radius:.3rem;font-size:.88rem;line-height:1;transition:background .15s}
.icon-btn:hover{background:#2a2a4a}
.btn-top{color:#60a5fa}
.btn-del{color:#f87171}
.q-empty{text-align:center;color:#444;padding:2.5rem;font-size:.9rem}
.sortable-ghost{opacity:.35;background:#2a2a4a}
/* Settings */
.section{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:.7rem;padding:.85rem}
.section h2{font-size:.72rem;font-weight:700;color:#666;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.75rem}
.setting-row{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:.7rem}
.setting-row:last-child{margin-bottom:0}
.setting-label{font-size:.82rem;color:#e0e0f0}
.setting-label small{display:block;font-size:.7rem;color:#555;margin-top:.12rem}
.num-inp{width:58px;padding:.33rem .5rem;border-radius:.35rem;border:1px solid #2a2a4a;background:#0f0f1a;color:#e0e0f0;font-size:.85rem;text-align:center;outline:none}
.num-inp:focus{border-color:#a78bfa}
.toggle{position:relative;display:inline-block;width:42px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#2a2a4a;border-radius:11px;cursor:pointer;transition:.2s}
.slider:before{content:'';position:absolute;width:16px;height:16px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
input:checked+.slider{background:#7c3aed}
input:checked+.slider:before{transform:translateX(20px)}
/* Upload */
.drop-zone{border:2px dashed #2a2a4a;border-radius:.6rem;padding:.9rem;text-align:center;cursor:pointer;font-size:1.4rem;transition:border-color .2s,background .2s}
.drop-zone:hover{border-color:#a78bfa;background:#1a1a3a}
.drop-zone p{font-size:.75rem;color:#555;margin-top:.25rem}
.upload-bar-wrap{height:3px;background:#2a2a4a;border-radius:2px;overflow:hidden;margin:.4rem 0}
.upload-bar{height:100%;background:linear-gradient(90deg,#a78bfa,#60a5fa);width:0;transition:width .2s}
.upload-log{font-size:.73rem;color:#555;max-height:75px;overflow-y:auto}
</style>
</head>
<body>
<div class="header">
  <h1>&#127908; Karaoke</h1>
  <span class="badge">ADMIN</span>
  <a href="/" class="guest-link" target="_blank">&#128100; Gäste-Ansicht</a>
</div>
<div class="layout">
  <div class="col-main">
    <div class="now-wrap" id="now-wrap"></div>
    <div class="queue-hdr">
      <h2>Warteschlange</h2>
      <span class="q-count" id="q-count"></span>
    </div>
    <div class="queue-body" id="queue-body"></div>
  </div>
  <div class="col-side">
    <div class="section">
      <h2>Einstellungen</h2>
      <div class="setting-row">
        <div class="setting-label">Songs-Limit pro Gast<small>Max. gleichzeitig in Queue</small></div>
        <input type="number" class="num-inp" id="song-limit" min="1" max="20" value="2" onchange="saveSetting()"/>
      </div>
      <div class="setting-row">
        <div class="setting-label">Queue sperren<small>Gäste können nichts hinzufügen</small></div>
        <label class="toggle"><input type="checkbox" id="queue-locked" onchange="saveSetting()"/><span class="slider"></span></label>
      </div>
    </div>
    <div class="section">
      <h2>Songs hochladen</h2>
      <div class="drop-zone" id="dz" onclick="document.getElementById('fi').click()" ondragover="event.preventDefault();this.style.borderColor='#a78bfa'" ondragleave="this.style.borderColor='#2a2a4a'" ondrop="dropFiles(event)">
        &#128190;
        <p>MP3 + CDG / MP4</p>
        <input type="file" id="fi" multiple accept=".mp3,.cdg,.mp4,.webm,.m4v,.wav" style="display:none" onchange="uploadFiles(this.files)"/>
      </div>
      <div class="upload-bar-wrap"><div class="upload-bar" id="ubar"></div></div>
      <div class="upload-log" id="ulog"></div>
    </div>
  </div>
</div>

<script>
const socket = io();
let state = {now_playing: null, queue: [], settings: {song_limit: 2, queue_locked: false}};
let keyOffset = 0;
let sortable = null;

socket.on('connect', () => socket.emit('get_state'));
socket.on('state', s => { state = s; render(); });
socket.on('queue_update', s => { state = s; render(); });

function render() { renderNow(); renderQueue(); renderSettings(); }

function renderNow() {
  const np = state.now_playing;
  const el = document.getElementById('now-wrap');
  if (!np) { el.innerHTML = '<div class="now-empty">&#9899; Nichts spielt gerade.</div>'; return; }
  el.innerHTML = `<div class="now-card">
    <div class="now-song-title">&#9654; ${esc(np.song.display)}</div>
    <div class="now-singer-name">&#127908; ${esc(np.singer)}</div>
    <div class="controls">
      <button class="btn btn-play" onclick="socket.emit('play')">&#9654; Play</button>
      <button class="btn btn-pause" onclick="socket.emit('pause')">&#9646;&#9646; Pause</button>
      <button class="btn btn-skip" onclick="socket.emit('skip')">&#9197; Skip</button>
      <button class="btn btn-key" onclick="changeKey(-1)">&#9660;</button>
      <span class="key-val" id="key-val">${keyOffset >= 0 ? '+' : ''}${keyOffset}</span>
      <button class="btn btn-key" onclick="changeKey(1)">&#9650;</button>
    </div>
  </div>`;
}

function renderQueue() {
  const body = document.getElementById('queue-body');
  const count = state.queue.length;
  document.getElementById('q-count').textContent = count ? '(' + count + ')' : '';
  if (!count) {
    body.innerHTML = '<div class="q-empty">Warteschlange ist leer</div>';
    if (sortable) { sortable.destroy(); sortable = null; }
    return;
  }
  body.innerHTML = state.queue.map(item => `
    <div class="q-item" data-id="${item.id}">
      <span class="drag-handle">&#8801;</span>
      <div class="q-info">
        <span class="q-singer" onclick="editSinger(this,'${item.id}',this.textContent)">${esc(item.singer)}</span>
        <div class="q-song">${esc(item.song.display)}</div>
      </div>
      <div class="q-actions">
        <button class="icon-btn btn-top" title="An erste Stelle" onclick="moveTop('${item.id}')">&#11014;</button>
        <button class="icon-btn btn-del" title="Entfernen" onclick="removeItem('${item.id}')">&#10005;</button>
      </div>
    </div>`).join('');
  if (sortable) sortable.destroy();
  sortable = Sortable.create(body, {
    animation: 150, handle: '.drag-handle', ghostClass: 'sortable-ghost',
    onEnd: () => {
      const order = [...body.querySelectorAll('[data-id]')].map(el => el.dataset.id);
      socket.emit('queue_reorder', {order});
    }
  });
}

function renderSettings() {
  document.getElementById('song-limit').value = state.settings.song_limit;
  document.getElementById('queue-locked').checked = state.settings.queue_locked;
}

function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function changeKey(d) {
  keyOffset += d;
  const el = document.getElementById('key-val');
  if (el) el.textContent = (keyOffset >= 0 ? '+' : '') + keyOffset;
  socket.emit('key_change', {offset: keyOffset});
}

function moveTop(id) { socket.emit('queue_move_top', {id}); }
function removeItem(id) { socket.emit('queue_remove', {id}); }

function editSinger(el, id, current) {
  const inp = document.createElement('input');
  inp.className = 'q-singer-input';
  inp.value = current;
  el.replaceWith(inp);
  inp.focus();
  const save = () => { socket.emit('queue_edit_singer', {id, singer: inp.value.trim() || current}); };
  inp.addEventListener('blur', save);
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
}

function saveSetting() {
  socket.emit('settings_update', {
    song_limit: parseInt(document.getElementById('song-limit').value) || 2,
    queue_locked: document.getElementById('queue-locked').checked
  });
}

function dropFiles(e) { e.preventDefault(); document.getElementById('dz').style.borderColor = '#2a2a4a'; uploadFiles(e.dataTransfer.files); }
function uploadFiles(files) { [...files].forEach(uploadOne); }
async function uploadOne(file) {
  const log = document.getElementById('ulog');
  const row = document.createElement('div');
  row.style.cssText = 'padding:.15rem 0;border-bottom:1px solid #0f0f1a';
  row.textContent = file.name.slice(0, 32) + ' …';
  log.prepend(row);
  const fd = new FormData(); fd.append('file', file);
  const xhr = new XMLHttpRequest(); xhr.open('POST', '/upload');
  xhr.upload.onprogress = e => { if (e.lengthComputable) document.getElementById('ubar').style.width = (e.loaded / e.total * 100) + '%'; };
  await new Promise((res, rej) => { xhr.onload = () => xhr.status === 200 ? res() : rej(); xhr.onerror = rej; xhr.send(fd); });
  row.textContent = file.name.slice(0, 32) + (xhr.status === 200 ? ' ✓' : ' ✗');
  row.style.color = xhr.status === 200 ? '#4ade80' : '#f87171';
  document.getElementById('ubar').style.width = '0%';
}
</script>
</body>
</html>"""

# ── Player HTML ───────────────────────────────────────────────────────────────

PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <title>Karaoke Player</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    html,body{width:100%;height:100%;background:#000;overflow:hidden;}
    #standby{position:fixed;inset:0;background:#000;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Segoe UI',sans-serif;}
    #standby h1{font-size:5rem;font-weight:900;letter-spacing:.2em;background:linear-gradient(135deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
    #standby-msg{color:#444;font-size:1.1rem;margin-top:1rem;text-align:center;max-width:60vw}
    #standby-next{color:#333;font-size:.9rem;margin-top:.5rem;text-align:center;max-width:60vw}
    #cdg-wrap{display:none;position:fixed;inset:0;background:#000;align-items:center;justify-content:center;}
    #cdg-wrap.visible{display:flex;}
    canvas{image-rendering:pixelated;image-rendering:crisp-edges;display:block;}
    #vid-wrap{display:none;position:fixed;inset:0;background:#000;}
    #vid-wrap.visible{display:block;}
    video{width:100%;height:100%;object-fit:contain;}
    audio{position:fixed;bottom:0;left:0;right:0;width:100%;display:none;}
    audio.visible{display:block;}
    #offset-label{position:fixed;bottom:0.5rem;left:1rem;font-family:sans-serif;font-size:0.75rem;color:rgba(255,255,255,0.3);pointer-events:none;}
    #key-label{position:fixed;bottom:0.5rem;right:1rem;font-family:sans-serif;font-size:0.75rem;color:rgba(255,255,255,0.25);pointer-events:none;}
  </style>
</head>
<body>
<div id="standby">
  <h1>KARAOKE</h1>
  <p id="standby-msg">Warte auf Song&hellip;</p>
  <p id="standby-next"></p>
</div>
<div id="cdg-wrap"><canvas id="cdg" width="300" height="216"></canvas></div>
<div id="vid-wrap"><video id="vid" preload="auto"></video></div>
<audio id="aud" preload="auto" controls></audio>
<div id="offset-label"></div>
<div id="key-label"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
const CDG_CMD            = 0x09;
const MEMORY_PRESET      = 0x01;
const BORDER_PRESET      = 0x02;
const TILE_BLOCK         = 0x06;
const TILE_BLOCK_XOR     = 0x26;
const SCROLL_PRESET      = 0x14;
const SCROLL_COPY        = 0x15;
const LOAD_CLR_LOW       = 0x1E;
const LOAD_CLR_HIGH      = 0x1F;

class CDGPlayer {
  constructor(canvas) { this.canvas = canvas; this.ctx = canvas.getContext('2d'); this.reset(); }
  reset() {
    this.packets = []; this.pixels = new Uint8Array(300 * 216);
    this.clrTable = new Uint32Array(16); this.imgData = this.ctx.createImageData(300, 216);
    this.pktIdx = 0; this.ctx.fillStyle = '#000'; this.ctx.fillRect(0, 0, 300, 216);
  }
  load(buffer) {
    this.reset();
    const u8 = new Uint8Array(buffer);
    for (let i = 0; i + 24 <= u8.length; i += 24)
      this.packets.push({cmd: u8[i] & 0x3F, inst: u8[i+1] & 0x3F, data: u8.slice(i+4, i+20)});
  }
  seek(timeSec) {
    const target = Math.floor(timeSec * 75);
    if (target < this.pktIdx) { this.pixels.fill(0); this.clrTable.fill(0); this.pktIdx = 0; }
    let dirty = false;
    while (this.pktIdx < target && this.pktIdx < this.packets.length) {
      const p = this.packets[this.pktIdx++];
      if (p.cmd === CDG_CMD) { this.exec(p); dirty = true; }
    }
    if (dirty) this.paint();
  }
  exec(p) {
    const d = p.data;
    switch (p.inst) {
      case MEMORY_PRESET:
        if ((d[1] & 0x0F) === 0) this.pixels.fill(d[0] & 0x0F);
        break;
      case BORDER_PRESET: {
        const c = d[0] & 0x0F;
        for (let x = 0; x < 300; x++) { for (let y = 0; y < 12; y++) this.pixels[y*300+x] = c; for (let y = 204; y < 216; y++) this.pixels[y*300+x] = c; }
        for (let y = 12; y < 204; y++) { for (let x = 0; x < 6; x++) this.pixels[y*300+x] = c; for (let x = 294; x < 300; x++) this.pixels[y*300+x] = c; }
        break;
      }
      case TILE_BLOCK:
      case TILE_BLOCK_XOR: {
        const c0 = d[0] & 0x0F, c1 = d[1] & 0x0F;
        const row = (d[2] & 0x1F) * 12, col = (d[3] & 0x3F) * 6;
        const xorMode = p.inst === TILE_BLOCK_XOR;
        for (let r = 0; r < 12; r++) {
          const bits = d[4+r] & 0x3F;
          for (let c = 0; c < 6; c++) {
            const bit = (bits >> (5-c)) & 1, idx = (row+r)*300 + (col+c);
            this.pixels[idx] = xorMode ? this.pixels[idx] ^ (bit ? c1 : c0) : (bit ? c1 : c0);
          }
        }
        break;
      }
      case LOAD_CLR_LOW:
      case LOAD_CLR_HIGH: {
        const off = p.inst === LOAD_CLR_HIGH ? 8 : 0;
        for (let i = 0; i < 8; i++) {
          const hi = d[i*2] & 0x3F, lo = d[i*2+1] & 0x3F;
          const r = ((hi >> 2) & 0x0F) * 17, g = (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17, b = (lo & 0x0F) * 17;
          this.clrTable[off+i] = (255<<24) | (b<<16) | (g<<8) | r;
        }
        break;
      }
      case SCROLL_PRESET:
      case SCROLL_COPY: {
        const color = d[0] & 0x0F;
        const vScroll = d[2] & 0x3F, vCmd = (vScroll >> 4) & 3;
        const tmp = new Uint8Array(this.pixels);
        if (vCmd === 2) { for (let y = 0; y < 204; y++) this.pixels.copyWithin(y*300, (y+12)*300, (y+13)*300); for (let y = 204; y < 216; y++) this.pixels.fill(p.inst===SCROLL_PRESET?color:tmp[y*300], y*300, (y+1)*300); }
        else if (vCmd === 1) { for (let y = 215; y >= 12; y--) this.pixels.copyWithin(y*300, (y-12)*300, (y-11)*300); for (let y = 0; y < 12; y++) this.pixels.fill(p.inst===SCROLL_PRESET?color:tmp[(204+y)*300], y*300, (y+1)*300); }
        break;
      }
    }
  }
  paint() {
    const px = this.imgData.data, c = this.clrTable, buf = this.pixels;
    for (let i = 0; i < buf.length; i++) {
      const rgb = c[buf[i]]; px[i*4] = rgb & 0xFF; px[i*4+1] = (rgb>>8) & 0xFF; px[i*4+2] = (rgb>>16) & 0xFF; px[i*4+3] = 255;
    }
    this.ctx.putImageData(this.imgData, 0, 0);
  }
}

function scaleCanvas() {
  const el = document.getElementById('cdg');
  const s = Math.min(window.innerWidth / 300, window.innerHeight / 216);
  el.style.width = (300 * s) + 'px'; el.style.height = (216 * s) + 'px';
}
window.addEventListener('resize', scaleCanvas); scaleCanvas();

const socket  = io({transports: ['polling', 'websocket']});
const aud     = document.getElementById('aud');
const vid     = document.getElementById('vid');
const cdgWrap = document.getElementById('cdg-wrap');
const vidWrap = document.getElementById('vid-wrap');
const standby = document.getElementById('standby');
const cdg     = new CDGPlayer(document.getElementById('cdg'));
let   rafId   = null;
let   syncOff = 0;

function showStandby() {
  standby.style.display = 'flex'; cdgWrap.classList.remove('visible');
  vidWrap.classList.remove('visible'); aud.classList.remove('visible');
}

function cdgLoop() { cdg.seek(Math.max(0, aud.currentTime + syncOff)); rafId = requestAnimationFrame(cdgLoop); }

function updateOffsetDisplay() {
  document.getElementById('offset-label').textContent = 'Sync: ' + (syncOff >= 0 ? '+' : '') + syncOff.toFixed(1) + 's';
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') { syncOff = parseFloat((syncOff + 0.5).toFixed(1)); updateOffsetDisplay(); }
  if (e.key === 'ArrowLeft')  { syncOff = parseFloat((syncOff - 0.5).toFixed(1)); updateOffsetDisplay(); }
});

aud.addEventListener('ended', () => socket.emit('song_ended'));
vid.addEventListener('ended', () => socket.emit('song_ended'));

socket.on('connect', () => socket.emit('player_connect'));

socket.on('load_song', async song => {
  cancelAnimationFrame(rafId); aud.pause(); vid.pause(); showStandby();
  syncOff = 0; updateOffsetDisplay();
  document.getElementById('key-label').textContent = '';

  if (song.type === 'cdg') {
    standby.style.display = 'none'; cdgWrap.classList.add('visible'); aud.classList.add('visible');
    aud.src = '/files/' + encodeURIComponent(song.mp3);
    const buf = await fetch('/files/' + encodeURIComponent(song.cdg)).then(r => r.arrayBuffer());
    cdg.load(buf); rafId = requestAnimationFrame(cdgLoop); aud.play().catch(() => {});
  } else if (song.type === 'video') {
    standby.style.display = 'none'; vidWrap.classList.add('visible');
    vid.src = '/files/' + encodeURIComponent(song.video); vid.play().catch(() => {});
  } else {
    standby.style.display = 'none'; aud.classList.add('visible');
    aud.src = '/files/' + encodeURIComponent(song.audio); aud.play().catch(() => {});
  }
});

socket.on('play',  () => { aud.play().catch(() => {}); vid.play().catch(() => {}); });
socket.on('pause', () => { aud.pause(); vid.pause(); });
socket.on('stop',  () => { cancelAnimationFrame(rafId); aud.pause(); vid.pause(); aud.src = ''; vid.src = ''; showStandby(); });

socket.on('key_change', data => {
  const off = data.offset || 0;
  document.getElementById('key-label').textContent = 'Key: ' + (off >= 0 ? '+' : '') + off;
});

socket.on('queue_update', state => {
  if (standby.style.display !== 'none') {
    const next = state.queue[0];
    document.getElementById('standby-next').textContent = next
      ? 'Als nächstes: ' + next.singer + ' – ' + next.song.display : '';
  }
});
</script>
</body>
</html>"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
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
    fn   = secure_filename(filename)
    path = os.path.join(LIBRARY_DIR, fn)
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
    if settings['queue_locked']:
        emit('add_result', {'ok': False, 'error': 'Warteschlange ist geschlossen'})
        return
    guest_id = data.get('guest_id', '')
    count = sum(1 for item in queue if item.get('guest_id') == guest_id)
    if count >= settings['song_limit']:
        emit('add_result', {'ok': False, 'error': f'Limit von {settings["song_limit"]} Songs erreicht'})
        return
    item = {
        'id': str(uuid.uuid4()),
        'song': data['song'],
        'singer': str(data.get('singer', 'Gast'))[:30],
        'guest_id': guest_id,
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
    global queue
    queue[:] = [item for item in queue if item['id'] != data.get('id')]
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_reorder')
def on_queue_reorder(data):
    global queue
    order  = data.get('order', [])
    id_map = {item['id']: item for item in queue}
    queue[:] = [id_map[oid] for oid in order if oid in id_map]
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_move_top')
def on_queue_move_top(data):
    global queue
    target_id = data.get('id')
    idx = next((i for i, item in enumerate(queue) if item['id'] == target_id), None)
    if idx is not None and idx > 0:
        queue.insert(0, queue.pop(idx))
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_edit_singer')
def on_queue_edit_singer(data):
    target_id = data.get('id')
    new_name  = str(data.get('singer', ''))[:30]
    for item in queue:
        if item['id'] == target_id:
            item['singer'] = new_name or item['singer']
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

init_db()
sync_library()
load_state()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
