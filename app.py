import eventlet
eventlet.monkey_patch()

import os, re, subprocess, json, uuid, threading, glob
from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

VIDEO_DIR = os.environ.get('VIDEO_DIR', '/app/video')
os.makedirs(VIDEO_DIR, exist_ok=True)

_state = {'song': None, 'playing': False}

# ── Download via yt-dlp OAuth2 ────────────────────────────────────────────────

YT_HOME = os.environ.get('YT_HOME', '/app/yt_home')
os.makedirs(YT_HOME, exist_ok=True)

def _download_video(video_id, out_path):
    r = subprocess.run([
        'yt-dlp',
        '--extractor-args', 'youtube:getpot_bgutil_baseurl=http://bgutil:4416',
        '-f', 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        '--merge-output-format', 'mp4',
        '-o', out_path, '--no-playlist',
        f'https://www.youtube.com/watch?v={video_id}',
    ], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(r.stderr[-400:] if r.stderr else 'Download fehlgeschlagen')

# ── HTML ──────────────────────────────────────────────────────────────────────

HOST_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Karaoke DJ</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f0f1a; color: #e0e0f0; min-height: 100vh; display: flex; align-items: flex-start; justify-content: center; padding: 2rem 1rem; }
    .card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 1.25rem; padding: 2.5rem; width: 100%; max-width: 640px; }
    h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.35rem; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .subtitle { color: #888; font-size: 0.9rem; margin-bottom: 2rem; }
    label { display: block; font-size: 0.8rem; font-weight: 600; color: #aaa; margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .row { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
    input[type="text"] { flex: 1; padding: 0.75rem 1rem; border-radius: 0.6rem; border: 1px solid #2a2a4a; background: #0f0f1a; color: #e0e0f0; font-size: 0.95rem; outline: none; }
    input[type="text"]:focus { border-color: #a78bfa; }
    .btn { padding: 0.75rem 1.25rem; border-radius: 0.6rem; border: none; background: linear-gradient(135deg, #7c3aed, #2563eb); color: #fff; font-size: 0.95rem; font-weight: 600; cursor: pointer; }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn:not(:disabled):hover { opacity: 0.85; }
    .btn-green { background: linear-gradient(135deg, #16a34a, #15803d); }
    .btn-red   { background: linear-gradient(135deg, #dc2626, #991b1b); }
    .btn-sm    { padding: 0.5rem 1rem; font-size: 0.85rem; }
    #results { display: none; margin-top: 0.5rem; }
    .result-item { display: flex; align-items: center; gap: 0.85rem; padding: 0.75rem; border-radius: 0.75rem; border: 1px solid #2a2a4a; margin-bottom: 0.5rem; cursor: pointer; transition: background 0.15s; }
    .result-item:hover { background: #252545; border-color: #a78bfa; }
    .result-item img { width: 80px; height: 52px; object-fit: cover; border-radius: 0.4rem; flex-shrink: 0; }
    .result-info { flex: 1; min-width: 0; }
    .result-title { font-size: 0.9rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .result-meta  { font-size: 0.78rem; color: #888; margin-top: 0.2rem; }
    .result-btn   { flex-shrink: 0; padding: 0.4rem 0.9rem; border-radius: 0.5rem; border: none; background: #7c3aed; color: #fff; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
    #loading-box { display: none; margin-top: 1.25rem; padding: 1.25rem; border-radius: 0.75rem; background: #1e1e3a; border: 1px solid #3a3a6a; color: #aaa; }
    #player-box  { display: none; margin-top: 1.25rem; padding: 1.25rem; border-radius: 0.75rem; border: 1px solid #2a5a9a; background: #1a2a3a; }
    .np-title   { font-size: 1rem; font-weight: 700; color: #60a5fa; margin-bottom: 0.2rem; }
    .np-channel { font-size: 0.82rem; color: #888; margin-bottom: 1rem; }
    .controls   { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .no-results { color: #666; font-size: 0.88rem; text-align: center; padding: 1rem 0; }
    .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #555; border-top-color: #a78bfa; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
<div class="card">
  <h1>&#127908; Karaoke DJ</h1>
  <p class="subtitle">Song suchen &amp; auf allen Screens abspielen</p>

  <label for="q">Song suchen</label>
  <div class="row">
    <input type="text" id="q" placeholder="z.B. Bohemian Rhapsody" onkeydown="if(event.key==='Enter')doSearch()"/>
    <button class="btn" id="search-btn" onclick="doSearch()">Suchen</button>
  </div>

  <div id="results"></div>

  <div id="loading-box">
    <span class="spinner"></span> Video wird heruntergeladen&hellip; (1&ndash;2 Min.)
  </div>

  <div id="player-box">
    <div class="np-title"   id="np-title"></div>
    <div class="np-channel" id="np-channel"></div>
    <div class="controls">
      <button class="btn btn-green btn-sm" onclick="doPlay()">&#9654; Play</button>
      <button class="btn btn-sm"           onclick="doPause()">&#9646;&#9646; Pause</button>
      <button class="btn btn-red   btn-sm" onclick="doStop()">&#9632; Stop</button>
    </div>
  </div>
</div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const socket = io();

  function ae(s) { return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }

  function doSearch() {
    const q = document.getElementById('q').value.trim();
    if (!q) return;
    const btn = document.getElementById('search-btn');
    btn.disabled = true; btn.textContent = 'Suche...';
    const el = document.getElementById('results');
    el.style.display = 'block';
    el.innerHTML = '<div class="no-results"><span class="spinner"></span> Suche...</div>';
    fetch('/search?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(vs => {
        btn.disabled = false; btn.textContent = 'Suchen';
        if (!vs.length) { el.innerHTML = '<div class="no-results">Keine Ergebnisse.</div>'; return; }
        el.innerHTML = vs.map(v => `
          <div class="result-item" data-id="${ae(v.id)}" data-title="${ae(v.title)}" data-channel="${ae(v.channel)}" onclick="pick(this)">
            <img src="https://i.ytimg.com/vi/${ae(v.id)}/mqdefault.jpg" loading="lazy"/>
            <div class="result-info">
              <div class="result-title">${v.title}</div>
              <div class="result-meta">${v.channel} &middot; ${v.duration}</div>
            </div>
            <button class="result-btn">Laden</button>
          </div>`).join('');
      })
      .catch(() => { btn.disabled=false; btn.textContent='Suchen'; });
  }

  function pick(el) {
    document.getElementById('results').style.display = 'none';
    document.getElementById('player-box').style.display = 'none';
    document.getElementById('loading-box').style.display = 'block';
    fetch('/load', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: el.dataset.id, title: el.dataset.title, channel: el.dataset.channel}),
    }).catch(e => { document.getElementById('loading-box').style.display='none'; alert('Fehler: '+e); });
  }

  socket.on('song_ready', song => {
    document.getElementById('loading-box').style.display = 'none';
    document.getElementById('np-title').textContent   = song.title;
    document.getElementById('np-channel').textContent = song.channel;
    document.getElementById('player-box').style.display = 'block';
  });

  socket.on('load_error', d => {
    document.getElementById('loading-box').style.display = 'none';
    alert('Fehler: ' + d.error);
  });

  function doPlay()  { socket.emit('play');  }
  function doPause() { socket.emit('pause'); }
  function doStop()  { socket.emit('stop');  }
</script>
</body>
</html>
"""

DISPLAY_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Karaoke</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { width: 100%; height: 100%; overflow: hidden; background: #000; }

    #activate {
      position: fixed; inset: 0; background: #000;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      cursor: pointer; z-index: 100;
    }
    #activate h1 { font-size: 5rem; font-weight: 900; letter-spacing: 0.2em; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-family: 'Segoe UI', sans-serif; }
    #activate p  { color: #555; font-size: 1.2rem; margin-top: 1rem; font-family: 'Segoe UI', sans-serif; }

    #standby {
      display: none; position: fixed; inset: 0; background: #000;
      flex-direction: column; align-items: center; justify-content: center;
    }
    #standby h1 { font-size: 5rem; font-weight: 900; letter-spacing: 0.2em; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-family: 'Segoe UI', sans-serif; }
    #standby p  { color: #444; font-size: 1.3rem; margin-top: 1rem; font-family: 'Segoe UI', sans-serif; }

    #video-wrap { display: none; width: 100%; height: 100%; }
    video { width: 100%; height: 100%; object-fit: contain; background: #000; }

    #conn { position: fixed; bottom: 0.5rem; right: 0.75rem; font-size: 0.7rem; padding: 0.2rem 0.6rem; border-radius: 1rem; background: rgba(0,0,0,0.6); color: #555; font-family: sans-serif; }
  </style>
</head>
<body>
  <!-- Step 1: click to activate audio/video autoplay -->
  <div id="activate" onclick="activate()">
    <h1>KARAOKE</h1>
    <p>Hier klicken zum Aktivieren</p>
  </div>

  <!-- Standby screen after activation -->
  <div id="standby">
    <h1>KARAOKE</h1>
    <p>Warte auf den DJ &hellip;</p>
  </div>

  <!-- Video player -->
  <div id="video-wrap">
    <video id="vid" preload="auto" playsinline></video>
  </div>

  <div id="conn">Verbinde...</div>

  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script>
    const socket = io({transports: ['polling', 'websocket']});
    const vid    = document.getElementById('vid');
    let activated = false;

    const connEl = document.getElementById('conn');
    socket.on('connect',    () => { connEl.textContent = 'Verbunden'; connEl.style.color = '#4ade80'; });
    socket.on('disconnect', () => { connEl.textContent = 'Getrennt';  connEl.style.color = '#f87171'; });

    function activate() {
      activated = true;
      document.getElementById('activate').style.display = 'none';
      document.getElementById('standby').style.display  = 'flex';
      // Trigger silent play to unlock autoplay policy
      vid.muted = true;
      vid.play().catch(() => {});
      vid.pause();
      vid.muted = false;
    }

    socket.on('song_ready', song => {
      vid.src = song.video_url;
      vid.load();
      document.getElementById('standby').style.display   = 'flex';
      document.getElementById('video-wrap').style.display = 'none';
    });

    socket.on('play', () => {
      document.getElementById('standby').style.display   = 'none';
      document.getElementById('video-wrap').style.display = 'block';
      vid.play().catch(e => console.warn('play blocked:', e));
    });

    socket.on('pause', () => { vid.pause(); });

    socket.on('stop', () => {
      vid.pause(); vid.src = '';
      document.getElementById('video-wrap').style.display = 'none';
      document.getElementById('standby').style.display   = 'flex';
    });

    // Reconnect sync: request current state
    socket.on('connect', () => {
      socket.emit('request_state');
    });
  </script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def host_page():
    return HOST_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/display')
def display_page():
    return DISPLAY_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/setup')
def setup_page():
    return '''<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"/>
<title>YouTube Setup</title>
<style>
body{font-family:sans-serif;background:#0f0f1a;color:#e0e0f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:1rem;padding:2rem;max-width:600px;width:100%;}
h1{font-size:1.4rem;margin-bottom:0.5rem;color:#a78bfa;}
p{color:#888;font-size:0.9rem;margin-bottom:1.5rem;}
.btn{padding:0.75rem 1.5rem;border-radius:0.6rem;border:none;background:linear-gradient(135deg,#7c3aed,#2563eb);color:#fff;font-size:1rem;font-weight:600;cursor:pointer;}
.btn:disabled{opacity:0.4;}
#log{margin-top:1.5rem;background:#000;border-radius:0.5rem;padding:1rem;font-family:monospace;font-size:0.85rem;white-space:pre-wrap;min-height:100px;color:#4ade80;display:none;}
a{color:#60a5fa;}
</style></head>
<body><div class="card">
<h1>&#128274; YouTube verknüpfen</h1>
<p>Klicke auf "Start" — du bekommst einen Link und einen Code.<br>
Öffne den Link, gib den Code ein und logge dich mit Google ein.<br>
Danach funktioniert der YouTube-Download dauerhaft.</p>
<button class="btn" id="btn" onclick="start()">Start</button>
<div id="log"></div>
</div>
<script>
function start(){
  const btn=document.getElementById("btn");
  const log=document.getElementById("log");
  btn.disabled=true; btn.textContent="Warte...";
  log.style.display="block"; log.textContent="";
  const es=new EventSource("/setup/stream");
  es.onmessage=e=>{
    if(e.data==="DONE"){es.close();btn.textContent="Fertig! YouTube verknüpft ✓";log.textContent+="\\n\\nErfolgreich! Du kannst diese Seite schliessen.";}
    else if(e.data==="ERROR"){es.close();btn.disabled=false;btn.textContent="Fehler — nochmal versuchen";}
    else{log.textContent+=e.data+"\\n";}
  };
  es.onerror=()=>{es.close();btn.disabled=false;btn.textContent="Verbindungsfehler";};
}
</script></body></html>
''', 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/setup/stream')
def setup_stream():
    def generate():
        env = {**os.environ, 'HOME': YT_HOME}
        proc = subprocess.Popen(
            ['yt-dlp', '--username', 'oauth2', '--password', '',
             '--skip-download', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                yield f'data: {line}\n\n'
        proc.wait()
        yield f'data: {"DONE" if proc.returncode == 0 else "ERROR"}\n\n'

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    result = subprocess.run(
        ['yt-dlp', f'ytsearch8:{q} karaoke',
         '--dump-json', '--flat-playlist', '--no-download'],
        capture_output=True, text=True, timeout=60,
    )
    videos = []
    for line in result.stdout.strip().splitlines():
        try:
            d = json.loads(line)
            vid_id = d.get('id', '')
            if not vid_id:
                continue
            videos.append({
                'id':       vid_id,
                'title':    d.get('title', 'Unknown'),
                'channel':  d.get('uploader') or d.get('channel', ''),
                'duration': d.get('duration_string') or '',
            })
        except Exception:
            continue
    return jsonify(videos)

@app.route('/load', methods=['POST'])
def load():
    data    = request.get_json(force=True) or {}
    vid_id  = data.get('id', '').strip()
    title   = data.get('title', '')
    channel = data.get('channel', '')
    if not vid_id:
        return jsonify({'error': 'Keine ID'}), 400

    job_id = str(uuid.uuid4())

    def work():
        try:
            out_path = os.path.join(VIDEO_DIR, f'{job_id}.mp4')
            _download_video(vid_id, out_path)

            song = {
                'job_id':    job_id,
                'title':     title,
                'channel':   channel,
                'video_url': f'/video/{job_id}',
            }
            _state['song']    = song
            _state['playing'] = False
            socketio.emit('song_ready', song, namespace='/')
        except Exception as e:
            socketio.emit('load_error', {'error': str(e)}, namespace='/')

    threading.Thread(target=work, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/video/<job_id>')
def serve_video(job_id):
    if not re.match(r'^[0-9a-f-]+$', job_id):
        return '', 404
    files = glob.glob(os.path.join(VIDEO_DIR, f'{job_id}.*'))
    if not files:
        return '', 404
    return send_file(files[0])

# ── WebSocket ─────────────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    if _state.get('song'):
        emit('song_ready', _state['song'])
        if _state['playing']:
            emit('play')

@socketio.on('request_state')
def on_request_state():
    if _state.get('song'):
        emit('song_ready', _state['song'])
        if _state['playing']:
            emit('play')

@socketio.on('play')
def on_play():
    _state['playing'] = True
    emit('play', broadcast=True)

@socketio.on('pause')
def on_pause():
    _state['playing'] = False
    emit('pause', broadcast=True)

@socketio.on('stop')
def on_stop():
    _state['song']    = None
    _state['playing'] = False
    emit('stop', broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
