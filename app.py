import eventlet
eventlet.monkey_patch()

import os, re, subprocess, json, uuid, threading, glob
import requests
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

AUDIO_DIR = os.environ.get('AUDIO_DIR', '/app/audio')
os.makedirs(AUDIO_DIR, exist_ok=True)

_state = {'song': None, 'start_ts': None, 'paused_pos': None}

INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net',
    'https://invidious.nerdvpn.de',
    'https://yewtu.be',
    'https://invidious.privacyredirect.com',
    'https://invidious.flokinet.to',
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _invidious(path, params=None):
    for base in INVIDIOUS_INSTANCES:
        try:
            r = requests.get(f'{base}/api/v1/{path}', params=params, timeout=10)
            if r.ok:
                return r.json()
        except Exception:
            continue
    return None

def _duration_str(secs):
    secs = int(secs or 0)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

def _fetch_lyrics(title, channel):
    clean = re.sub(r'\s*[\(\[].*?(karaoke|instrumental|no vocal|backing).*?[\)\]]',
                   '', title, flags=re.IGNORECASE).strip()
    if ' - ' in clean:
        artist, song = clean.split(' - ', 1)
    else:
        artist = re.sub(r'(?i)\s*(karaoke|sings?|covers?)\s*', ' ', channel).strip()
        song   = clean
    try:
        r = requests.get('https://lrclib.net/api/search',
                         params={'q': f'{artist} {song}'.strip()}, timeout=10)
        if r.ok:
            for item in r.json():
                lrc = item.get('syncedLyrics', '')
                if lrc:
                    return lrc
    except Exception:
        pass
    return ''

