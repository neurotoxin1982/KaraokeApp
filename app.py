import eventlet
eventlet.monkey_patch()

import os, glob
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024  # 4 GB

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet',
                    max_http_buffer_size=1024 * 1024)

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', '/app/library')
os.makedirs(LIBRARY_DIR, exist_ok=True)

ALLOWED = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.mp3', '.m4a', '.m4v', '.wav', '.ogg'}

def display_name(filename):
    return os.path.splitext(filename)[0].replace('_', ' ').strip()

# ── HTML ──────────────────────────────────────────────────────────────────────

DJ_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Karaoke Library</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f0f1a; color: #e0e0f0; min-height: 100vh; }
    .header { background: #1a1a2e; border-bottom: 1px solid #2a2a4a; padding: 1rem 2rem; display: flex; align-items: center; gap: 1rem; }
    .header h1 { font-size: 1.4rem; font-weight: 700; background: linear-gradient(135deg,#a78bfa,#60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .header input { flex: 1; max-width: 400px; padding: 0.6rem 1rem; border-radius: 0.5rem; border: 1px solid #2a2a4a; background: #0f0f1a; color: #e0e0f0; font-size: 0.95rem; outline: none; }
    .header input:focus { border-color: #a78bfa; }
    .layout { display: flex; height: calc(100vh - 60px); }
    /* Sidebar upload */
    .sidebar { width: 280px; flex-shrink: 0; border-right: 1px solid #2a2a4a; padding: 1.25rem; overflow-y: auto; }
    .sidebar h2 { font-size: 0.85rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem; }
    .drop-zone { border: 2px dashed #2a2a4a; border-radius: 0.75rem; padding: 1.5rem; text-align: center; cursor: pointer; transition: border-color 0.2s, background 0.2s; }
    .drop-zone:hover, .drop-zone.over { border-color: #a78bfa; background: #1a1a3a; }
    .drop-zone p { font-size: 0.85rem; color: #888; margin-top: 0.5rem; }
    .drop-zone input { display: none; }
    .upload-list { margin-top: 0.75rem; }
    .upload-item { font-size: 0.8rem; color: #888; padding: 0.3rem 0; border-bottom: 1px solid #1a1a2e; display: flex; justify-content: space-between; }
    .upload-item .ok  { color: #4ade80; }
    .upload-item .err { color: #f87171; }
    .progress { height: 4px; background: #2a2a4a; border-radius: 2px; margin-top: 0.4rem; overflow: hidden; }
    .progress-bar { height: 100%; background: linear-gradient(90deg,#a78bfa,#60a5fa); width: 0%; transition: width 0.3s; }
    /* Song list */
    .main { flex: 1; overflow-y: auto; padding: 1rem 1.5rem; }
    .song-count { font-size: 0.8rem; color: #555; margin-bottom: 0.75rem; }
    .song-item { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1rem; border-radius: 0.6rem; border: 1px solid #2a2a4a; margin-bottom: 0.4rem; cursor: pointer; transition: background 0.15s; }
    .song-item:hover { background: #1a1a3a; border-color: #a78bfa; }
    .song-item.playing { background: #1a2a1a; border-color: #4ade80; }
    .song-icon { font-size: 1.3rem; flex-shrink: 0; }
    .song-name { flex: 1; font-size: 0.92rem; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .song-ext  { font-size: 0.75rem; color: #555; flex-shrink: 0; }
    .play-btn  { flex-shrink: 0; padding: 0.3rem 0.8rem; border-radius: 0.4rem; border: none; background: #7c3aed; color: #fff; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
    .play-btn:hover { background: #6d28d9; }
    /* Now playing bar */
    #now-playing { display: none; position: fixed; bottom: 0; left: 0; right: 0; background: #1a1a2e; border-top: 1px solid #2a2a4a; padding: 0.75rem 1.5rem; display: none; align-items: center; gap: 1rem; }
    #now-playing.visible { display: flex; }
    #np-name { flex: 1; font-size: 0.9rem; font-weight: 600; color: #4ade80; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ctrl-btn { padding: 0.4rem 1rem; border-radius: 0.4rem; border: none; font-size: 0.85rem; font-weight: 600; cursor: pointer; }
    .ctrl-play  { background: #16a34a; color: #fff; }
    .ctrl-pause { background: #2563eb; color: #fff; }
    .ctrl-stop  { background: #dc2626; color: #fff; }
    .empty { text-align: center; color: #555; padding: 3rem 1rem; font-size: 0.9rem; }
  </style>
</head>
<body>
<div class="header">
  <h1>&#127908; Karaoke Library</h1>
  <input type="text" id="search" placeholder="Song suchen..." oninput="filterSongs()"/>
</div>

<div class="layout">
  <div class="sidebar">
    <h2>Songs hochladen</h2>
    <div class="drop-zone" id="drop-zone" onclick="document.getElementById('file-input').click()"
         ondragover="event.preventDefault();this.classList.add('over')"
         ondragleave="this.classList.remove('over')"
         ondrop="handleDrop(event)">
      &#128190;
      <p>Hier klicken oder<br>Dateien reinziehen</p>
      <input type="file" id="file-input" multiple accept="video/*,audio/*" onchange="handleFiles(this.files)"/>
    </div>
    <div class="progress"><div class="progress-bar" id="prog-bar"></div></div>
    <div class="upload-list" id="upload-list"></div>
  </div>

  <div class="main">
    <div class="song-count" id="song-count"></div>
    <div id="song-list"></div>
  </div>
</div>

<div id="now-playing">
  <span id="np-name"></span>
  <button class="ctrl-btn ctrl-play"  onclick="doPlay()">&#9654; Play</button>
  <button class="ctrl-btn ctrl-pause" onclick="doPause()">&#9646;&#9646; Pause</button>
  <button class="ctrl-btn ctrl-stop"  onclick="doStop()">&#9632; Stop</button>
</div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const socket = io();
  let allSongs = [];
  let playerWin = null;
  let currentFile = null;

  function ae(s){ return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }

  // ── Library ──────────────────────────────────────────────────────────────────
  function loadLibrary() {
    fetch('/library').then(r=>r.json()).then(songs => {
      allSongs = songs;
      renderSongs(songs);
    });
  }

  function renderSongs(songs) {
    const el = document.getElementById('song-list');
    document.getElementById('song-count').textContent = songs.length + ' Songs';
    if (!songs.length) { el.innerHTML='<div class="empty">Keine Songs — lade Dateien hoch.</div>'; return; }
    el.innerHTML = songs.map(s => `
      <div class="song-item ${s.filename===currentFile?'playing':''}"
           id="si-${CSS.escape(ae(s.filename))}"
           data-filename="${ae(s.filename)}" data-display="${ae(s.display)}"
           onclick="playSong(this)">
        <span class="song-icon">${s.type==='audio'?'&#127925;':'&#127916;'}</span>
        <span class="song-name" title="${ae(s.display)}">${s.display}</span>
        <span class="song-ext">${ae(s.ext)}</span>
        <button class="play-btn">&#9654; Play</button>
      </div>`).join('');
  }

  function filterSongs() {
    const q = document.getElementById('search').value.toLowerCase();
    const filtered = q ? allSongs.filter(s=>s.display.toLowerCase().includes(q)) : allSongs;
    renderSongs(filtered);
  }

  // ── Playback ─────────────────────────────────────────────────────────────────
  function playSong(el) {
    const filename = el.dataset.filename;
    const display  = el.dataset.display;
    currentFile = filename;

    // Open player window if not open
    if (!playerWin || playerWin.closed) {
      playerWin = window.open('/player', 'karaoke_player', 'width=1280,height=720');
    }

    // Give player window time to load, then send event
    setTimeout(() => {
      socket.emit('load_song', {filename, display});
    }, 800);

    document.getElementById('np-name').textContent = display;
    document.getElementById('now-playing').classList.add('visible');
    document.querySelectorAll('.song-item').forEach(i=>i.classList.remove('playing'));
    el.classList.add('playing');
  }

  function doPlay()  { socket.emit('play');  }
  function doPause() { socket.emit('pause'); }
  function doStop()  {
    socket.emit('stop');
    currentFile = null;
    document.getElementById('now-playing').classList.remove('visible');
    document.querySelectorAll('.song-item').forEach(i=>i.classList.remove('playing'));
  }

  // ── Upload ───────────────────────────────────────────────────────────────────
  function handleDrop(e) {
    e.preventDefault();
    document.getElementById('drop-zone').classList.remove('over');
    handleFiles(e.dataTransfer.files);
  }

  function handleFiles(files) {
    [...files].forEach(uploadFile);
  }

  async function uploadFile(file) {
    const list = document.getElementById('upload-list');
    const item = document.createElement('div');
    item.className = 'upload-item';
    item.innerHTML = `<span>${file.name.slice(0,30)}</span><span>...</span>`;
    list.prepend(item);

    const bar = document.getElementById('prog-bar');
    const fd  = new FormData();
    fd.append('file', file);

    try {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) bar.style.width = (e.loaded/e.total*100)+'%';
      };
      await new Promise((res,rej) => {
        xhr.onload = () => xhr.status===200 ? res(JSON.parse(xhr.responseText)) : rej(xhr.responseText);
        xhr.onerror = rej;
        xhr.send(fd);
      });
      item.querySelector('span:last-child').className = 'ok';
      item.querySelector('span:last-child').textContent = '✓';
      bar.style.width = '0%';
      loadLibrary();
    } catch(e) {
      item.querySelector('span:last-child').className = 'err';
      item.querySelector('span:last-child').textContent = '✗';
      bar.style.width = '0%';
    }
  }

  loadLibrary();
</script>
</body>
</html>
"""

PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <title>Karaoke Player</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { width: 100%; height: 100%; background: #000; overflow: hidden; }
    #vid { width: 100%; height: 100%; object-fit: contain; display: block; }
    #standby {
      position: fixed; inset: 0; background: #000;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      font-family: 'Segoe UI', sans-serif;
    }
    #standby h1 { font-size: 5rem; font-weight: 900; letter-spacing: 0.2em;
      background: linear-gradient(135deg,#a78bfa,#60a5fa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
    #standby p { color: #444; font-size: 1.2rem; margin-top: 1rem; }
    #song-title { position: fixed; top: 1rem; left: 0; right: 0; text-align: center;
      font-family: sans-serif; font-size: 1rem; color: rgba(255,255,255,0.4);
      display: none; pointer-events: none; }
  </style>
</head>
<body>
  <div id="standby">
    <h1>KARAOKE</h1>
    <p>Warte auf Song&hellip;</p>
  </div>
  <video id="vid"></video>
  <div id="song-title"></div>

  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script>
    const socket = io({transports:['polling','websocket']});
    const vid    = document.getElementById('vid');
    const standby = document.getElementById('standby');
    const titleEl = document.getElementById('song-title');

    socket.on('load_song', data => {
      vid.src = '/files/' + encodeURIComponent(data.filename);
      vid.load();
      titleEl.textContent = data.display;
      titleEl.style.display = 'block';
      standby.style.display = 'none';
      vid.play().catch(()=>{});
    });

    socket.on('play',  () => vid.play().catch(()=>{}));
    socket.on('pause', () => vid.pause());
    socket.on('stop',  () => {
      vid.pause(); vid.src = '';
      standby.style.display = 'flex';
      titleEl.style.display = 'none';
    });
  </script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return DJ_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/player')
def player():
    return PLAYER_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/library')
def library():
    songs = []
    for path in sorted(glob.glob(os.path.join(LIBRARY_DIR, '*'))):
        fn  = os.path.basename(path)
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ALLOWED:
            continue
        is_audio = ext in {'.mp3', '.m4a', '.wav', '.ogg'}
        songs.append({
            'filename': fn,
            'display':  display_name(fn),
            'ext':      ext,
            'type':     'audio' if is_audio else 'video',
        })
    return jsonify(songs)

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
    return jsonify({'filename': fn, 'display': display_name(fn)})

@app.route('/files/<filename>')
def serve_file(filename):
    fn = secure_filename(filename)
    path = os.path.join(LIBRARY_DIR, fn)
    if not os.path.exists(path):
        return '', 404
    return send_file(path, conditional=True)

# ── WebSocket ─────────────────────────────────────────────────────────────────

@socketio.on('load_song')
def on_load_song(data):
    emit('load_song', data, broadcast=True)

@socketio.on('play')
def on_play():
    emit('play', broadcast=True)

@socketio.on('pause')
def on_pause():
    emit('pause', broadcast=True)

@socketio.on('stop')
def on_stop():
    emit('stop', broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
