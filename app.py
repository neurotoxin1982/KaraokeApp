import eventlet
eventlet.monkey_patch()

import os, glob
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024  # 4 GB

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', '/app/library')
os.makedirs(LIBRARY_DIR, exist_ok=True)

AUDIO_EXT = {'.mp3', '.m4a', '.wav', '.ogg'}
VIDEO_EXT = {'.mp4', '.webm', '.mkv', '.mov', '.avi', '.m4v'}
ALLOWED   = AUDIO_EXT | VIDEO_EXT | {'.cdg'}

def clean_name(base):
    return base.replace('_', ' ').strip()

# ── Library ───────────────────────────────────────────────────────────────────

def get_library():
    by_base = {}
    for path in glob.glob(os.path.join(LIBRARY_DIR, '*')):
        fn  = os.path.basename(path)
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ALLOWED:
            continue
        base = os.path.splitext(fn)[0]
        by_base.setdefault(base, {})[ext] = fn

    songs = []
    for base in sorted(by_base.keys(), key=str.lower):
        files = by_base[base]
        cdg = files.get('.cdg')
        mp3 = files.get('.mp3') or next((files[e] for e in AUDIO_EXT if e in files), None)
        mp4 = next((files[e] for e in VIDEO_EXT if e in files), None)

        if cdg and mp3:
            songs.append({'display': clean_name(base), 'type': 'cdg',   'mp3': mp3, 'cdg': cdg})
        elif mp4:
            songs.append({'display': clean_name(base), 'type': 'video', 'video': mp4})
        elif mp3:
            songs.append({'display': clean_name(base), 'type': 'audio', 'audio': mp3})
    return songs

# ── HTML ──────────────────────────────────────────────────────────────────────