def _download_audio(video_id, out_path):
    """Download audio via Invidious stream URL + ffmpeg conversion."""
    data = _invidious(f'videos/{video_id}')
    if not data:
        raise RuntimeError('Kein Invidious-Server erreichbar')

    # Pick best audio-only stream
    fmts = [f for f in data.get('adaptiveFormats', [])
            if 'audio' in f.get('type', '')]
    if not fmts:
        raise RuntimeError('Kein Audio-Stream gefunden')
    fmts.sort(key=lambda x: int(x.get('bitrate', 0)), reverse=True)
    stream_url = fmts[0]['url']

    # Download raw stream to temp file
    tmp = out_path + '.tmp'
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; bot)'}
    with requests.get(stream_url, stream=True, timeout=180, headers=headers) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)

    # Convert to mp3
    result = subprocess.run(
        ['ffmpeg', '-y', '-i', tmp, '-acodec', 'libmp3lame', '-q:a', '4', out_path],
        capture_output=True, timeout=180,
    )
    try:
        os.unlink(tmp)
    except OSError:
        pass
    if result.returncode != 0:
        raise RuntimeError(f'ffmpeg: {result.stderr.decode()[-300:]}')

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
    .btn-red { background: linear-gradient(135deg, #dc2626, #991b1b); }
    .btn-sm  { padding: 0.5rem 1rem; font-size: 0.85rem; }
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
    .np-channel { font-size: 0.82rem; color: #888; margin-bottom: 0.5rem; }
    .np-badge   { font-size: 0.78rem; margin-bottom: 0.75rem; }
    .has-lyrics { color: #4ade80; }
    .no-lyrics  { color: #f87171; }
    audio { width: 100%; margin-bottom: 0.75rem; }
    .offset-row { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem; font-size: 0.82rem; color: #aaa; }
    .btn-off { padding: 0.3rem 0.7rem; border-radius: 0.4rem; border: 1px solid #2a2a4a; background: #1a1a2e; color: #e0e0f0; cursor: pointer; font-size: 0.8rem; }
    .btn-off:hover { background: #252545; }
    .off-val { min-width: 2rem; text-align: center; color: #60a5fa; font-weight: 600; }
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
    <span class="spinner"></span> Song wird heruntergeladen&hellip; (30&ndash;60 Sek.)
  </div>

  <div id="player-box">
    <div class="np-title"   id="np-title"></div>
    <div class="np-channel" id="np-channel"></div>
    <div class="np-badge"   id="np-badge"></div>
    <audio id="audio" controls preload="auto"></audio>
    <div class="offset-row">
      Lyrics-Offset:
      <button class="btn-off" onclick="shift(-3)">&#8722;3s</button>
      <span class="off-val" id="off-val">0s</span>
      <button class="btn-off" onclick="shift(3)">+3s</button>
      <button class="btn btn-red btn-sm" style="margin-left:auto" onclick="stopSong()">&#9632; Stop</button>
    </div>
  </div>
</div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const socket = io();
  const audio  = document.getElementById('audio');
  let offsetMs = 0;

  function ae(s) { return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }

  // ── Search ──────────────────────────────────────────────────────────────────
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
      .catch(() => { btn.disabled=false; btn.textContent='Suchen'; el.innerHTML='<div class="no-results">Fehler.</div>'; });
  }

  // ── Pick & load ─────────────────────────────────────────────────────────────
  function pick(el) {
    document.getElementById('results').style.display = 'none';
    document.getElementById('player-box').style.display = 'none';
    document.getElementById('loading-box').style.display = 'block';
    offsetMs = 0; document.getElementById('off-val').textContent = '0s';
    fetch('/load', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id: el.dataset.id, title: el.dataset.title, channel: el.dataset.channel}),
    }).catch(() => {
      document.getElementById('loading-box').style.display = 'none';
      alert('Fehler beim Laden');
    });
  }

  socket.on('song_ready', song => {
    document.getElementById('loading-box').style.display = 'none';
    document.getElementById('np-title').textContent   = song.title;
    document.getElementById('np-channel').textContent = song.channel;
    const badge = document.getElementById('np-badge');
    badge.textContent = song.lrc ? '✓ Songtext gefunden' : '✗ Kein Songtext gefunden';
    badge.className   = 'np-badge ' + (song.lrc ? 'has-lyrics' : 'no-lyrics');
    audio.src = song.audio_url;
    audio.load();
    document.getElementById('player-box').style.display = 'block';
  });

  socket.on('load_error', d => {
    document.getElementById('loading-box').style.display = 'none';
    alert('Fehler: ' + d.error);
  });

  // ── Audio events → sync displays ────────────────────────────────────────────
  audio.addEventListener('play', emitPlay);
  audio.addEventListener('seeked', () => { if (!audio.paused) emitPlay(); });
  audio.addEventListener('pause', () => {
    socket.emit('pause', {position: Math.round(audio.currentTime * 1000)});
  });

  function emitPlay() {
    socket.emit('play', {start_ts: Date.now() - Math.round(audio.currentTime * 1000) + offsetMs});
  }

  // ── Offset ──────────────────────────────────────────────────────────────────
  function shift(sec) {
    offsetMs += sec * 1000;
    document.getElementById('off-val').textContent = (offsetMs / 1000) + 's';
    if (!audio.paused) emitPlay();
    else socket.emit('pause', {position: Math.round(audio.currentTime * 1000) - offsetMs});
  }

  // ── Stop ────────────────────────────────────────────────────────────────────
  function stopSong() {
    audio.pause(); audio.src = '';
    socket.emit('stop');
    document.getElementById('player-box').style.display = 'none';
  }
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
    html, body { width: 100%; height: 100%; overflow: hidden; background: #000; color: #fff; font-family: 'Segoe UI', system-ui, sans-serif; }
    body { display: flex; flex-direction: column; align-items: center; justify-content: center; }
    #standby h1 { font-size: 6rem; font-weight: 900; letter-spacing: 0.2em; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-align: center; }
    #standby p  { color: #444; font-size: 1.3rem; margin-top: 1rem; text-align: center; }
    #song-info  { position: fixed; top: 1.5rem; left: 0; right: 0; text-align: center; display: none; }
    .s-title  { font-size: 1.2rem; font-weight: 700; color: #a78bfa; }
    .s-artist { font-size: 0.95rem; color: #555; margin-top: 0.2rem; }
    #lyrics { display: none; width: 100%; text-align: center; padding: 5rem 4rem 2rem; }
    #line-current { font-size: clamp(2rem,5vw,4rem); font-weight: 700; color: #fff; line-height: 1.3; margin-bottom: 1.5rem; text-shadow: 0 0 60px rgba(167,139,250,0.5); min-height: 1.3em; }
    #line-next    { font-size: clamp(1.2rem,3vw,2.2rem); color: #444; line-height: 1.4; min-height: 1.4em; }
    #waiting { display: none; color: #333; font-size: 2rem; text-align: center; }
    #conn { position: fixed; bottom: 0.75rem; right: 0.75rem; font-size: 0.7rem; padding: 0.2rem 0.6rem; border-radius: 1rem; background: #111; color: #555; }
  </style>
</head>
<body>
  <div id="standby"><h1>KARAOKE</h1><p>Warte auf den DJ &hellip;</p></div>
  <div id="song-info">
    <div class="s-title"  id="info-title"></div>
    <div class="s-artist" id="info-artist"></div>
  </div>
  <div id="lyrics">
    <div id="line-current"></div>
    <div id="line-next"></div>
  </div>
  <div id="waiting">&#9835; &nbsp; &#9835; &nbsp; &#9835;</div>
  <div id="conn">Verbinde...</div>

  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script>
    const socket = io({transports:['polling','websocket']});
    let lrcLines=[], ticker=null, startTs=null;
    const connEl = document.getElementById('conn');
    socket.on('connect',    ()=>{ connEl.textContent='Verbunden'; connEl.style.color='#4ade80'; });
    socket.on('disconnect', ()=>{ connEl.textContent='Getrennt';  connEl.style.color='#f87171'; });

    function parseLRC(lrc) {
      const lines=[];
      for (const line of lrc.split('\n')) {
        const m = line.match(/\[(\d+):(\d+)\.(\d+)\](.*)/);
        if (!m) continue;
        const ms = (parseInt(m[1])*60+parseInt(m[2]))*1000 + parseInt(m[3].padEnd(3,'0').slice(0,3));
        const text = m[4].trim();
        if (text) lines.push({ms,text});
      }
      return lines.sort((a,b)=>a.ms-b.ms);
    }

    function updateLyrics(posMs) {
      let idx=-1;
      for (let i=0;i<lrcLines.length;i++) { if(lrcLines[i].ms<=posMs) idx=i; else break; }
      document.getElementById('line-current').textContent = idx>=0 ? lrcLines[idx].text : '';
      document.getElementById('line-next').textContent    = idx+1<lrcLines.length ? lrcLines[idx+1].text : '';
    }

    socket.on('song_ready', song => {
      clearInterval(ticker); startTs=null;
      lrcLines = song.lrc ? parseLRC(song.lrc) : [];
      document.getElementById('standby').style.display   = 'none';
      document.getElementById('info-title').textContent  = song.title;
      document.getElementById('info-artist').textContent = song.channel;
      document.getElementById('song-info').style.display = 'block';
      document.getElementById('line-current').textContent = '';
      document.getElementById('line-next').textContent    = lrcLines.length ? lrcLines[0].text : '';
      document.getElementById('lyrics').style.display    = lrcLines.length ? 'block' : 'none';
      document.getElementById('waiting').style.display   = lrcLines.length ? 'none'  : 'block';
    });

    socket.on('play', data => {
      startTs = data.start_ts;
      clearInterval(ticker);
      ticker = setInterval(()=>updateLyrics(Date.now()-startTs), 200);
    });

    socket.on('pause', data => {
      clearInterval(ticker); startTs=null; updateLyrics(data.position);
    });

    socket.on('stop', ()=>{
      clearInterval(ticker); startTs=null; lrcLines=[];
      document.getElementById('standby').style.display   = 'block';
      document.getElementById('song-info').style.display = 'none';
      document.getElementById('lyrics').style.display    = 'none';
      document.getElementById('waiting').style.display   = 'none';
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
    if not videos:
        data = _invidious('search', {'q': f'{q} karaoke', 'type': 'video'})
        for item in (data or [])[:8]:
            vid_id = item.get('videoId', '')
            if vid_id:
                videos.append({'id': vid_id, 'title': item.get('title','Unknown'),
                               'channel': item.get('author',''),
                               'duration': _duration_str(item.get('lengthSeconds',0))})
    return jsonify(videos)

@app.route('/load', methods=['POST'])
def load():
    data    = request.get_json(force=True) or {}
    vid_id  = data.get('id', '').strip()
    title   = data.get('title', '')
    channel = data.get('channel', '')
    if not vid_id:
        return jsonify({'error': 'Keine Video-ID'}), 400

    job_id = str(uuid.uuid4())

    def work():
        try:
            out_mp3 = os.path.join(AUDIO_DIR, f'{job_id}.mp3')
            _download_audio(vid_id, out_mp3)
            lrc  = _fetch_lyrics(title, channel)
            song = {
                'job_id':    job_id,
                'title':     title,
                'channel':   channel,
                'audio_url': f'/audio/{job_id}',
                'lrc':       lrc,
            }
            _state['song']       = song
            _state['start_ts']   = None
            _state['paused_pos'] = None
            socketio.emit('song_ready', song, namespace='/')
        except Exception as e:
            socketio.emit('load_error', {'error': str(e)}, namespace='/')

    threading.Thread(target=work, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/audio/<job_id>')
def serve_audio(job_id):
    if not re.match(r'^[0-9a-f-]+$', job_id):
        return '', 404
    files = glob.glob(os.path.join(AUDIO_DIR, f'{job_id}.*'))
    if not files:
        return '', 404
    return send_file(files[0])

# ── WebSocket ─────────────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    song = _state.get('song')
    if not song:
        return
    emit('song_ready', song)
    if _state['start_ts'] is not None:
        emit('play', {'start_ts': _state['start_ts']})
    elif _state['paused_pos'] is not None:
        emit('pause', {'position': _state['paused_pos']})

@socketio.on('play')
def on_play(data):
    _state['start_ts']   = data.get('start_ts')
    _state['paused_pos'] = None
    emit('play', data, broadcast=True)

@socketio.on('pause')
def on_pause(data):
    _state['paused_pos'] = data.get('position')
    _state['start_ts']   = None
    emit('pause', data, broadcast=True)

@socketio.on('stop')
def on_stop():
    _state['song'] = _state['start_ts'] = _state['paused_pos'] = None
    emit('stop', {}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
