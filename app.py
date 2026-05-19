import eventlet
eventlet.monkey_patch()

import subprocess, json
import requests
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

_state = {'song': None, 'start_ts': None, 'paused_pos': None}

INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net',
    'https://invidious.nerdvpn.de',
    'https://yewtu.be',
    'https://invidious.privacyredirect.com',
]

def _invidious(path, params=None):
    for base in INVIDIOUS_INSTANCES:
        try:
            r = requests.get(f'{base}/api/v1/{path}', params=params, timeout=8)
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

def _fetch_lyrics(title, artist):
    try:
        r = requests.get('https://lrclib.net/api/search',
                         params={'q': f'{artist} {title}'.strip()}, timeout=10)
        if r.ok:
            for item in r.json():
                lrc = item.get('syncedLyrics', '')
                if lrc:
                    return lrc
    except Exception:
        pass
    return ''

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
    .card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 1.25rem; padding: 2.5rem; width: 100%; max-width: 640px; box-shadow: 0 8px 40px rgba(0,0,0,0.5); }
    h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.35rem; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .subtitle { color: #888; font-size: 0.9rem; margin-bottom: 2rem; }
    label { display: block; font-size: 0.8rem; font-weight: 600; color: #aaa; margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .row { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
    input[type="text"] { flex: 1; padding: 0.75rem 1rem; border-radius: 0.6rem; border: 1px solid #2a2a4a; background: #0f0f1a; color: #e0e0f0; font-size: 0.95rem; outline: none; }
    input[type="text"]:focus { border-color: #a78bfa; }
    .btn { padding: 0.75rem 1.25rem; border-radius: 0.6rem; border: none; background: linear-gradient(135deg, #7c3aed, #2563eb); color: #fff; font-size: 0.95rem; font-weight: 600; cursor: pointer; white-space: nowrap; }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn:not(:disabled):hover { opacity: 0.85; }
    .btn-red { background: linear-gradient(135deg, #dc2626, #991b1b); }
    #results { display: none; margin-top: 0.5rem; }
    .result-item { display: flex; align-items: center; gap: 0.85rem; padding: 0.75rem; border-radius: 0.75rem; border: 1px solid #2a2a4a; margin-bottom: 0.5rem; cursor: pointer; transition: background 0.15s, border-color 0.15s; }
    .result-item:hover { background: #252545; border-color: #a78bfa; }
    .result-item img { width: 80px; height: 52px; object-fit: cover; border-radius: 0.4rem; flex-shrink: 0; }
    .result-info { flex: 1; min-width: 0; }
    .result-title { font-size: 0.9rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 0.2rem; }
    .result-meta { font-size: 0.78rem; color: #888; }
    .result-btn { flex-shrink: 0; padding: 0.4rem 0.9rem; border-radius: 0.5rem; border: none; background: #7c3aed; color: #fff; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
    .result-btn:hover { background: #6d28d9; }
    #player-box { display: none; margin-top: 1.5rem; }
    #player-box h2 { font-size: 1rem; font-weight: 700; color: #60a5fa; margin-bottom: 0.25rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #player-box .channel { font-size: 0.82rem; color: #888; margin-bottom: 0.75rem; }
    #player-box .lyrics-badge { font-size: 0.78rem; margin-bottom: 0.75rem; }
    .has-lyrics { color: #4ade80; }
    .no-lyrics { color: #f87171; }
    #yt-player { width: 100%; border-radius: 0.75rem; aspect-ratio: 16/9; border: none; background: #000; }
    .np-actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
    .no-results { color: #666; font-size: 0.88rem; text-align: center; padding: 1rem 0; }
    .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #555; border-top-color: #a78bfa; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
<div class="card">
  <h1>&#127908; Karaoke DJ</h1>
  <p class="subtitle">Song suchen &amp; auf allen Screens abspielen</p>

  <label for="search-input">Song suchen</label>
  <div class="row">
    <input type="text" id="search-input" placeholder="z.B. Bohemian Rhapsody" onkeydown="if(event.key==='Enter') doSearch()"/>
    <button class="btn" id="search-btn" onclick="doSearch()">Suchen</button>
  </div>

  <div id="results"></div>

  <div id="player-box">
    <h2 id="np-title"></h2>
    <div class="channel" id="np-channel"></div>
    <div class="lyrics-badge" id="np-badge"></div>
    <div id="yt-player"></div>
    <div class="np-actions">
      <button class="btn btn-red" onclick="stopSong()">&#9632; Stop</button>
    </div>
  </div>
</div>

<script src="https://www.youtube.com/iframe_api"></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const socket = io();
  let ytPlayer = null;
  let syncInterval = null;
  let currentSong = null;

  function ae(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  function doSearch() {
    const q = document.getElementById('search-input').value.trim();
    if (!q) return;
    const btn = document.getElementById('search-btn');
    btn.disabled = true; btn.textContent = 'Suche...';
    const el = document.getElementById('results');
    el.style.display = 'block';
    el.innerHTML = '<div class="no-results"><span class="spinner"></span> Suche...</div>';

    fetch('/search?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(videos => {
        btn.disabled = false; btn.textContent = 'Suchen';
        if (!videos.length) { el.innerHTML = '<div class="no-results">Keine Ergebnisse.</div>'; return; }
        el.innerHTML = videos.map(v => `
          <div class="result-item"
               data-id="${ae(v.id)}" data-title="${ae(v.title)}"
               data-channel="${ae(v.channel)}"
               onclick="pickSong(this)">
            <img src="https://i.ytimg.com/vi/${ae(v.id)}/mqdefault.jpg" alt="" loading="lazy"/>
            <div class="result-info">
              <div class="result-title">${v.title}</div>
              <div class="result-meta">${v.channel} &middot; ${v.duration}</div>
            </div>
            <button class="result-btn">Abspielen</button>
          </div>`).join('');
      })
      .catch(() => {
        btn.disabled = false; btn.textContent = 'Suchen';
        el.innerHTML = '<div class="no-results">Suche fehlgeschlagen.</div>';
      });
  }

  function pickSong(el) {
    const id      = el.dataset.id;
    const title   = el.dataset.title;
    const channel = el.dataset.channel;
    document.getElementById('results').style.display = 'none';

    fetch('/prepare', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, title, channel}),
    })
    .then(r => r.json())
    .then(data => {
      currentSong = data;
      document.getElementById('np-title').textContent   = title;
      document.getElementById('np-channel').textContent = channel;
      const badge = document.getElementById('np-badge');
      badge.textContent  = data.lrc ? 'Songtext gefunden' : 'Kein Songtext gefunden';
      badge.className    = 'lyrics-badge ' + (data.lrc ? 'has-lyrics' : 'no-lyrics');
      document.getElementById('player-box').style.display = 'block';
      loadYT(id);
    });
  }

  function loadYT(videoId) {
    if (ytPlayer) { ytPlayer.destroy(); ytPlayer = null; }
    clearInterval(syncInterval);

    ytPlayer = new YT.Player('yt-player', {
      videoId,
      playerVars: {autoplay: 1, rel: 0, modestbranding: 1},
      events: {
        onStateChange(e) {
          if (e.data === YT.PlayerState.PLAYING) {
            emitPlay();
            syncInterval = setInterval(emitPlay, 5000);
          } else if (e.data === YT.PlayerState.PAUSED || e.data === YT.PlayerState.ENDED) {
            clearInterval(syncInterval);
            socket.emit('pause', {position: Math.round(ytPlayer.getCurrentTime() * 1000)});
          }
        },
        onReady() { ytPlayer.playVideo(); }
      }
    });
  }

  function emitPlay() {
    if (!ytPlayer) return;
    const startTs = Date.now() - Math.round(ytPlayer.getCurrentTime() * 1000);
    socket.emit('play', {start_ts: startTs});
  }

  function stopSong() {
    clearInterval(syncInterval);
    if (ytPlayer) { ytPlayer.stopVideo(); ytPlayer.destroy(); ytPlayer = null; }
    socket.emit('stop');
    document.getElementById('player-box').style.display = 'none';
    document.getElementById('yt-player').innerHTML = '';
    currentSong = null;
  }

  function onYouTubeIframeAPIReady() {}
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

    #standby { text-align: center; }
    #standby h1 { font-size: 6rem; font-weight: 900; letter-spacing: 0.2em; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    #standby p { color: #444; font-size: 1.3rem; margin-top: 1rem; }

    #song-info { position: fixed; top: 1.5rem; left: 0; right: 0; text-align: center; display: none; }
    #song-info .s-title  { font-size: 1.2rem; font-weight: 700; color: #a78bfa; }
    #song-info .s-artist { font-size: 0.95rem; color: #555; margin-top: 0.2rem; }

    #lyrics { display: none; width: 100%; text-align: center; padding: 4rem 4rem 2rem; }
    #line-current {
      font-size: clamp(2rem, 5vw, 4rem); font-weight: 700; color: #fff;
      line-height: 1.3; margin-bottom: 1.5rem;
      text-shadow: 0 0 60px rgba(167,139,250,0.5); min-height: 1.3em;
    }
    #line-next { font-size: clamp(1.2rem, 3vw, 2.2rem); color: #444; line-height: 1.4; min-height: 1.4em; }
    #waiting { display: none; color: #333; font-size: 2rem; text-align: center; }
  </style>
</head>
<body>
  <div id="standby">
    <h1>KARAOKE</h1>
    <p>Warte auf den DJ &hellip;</p>
  </div>
  <div id="song-info">
    <div class="s-title"  id="info-title"></div>
    <div class="s-artist" id="info-artist"></div>
  </div>
  <div id="lyrics">
    <div id="line-current"></div>
    <div id="line-next"></div>
  </div>
  <div id="waiting">&#9835; &nbsp; &#9835; &nbsp; &#9835;</div>

  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script>
    const socket = io();
    let lrcLines = [], ticker = null, startTs = null;

    function parseLRC(lrc) {
      const lines = [];
      for (const line of lrc.split('\n')) {
        const m = line.match(/\[(\d+):(\d+)\.(\d+)\](.*)/);
        if (!m) continue;
        const ms = (parseInt(m[1]) * 60 + parseInt(m[2])) * 1000
                 + parseInt(m[3].padEnd(3,'0').slice(0,3));
        const text = m[4].trim();
        if (text) lines.push({ms, text});
      }
      return lines.sort((a, b) => a.ms - b.ms);
    }

    function updateLyrics(posMs) {
      let idx = -1;
      for (let i = 0; i < lrcLines.length; i++) {
        if (lrcLines[i].ms <= posMs) idx = i; else break;
      }
      document.getElementById('line-current').textContent = idx >= 0 ? lrcLines[idx].text : '';
      document.getElementById('line-next').textContent    = idx + 1 < lrcLines.length ? lrcLines[idx+1].text : '';
    }

    socket.on('song_ready', (song) => {
      clearInterval(ticker); startTs = null;
      lrcLines = song.lrc ? parseLRC(song.lrc) : [];
      document.getElementById('standby').style.display    = 'none';
      document.getElementById('info-title').textContent   = song.title;
      document.getElementById('info-artist').textContent  = song.channel;
      document.getElementById('song-info').style.display  = 'block';
      document.getElementById('line-current').textContent = '';
      document.getElementById('line-next').textContent    = lrcLines.length ? lrcLines[0].text : '';
      document.getElementById('lyrics').style.display     = lrcLines.length ? 'block' : 'none';
      document.getElementById('waiting').style.display    = lrcLines.length ? 'none' : 'block';
    });

    socket.on('play', (data) => {
      startTs = data.start_ts;
      clearInterval(ticker);
      ticker = setInterval(() => updateLyrics(Date.now() - startTs), 200);
    });

    socket.on('pause', (data) => {
      clearInterval(ticker); startTs = null; updateLyrics(data.position);
    });

    socket.on('stop', () => {
      clearInterval(ticker); startTs = null; lrcLines = [];
      document.getElementById('standby').style.display = 'block';
      document.getElementById('song-info').style.display = 'none';
      document.getElementById('lyrics').style.display = 'none';
      document.getElementById('waiting').style.display = 'none';
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

    # yt-dlp flat-playlist search — no bot detection, only fetches metadata
    result = subprocess.run([
        'yt-dlp', f'ytsearch8:{q} karaoke',
        '--dump-json', '--flat-playlist', '--no-download',
    ], capture_output=True, text=True, timeout=60)

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

    # fallback to Invidious if yt-dlp returned nothing
    if not videos:
        data = _invidious('search', {'q': f'{q} karaoke', 'type': 'video'})
        for item in (data or [])[:8]:
            vid_id = item.get('videoId', '')
            if vid_id:
                videos.append({
                    'id':       vid_id,
                    'title':    item.get('title', 'Unknown'),
                    'channel':  item.get('author', ''),
                    'duration': _duration_str(item.get('lengthSeconds', 0)),
                })

    return jsonify(videos)

@app.route('/prepare', methods=['POST'])
def prepare():
    data    = request.get_json(force=True) or {}
    vid_id  = data.get('id', '')
    title   = data.get('title', '')
    channel = data.get('channel', '')

    lrc = _fetch_lyrics(title, channel)

    song = {'id': vid_id, 'title': title, 'channel': channel, 'lrc': lrc}
    _state['song']       = song
    _state['start_ts']   = None
    _state['paused_pos'] = None

    socketio.emit('song_ready', song)
    return jsonify(song)

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