DJ_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Karaoke Library</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f0f1a;color:#e0e0f0;height:100vh;display:flex;flex-direction:column;}
    .header{background:#1a1a2e;border-bottom:1px solid #2a2a4a;padding:0.85rem 1.5rem;display:flex;align-items:center;gap:1rem;flex-shrink:0;}
    .header h1{font-size:1.3rem;font-weight:700;background:linear-gradient(135deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;white-space:nowrap;}
    .header input{flex:1;max-width:380px;padding:0.55rem 0.9rem;border-radius:0.5rem;border:1px solid #2a2a4a;background:#0f0f1a;color:#e0e0f0;font-size:0.9rem;outline:none;}
    .header input:focus{border-color:#a78bfa;}
    .layout{display:flex;flex:1;overflow:hidden;}
    .sidebar{width:260px;flex-shrink:0;border-right:1px solid #2a2a4a;padding:1rem;overflow-y:auto;display:flex;flex-direction:column;gap:0.75rem;}
    .sidebar h2{font-size:0.78rem;font-weight:600;color:#666;text-transform:uppercase;letter-spacing:.05em;}
    .drop-zone{border:2px dashed #2a2a4a;border-radius:.75rem;padding:1.25rem;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;font-size:2rem;}
    .drop-zone p{font-size:0.8rem;color:#666;margin-top:.35rem;}
    .drop-zone:hover,.drop-zone.over{border-color:#a78bfa;background:#1a1a3a;}
    .drop-zone input{display:none;}
    .progress{height:3px;background:#2a2a4a;border-radius:2px;overflow:hidden;}
    .progress-fill{height:100%;background:linear-gradient(90deg,#a78bfa,#60a5fa);width:0;transition:width .2s;}
    .upload-log{font-size:.78rem;color:#666;max-height:120px;overflow-y:auto;}
    .upload-log div{padding:.2rem 0;border-bottom:1px solid #1a1a2e;display:flex;justify-content:space-between;}
    .ok{color:#4ade80;}.err{color:#f87171;}
    .main{flex:1;overflow-y:auto;padding:.75rem 1rem;}
    .count{font-size:.78rem;color:#444;margin-bottom:.6rem;}
    .song{display:flex;align-items:center;gap:.6rem;padding:.65rem .85rem;border-radius:.6rem;border:1px solid #2a2a4a;margin-bottom:.35rem;cursor:pointer;transition:background .15s;}
    .song:hover{background:#1a1a3a;border-color:#a78bfa;}
    .song.playing{background:#0d1f12;border-color:#4ade80;}
    .song-icon{font-size:1.1rem;flex-shrink:0;}
    .song-name{flex:1;font-size:.88rem;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .song-type{font-size:.7rem;color:#555;flex-shrink:0;}
    .play-btn{flex-shrink:0;padding:.25rem .7rem;border-radius:.35rem;border:none;background:#7c3aed;color:#fff;font-size:.78rem;font-weight:600;cursor:pointer;}
    .play-btn:hover{background:#6d28d9;}
    .now-bar{display:none;background:#1a1a2e;border-top:1px solid #2a2a4a;padding:.6rem 1.5rem;align-items:center;gap:.75rem;flex-shrink:0;}
    .now-bar.visible{display:flex;}
    .now-name{flex:1;font-size:.88rem;font-weight:600;color:#4ade80;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .cbtn{padding:.35rem .9rem;border-radius:.35rem;border:none;font-size:.82rem;font-weight:600;cursor:pointer;}
    .c-play{background:#16a34a;color:#fff;}.c-pause{background:#2563eb;color:#fff;}.c-stop{background:#dc2626;color:#fff;}
    .empty{text-align:center;color:#444;padding:3rem;font-size:.9rem;}
  </style>
</head>
<body>
<div class="header">
  <h1>&#127908; Karaoke Library</h1>
  <input type="text" id="q" placeholder="Song suchen&#8230;" oninput="filter()"/>
</div>
<div class="layout">
  <div class="sidebar">
    <h2>Songs hochladen</h2>
    <div class="drop-zone" id="dz" onclick="document.getElementById('fi').click()"
         ondragover="ev.preventDefault();dz.classList.add('over')"
         ondragleave="document.getElementById('dz').classList.remove('over')"
         ondrop="drop(event)">
      &#128190;
      <p>Klicken oder Dateien<br>reinziehen<br><small style="color:#555">MP3 + CDG, MP4</small></p>
      <input type="file" id="fi" multiple accept=".mp3,.cdg,.mp4,.webm,.m4v,.wav" onchange="handleFiles(this.files)"/>
    </div>
    <div class="progress"><div class="progress-fill" id="pbar"></div></div>
    <h2>Uploads</h2>
    <div class="upload-log" id="ulog"></div>
  </div>
  <div class="main">
    <div class="count" id="cnt"></div>
    <div id="list"></div>
  </div>
</div>
<div class="now-bar" id="now">
  <span class="now-name" id="now-name"></span>
  <button class="cbtn c-play"  onclick="doPlay()">&#9654;</button>
  <button class="cbtn c-pause" onclick="doPause()">&#9646;&#9646;</button>
  <button class="cbtn c-stop"  onclick="doStop()">&#9632;</button>
</div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const socket = io();
  let all = [], current = null, win = null;

  function ae(s){return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;');}

  function load(){
    fetch('/library').then(r=>r.json()).then(s=>{all=s;render(s);});
  }

  function render(songs){
    document.getElementById('cnt').textContent = songs.length+' Songs';
    const el=document.getElementById('list');
    if(!songs.length){el.innerHTML='<div class="empty">Noch keine Songs. Lade MP3+CDG oder MP4 Dateien hoch.</div>';return;}
    el.innerHTML=songs.map(s=>`
      <div class="song ${JSON.stringify(s)===JSON.stringify(current)?'playing':''}"
           onclick="play(${ae(JSON.stringify(s))})" data-song='${ae(JSON.stringify(s))}'>
        <span class="song-icon">${s.type==='cdg'?'&#127908;':s.type==='video'?'&#127916;':'&#127925;'}</span>
        <span class="song-name">${s.display}</span>
        <span class="song-type">${s.type.toUpperCase()}</span>
        <button class="play-btn">&#9654;</button>
      </div>`).join('');
  }

  function filter(){
    const q=document.getElementById('q').value.toLowerCase();
    render(q?all.filter(s=>s.display.toLowerCase().includes(q)):all);
  }

  function play(song){
    current=song;
    if(!win||win.closed) win=window.open('/player','karaoke_player','width=1280,height=720');
    setTimeout(()=>socket.emit('load_song',song),900);
    document.getElementById('now-name').textContent=song.display;
    document.getElementById('now').classList.add('visible');
    render(document.getElementById('q').value?all.filter(s=>s.display.toLowerCase().includes(document.getElementById('q').value.toLowerCase())):all);
  }

  function doPlay() {socket.emit('play');}
  function doPause(){socket.emit('pause');}
  function doStop(){
    socket.emit('stop'); current=null;
    document.getElementById('now').classList.remove('visible');
    render(all);
  }

  function drop(e){e.preventDefault();document.getElementById('dz').classList.remove('over');handleFiles(e.dataTransfer.files);}
  function handleFiles(files){[...files].forEach(upload);}

  async function upload(file){
    const log=document.getElementById('ulog');
    const row=document.createElement('div');
    row.innerHTML=`<span>${file.name.slice(0,28)}</span><span>&#8230;</span>`;
    log.prepend(row);
    const fd=new FormData(); fd.append('file',file);
    const xhr=new XMLHttpRequest(); xhr.open('POST','/upload');
    xhr.upload.onprogress=e=>{if(e.lengthComputable)document.getElementById('pbar').style.width=(e.loaded/e.total*100)+'%';};
    await new Promise((res,rej)=>{xhr.onload=()=>xhr.status===200?res():rej();xhr.onerror=rej;xhr.send(fd);});
    row.querySelector('span:last-child').className=xhr.status===200?'ok':'err';
    row.querySelector('span:last-child').textContent=xhr.status===200?'✓':'✗';
    document.getElementById('pbar').style.width='0%';
    if(xhr.status===200) load();
  }

  load();
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
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    html,body{width:100%;height:100%;background:#000;overflow:hidden;}
    #standby{position:fixed;inset:0;background:#000;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Segoe UI',sans-serif;}
    #standby h1{font-size:5rem;font-weight:900;letter-spacing:.2em;background:linear-gradient(135deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
    #standby p{color:#444;font-size:1.2rem;margin-top:1rem;}
    #cdg-wrap{display:none;position:fixed;inset:0;background:#000;align-items:center;justify-content:center;}
    #cdg-wrap.visible{display:flex;}
    canvas{image-rendering:pixelated;image-rendering:crisp-edges;display:block;}
    #vid-wrap{display:none;position:fixed;inset:0;background:#000;}
    #vid-wrap.visible{display:block;}
    video{width:100%;height:100%;object-fit:contain;}
    audio{position:fixed;bottom:0;left:0;right:0;width:100%;display:none;}
    audio.visible{display:block;}
  </style>
</head>
<body>
<div id="standby"><h1>KARAOKE</h1><p>Warte auf Song&hellip;</p></div>
<div id="cdg-wrap"><canvas id="cdg" width="300" height="216"></canvas></div>
<div id="vid-wrap"><video id="vid" preload="auto"></video></div>
<audio id="aud" preload="auto" controls></audio>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
// ── CDG Player ─────────────────────────────────────────────────────────────
const CDG_CMD      = 0x09;
const MEMORY_PRESET      = 0x01;
const BORDER_PRESET      = 0x02;
const TILE_BLOCK         = 0x06;
const TILE_BLOCK_XOR     = 0x26;
const SCROLL_PRESET      = 0x14;
const SCROLL_COPY        = 0x15;
const DEFINE_TRANSPARENT = 0x1C;
const LOAD_CLR_LOW       = 0x1E;
const LOAD_CLR_HIGH      = 0x1F;

class CDGPlayer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx    = canvas.getContext('2d');
    this.reset();
  }

  reset() {
    this.packets   = [];
    this.pixels    = new Uint8Array(300 * 216);
    this.clrTable  = new Uint32Array(16);
    this.imgData   = this.ctx.createImageData(300, 216);
    this.pktIdx    = 0;
    this.ctx.fillStyle = '#000';
    this.ctx.fillRect(0, 0, 300, 216);
  }

  load(buffer) {
    this.reset();
    const u8 = new Uint8Array(buffer);
    for (let i = 0; i + 24 <= u8.length; i += 24) {
      this.packets.push({
        cmd:  u8[i]   & 0x3F,
        inst: u8[i+1] & 0x3F,
        data: u8.slice(i + 4, i + 20),
      });
    }
  }

  seek(timeSec) {
    const target = Math.floor(timeSec * 75); // 75 packets/sec
    if (target < this.pktIdx) {
      // Rewind: replay from start
      this.pixels.fill(0);
      this.clrTable.fill(0);
      this.pktIdx = 0;
    }
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
        for (let x = 0; x < 300; x++) {
          for (let y = 0; y < 12; y++)  this.pixels[y*300+x] = c;
          for (let y = 204; y < 216; y++) this.pixels[y*300+x] = c;
        }
        for (let y = 12; y < 204; y++) {
          for (let x = 0; x < 6; x++)   this.pixels[y*300+x] = c;
          for (let x = 294; x < 300; x++) this.pixels[y*300+x] = c;
        }
        break;
      }
      case TILE_BLOCK:
      case TILE_BLOCK_XOR: {
        const c0  = d[0] & 0x0F, c1 = d[1] & 0x0F;
        const row = (d[2] & 0x1F) * 12, col = (d[3] & 0x3F) * 6;
        const xorMode = p.inst === TILE_BLOCK_XOR;
        for (let r = 0; r < 12; r++) {
          const bits = d[4+r] & 0x3F;
          for (let c = 0; c < 6; c++) {
            const bit = (bits >> (5-c)) & 1;
            const idx = (row+r)*300 + (col+c);
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
          const r = ((hi >> 2) & 0x0F) * 17;
          const g = (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17;
          const b = (lo & 0x0F) * 17;
          this.clrTable[off+i] = (255<<24) | (b<<16) | (g<<8) | r;
        }
        break;
      }
      case SCROLL_PRESET:
      case SCROLL_COPY: {
        const color = d[0] & 0x0F;
        const hScroll = d[1] & 0x3F, hCmd = (hScroll >> 4) & 3, hOffset = hScroll & 7;
        const vScroll = d[2] & 0x3F, vCmd = (vScroll >> 4) & 3;
        const tmp = new Uint8Array(this.pixels);
        if (vCmd === 2) { // scroll up
          for (let y = 0; y < 204; y++) this.pixels.copyWithin(y*300, (y+12)*300, (y+12+1)*300);
          for (let y = 204; y < 216; y++) this.pixels.fill(p.inst===SCROLL_PRESET?color:tmp[y*300], y*300, (y+1)*300);
        } else if (vCmd === 1) { // scroll down
          for (let y = 215; y >= 12; y--) this.pixels.copyWithin(y*300, (y-12)*300, (y-11)*300);
          for (let y = 0; y < 12; y++) this.pixels.fill(p.inst===SCROLL_PRESET?color:tmp[(204+y)*300], y*300, (y+1)*300);
        }
        break;
      }
    }
  }

  paint() {
    const px = this.imgData.data, c = this.clrTable, buf = this.pixels;
    for (let i = 0; i < buf.length; i++) {
      const rgb = c[buf[i]];
      px[i*4]   = rgb & 0xFF;
      px[i*4+1] = (rgb>>8) & 0xFF;
      px[i*4+2] = (rgb>>16) & 0xFF;
      px[i*4+3] = 255;
    }
    this.ctx.putImageData(this.imgData, 0, 0);
  }
}

// ── Scale CDG canvas to fill window ───────────────────────────────────────
function scaleCanvas() {
  const el = document.getElementById('cdg');
  const sx = window.innerWidth  / 300;
  const sy = window.innerHeight / 216;
  const s  = Math.min(sx, sy);
  el.style.width  = (300 * s) + 'px';
  el.style.height = (216 * s) + 'px';
}
window.addEventListener('resize', scaleCanvas);
scaleCanvas();

// ── Player setup ───────────────────────────────────────────────────────────
const socket  = io({transports:['polling','websocket']});
const aud     = document.getElementById('aud');
const vid     = document.getElementById('vid');
const cdgWrap = document.getElementById('cdg-wrap');
const vidWrap = document.getElementById('vid-wrap');
const standby = document.getElementById('standby');
const cdg     = new CDGPlayer(document.getElementById('cdg'));
let   rafId   = null;

function showStandby(){
  standby.style.display='flex'; cdgWrap.classList.remove('visible');
  vidWrap.classList.remove('visible'); aud.classList.remove('visible');
}

function cdgLoop(){
  cdg.seek(aud.currentTime);
  rafId = requestAnimationFrame(cdgLoop);
}

socket.on('load_song', async song => {
  cancelAnimationFrame(rafId);
  aud.pause(); vid.pause();
  showStandby();

  if (song.type === 'cdg') {
    standby.style.display = 'none';
    cdgWrap.classList.add('visible');
    aud.classList.add('visible');

    const [, buf] = await Promise.all([
      fetch('/files/'+encodeURIComponent(song.mp3)).then(r=>r.blob()).then(b=>{aud.src=URL.createObjectURL(b);}),
      fetch('/files/'+encodeURIComponent(song.cdg)).then(r=>r.arrayBuffer()),
    ]);
    cdg.load(buf);
    rafId = requestAnimationFrame(cdgLoop);
    aud.play().catch(()=>{});
  } else if (song.type === 'video') {
    standby.style.display = 'none';
    vidWrap.classList.add('visible');
    vid.src = '/files/'+encodeURIComponent(song.video);
    vid.play().catch(()=>{});
  } else {
    standby.style.display = 'none';
    aud.classList.add('visible');
    aud.src = '/files/'+encodeURIComponent(song.audio);
    aud.play().catch(()=>{});
  }
});

socket.on('play',  () => { aud.play().catch(()=>{}); vid.play().catch(()=>{}); });
socket.on('pause', () => { aud.pause(); vid.pause(); });
socket.on('stop',  () => {
  cancelAnimationFrame(rafId); aud.pause(); vid.pause();
  aud.src=''; vid.src=''; showStandby();
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
    return jsonify({'ok': True, 'filename': fn})

@app.route('/files/<filename>')
def serve_file(filename):
    fn   = secure_filename(filename)
    path = os.path.join(LIBRARY_DIR, fn)
    if not os.path.exists(path):
        return '', 404
    return send_file(path, conditional=True)

# ── WebSocket ─────────────────────────────────────────────────────────────────

@socketio.on('load_song')
def on_load(data):   emit('load_song', data, broadcast=True)

@socketio.on('play')
def on_play():       emit('play',  broadcast=True)

@socketio.on('pause')
def on_pause():      emit('pause', broadcast=True)

@socketio.on('stop')
def on_stop():       emit('stop',  broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)