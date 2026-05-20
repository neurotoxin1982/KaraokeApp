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
                base TEXT PRIMARY KEY, display TEXT NOT NULL, type TEXT NOT NULL,
                mp3 TEXT, cdg TEXT, video TEXT, audio TEXT
            );
            CREATE TABLE IF NOT EXISTS queue (
                id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                song_json TEXT NOT NULL, singer TEXT NOT NULL, guest_id TEXT NOT NULL DEFAULT ""
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
        disp = base.replace('_', ' ').strip()
        if cdg and mp3:   rows.append((base, disp, 'cdg',   mp3, cdg,  None, None))
        elif mp4:         rows.append((base, disp, 'video', None, None, mp4, None))
        elif mp3:         rows.append((base, disp, 'audio', mp3, None, None, mp3))
    with db() as c:
        c.execute('DELETE FROM songs')
        c.executemany('INSERT INTO songs(base,display,type,mp3,cdg,video,audio) VALUES(?,?,?,?,?,?,?)', rows)

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
            if row['key'] == 'song_limit':   settings['song_limit']   = int(row['value'])
            elif row['key'] == 'queue_locked': settings['queue_locked'] = row['value'] == '1'
        queue = [
            {'id': r['id'], 'song': json.loads(r['song_json']), 'singer': r['singer'], 'guest_id': r['guest_id']}
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

# ── Guest UI ──────────────────────────────────────────────────────────────────

GUEST_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Karaoke</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#09090f;--sur:#12121c;--brd:#1c1c2c;--acc:#7c3aed;--txt:#e2e2f0;--mut:#44445a;--grn:#22c55e;--red:#ef4444}
html,body{height:100%;background:var(--bg);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden}
.app{display:flex;flex-direction:column;height:100dvh}
/* header */
.hdr{background:var(--sur);border-bottom:1px solid var(--brd);padding:.8rem 1rem .6rem;flex-shrink:0}
.logo{font-size:1.1rem;font-weight:800;background:linear-gradient(120deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.55rem}
.inp{width:100%;padding:.6rem .85rem;background:var(--bg);border:1.5px solid var(--brd);border-radius:.55rem;color:var(--txt);font-size:.95rem;outline:none;transition:border-color .15s}
.inp:focus{border-color:var(--acc)}
.inp::placeholder{color:var(--mut)}
.inp+.inp{margin-top:.45rem}
/* tabs */
.tabs{display:flex;border-bottom:1px solid var(--brd);flex-shrink:0}
.tab{flex:1;padding:.7rem .5rem;text-align:center;font-size:.82rem;font-weight:600;color:var(--mut);cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;user-select:none}
.tab.on{color:#a78bfa;border-color:#a78bfa}
.n{display:inline-block;background:var(--acc);color:#fff;font-size:.65rem;padding:.1rem .4rem;border-radius:999px;margin-left:.25rem;vertical-align:middle}
/* panes */
.pane{flex:1;overflow-y:auto;padding:.65rem}
.pane.hidden{display:none}
/* song list */
.srow{display:flex;align-items:center;gap:.55rem;padding:.72rem .9rem;background:var(--sur);border:1px solid var(--brd);border-radius:.6rem;margin-bottom:.32rem;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:border-color .15s}
.srow:active{border-color:var(--acc)}
.sicon{font-size:1.1rem;flex-shrink:0}
.sname{flex:1;font-size:.9rem;font-weight:500}
.stag{font-size:.63rem;color:var(--mut);background:var(--brd);padding:.12rem .38rem;border-radius:.3rem;flex-shrink:0}
.abtn{flex-shrink:0;padding:.38rem .8rem;background:var(--acc);color:#fff;border:none;border-radius:.4rem;font-size:.78rem;font-weight:700;cursor:pointer}
/* queue */
.now-card{background:#0a1c0e;border:1.5px solid var(--grn);border-radius:.65rem;padding:.85rem 1rem;margin-bottom:.5rem}
.now-lbl{font-size:.66rem;font-weight:700;color:var(--grn);text-transform:uppercase;letter-spacing:.07em}
.now-song{font-size:.95rem;font-weight:700;color:var(--grn);margin-top:.2rem}
.now-who{font-size:.78rem;color:#86efac;margin-top:.15rem}
.qrow{display:flex;align-items:center;gap:.55rem;padding:.65rem .85rem;background:var(--sur);border:1px solid var(--brd);border-radius:.6rem;margin-bottom:.32rem}
.qpos{font-size:1.05rem;font-weight:800;color:var(--acc);min-width:1.5rem;text-align:center;flex-shrink:0}
.qinfo{flex:1;min-width:0}
.qwho{font-size:.72rem;font-weight:600;color:#a78bfa}
.qsong{font-size:.86rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.locked{display:flex;align-items:center;gap:.5rem;background:#190a0a;border:1px solid #5a1a1a;border-radius:.6rem;padding:.8rem 1rem;color:var(--red);font-size:.86rem;margin-bottom:.5rem}
.empty{text-align:center;color:var(--mut);padding:3rem 1rem;font-size:.9rem}
/* toast */
.toast{position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%) translateY(.4rem);background:var(--sur);border:1px solid var(--brd);color:var(--txt);padding:.58rem 1.15rem;border-radius:.5rem;font-size:.84rem;z-index:99;opacity:0;transition:opacity .2s,transform .2s;pointer-events:none;white-space:nowrap;max-width:90vw;text-align:center}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.ok{border-color:var(--grn);color:var(--grn)}
.toast.err{border-color:var(--red);color:var(--red)}
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div class="logo">&#127908; Karaoke</div>
    <input id="uname" class="inp" type="text" placeholder="Dein Name&#8230;" maxlength="30" oninput="localStorage.setItem('kname',this.value)">
    <input id="search" class="inp" type="search" placeholder="Song suchen&#8230;" oninput="filter()" style="display:none;margin-top:.45rem">
  </div>
  <div class="tabs">
    <div id="t-s" class="tab on" onclick="tab('s')">&#127925; Songs</div>
    <div id="t-q" class="tab"    onclick="tab('q')">&#9654; Warteschlange</div>
  </div>
  <div id="p-s" class="pane"><div class="empty">Lädt&#8230;</div></div>
  <div id="p-q" class="pane hidden"></div>
</div>
<div id="toast" class="toast"></div>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
const socket = io();
let gid = localStorage.getItem('kgid') || (()=>{const v=Date.now().toString(36)+Math.random().toString(36).slice(2);localStorage.setItem('kgid',v);return v;})();
let all=[], st={now_playing:null,queue:[],settings:{song_limit:2,queue_locked:false}};

fetch('/library').then(r=>r.json()).then(s=>{all=s;renderSongs(s);document.getElementById('search').style.display='';});

function filter(){const q=document.getElementById('search').value.toLowerCase();renderSongs(q?all.filter(s=>s.display.toLowerCase().includes(q)):all);}

function renderSongs(songs){
  const el=document.getElementById('p-s');
  if(!songs.length){el.innerHTML='<div class="empty">Keine Songs gefunden.</div>';return;}
  const ic={cdg:'&#127908;',video:'&#127916;',audio:'&#127925;'};
  el.innerHTML=songs.map(s=>{const d=JSON.stringify(s).replace(/'/g,"&#39;");return`<div class="srow" onclick='tap(${d})'><span class="sicon">${ic[s.type]}</span><span class="sname">${esc(s.display)}</span><span class="stag">${s.type.toUpperCase()}</span><button class="abtn" onclick="event.stopPropagation();tap(${d})">+</button></div>`;}).join('');
}

function tap(song){
  const name=document.getElementById('uname').value.trim();
  if(!name){toast('Bitte erst deinen Namen eingeben.','err');return;}
  if(st.settings.queue_locked){toast('Warteschlange ist gerade geschlossen.','err');return;}
  if(st.queue.filter(i=>i.guest_id===gid).length>=st.settings.song_limit){toast('Limit erreicht – max. '+st.settings.song_limit+' Songs.','err');return;}
  socket.emit('queue_add',{song,singer:name,guest_id:gid});
}

function renderQueue(){
  const el=document.getElementById('p-q');
  const{now_playing:np,queue,settings}=st;
  let h='';
  if(settings.queue_locked)h+='<div class="locked">&#128274; Warteschlange vorübergehend geschlossen.</div>';
  if(np)h+=`<div class="now-card"><div class="now-lbl">Spielt gerade</div><div class="now-song">&#9654; ${esc(np.song.display)}</div><div class="now-who">&#127908; ${esc(np.singer)}</div></div>`;
  if(!queue.length){h+='<div class="empty">'+(np?'Keine weiteren Songs.':'Queue ist leer.')+'</div>';el.innerHTML=h;return;}
  h+=queue.map((it,i)=>`<div class="qrow"><div class="qpos">${i+1}</div><div class="qinfo"><div class="qwho">&#127908; ${esc(it.singer)}</div><div class="qsong">${esc(it.song.display)}</div></div></div>`).join('');
  el.innerHTML=h;
}

function updateBadge(){
  const n=st.queue.length+(st.now_playing?1:0);
  document.getElementById('t-q').innerHTML='&#9654; Warteschlange'+(n?`<span class="n">${n}</span>`:'');
}

function tab(t){
  ['s','q'].forEach(x=>{document.getElementById('t-'+x).classList.toggle('on',x===t);document.getElementById('p-'+x).classList.toggle('hidden',x!==t);});
}

socket.on('connect',()=>socket.emit('get_state'));
socket.on('state',s=>{st=s;renderQueue();updateBadge();});
socket.on('queue_update',s=>{st=s;renderQueue();updateBadge();});
socket.on('add_result',r=>{if(r.ok){toast('Song hinzugefügt!','ok');tab('q');}else toast(r.error,'err');});

let toastT;
function toast(msg,type=''){const el=document.getElementById('toast');el.textContent=msg;el.className='toast show '+(type||'');clearTimeout(toastT);toastT=setTimeout(()=>el.className='toast',2800);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

document.getElementById('uname').value=localStorage.getItem('kname')||'';
</script>
</body>
</html>"""

# ── Admin UI ──────────────────────────────────────────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin – Karaoke</title>
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#09090f;--sur:#12121c;--brd:#1c1c2c;--acc:#7c3aed;--txt:#e2e2f0;--mut:#44445a;--grn:#22c55e;--amb:#f59e0b;--blu:#3b82f6;--red:#ef4444}
html,body{height:100%;background:var(--bg);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden}
.root{display:flex;flex-direction:column;height:100vh}
/* topbar */
.bar{display:flex;align-items:center;gap:.7rem;padding:.58rem 1.2rem;background:var(--sur);border-bottom:1px solid var(--brd);flex-shrink:0}
.bar-logo{font-size:1.05rem;font-weight:800;background:linear-gradient(120deg,#f472b6,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.chip{font-size:.62rem;font-weight:700;padding:.18rem .5rem;border-radius:999px;background:var(--acc);color:#fff;letter-spacing:.05em}
.bar a{margin-left:auto;font-size:.76rem;color:var(--mut);text-decoration:none;padding:.28rem .6rem;border:1px solid var(--brd);border-radius:.38rem;transition:color .15s,border-color .15s}
.bar a:hover{color:#a78bfa;border-color:var(--acc)}
/* body */
.body{display:flex;flex:1;overflow:hidden}
/* queue col */
.col-q{display:flex;flex-direction:column;flex:1;overflow:hidden;border-right:1px solid var(--brd)}
/* now playing */
.now{padding:.7rem .95rem;border-bottom:1px solid var(--brd);flex-shrink:0}
.now-nil{color:var(--mut);font-size:.84rem;padding:.25rem 0}
.now-card{background:#0a1c0e;border:1.5px solid var(--grn);border-radius:.65rem;padding:.8rem .95rem}
.now-song{font-size:.98rem;font-weight:700;color:var(--grn);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now-who{font-size:.76rem;color:#86efac;margin-top:.18rem}
.ctrl{display:flex;align-items:center;gap:.38rem;margin-top:.65rem;flex-wrap:wrap}
.btn{padding:.36rem .82rem;border:none;border-radius:.4rem;font-size:.78rem;font-weight:700;cursor:pointer;transition:opacity .15s,transform .1s}
.btn:active{transform:scale(.95)}
.btn:hover{opacity:.83}
.b-play{background:var(--grn);color:#000}
.b-pause{background:var(--blu);color:#fff}
.b-skip{background:var(--amb);color:#000}
.b-key{background:#22223a;color:var(--txt);padding:.36rem .52rem;min-width:1.9rem}
.keyval{font-size:.76rem;color:var(--mut);min-width:2rem;text-align:center}
/* queue list */
.q-hdr{display:flex;align-items:center;gap:.5rem;padding:.55rem .95rem;border-bottom:1px solid var(--brd);flex-shrink:0}
.q-hdr h2{font-size:.7rem;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.07em}
.q-cnt{font-size:.7rem;font-weight:700;color:var(--acc)}
.q-scroll{flex:1;overflow-y:auto;padding:.45rem .7rem}
.qitem{display:flex;align-items:center;gap:.5rem;padding:.58rem .72rem;background:var(--sur);border:1px solid var(--brd);border-radius:.58rem;margin-bottom:.28rem;transition:border-color .15s}
.qitem:hover{border-color:#2e2e4e}
.sortable-ghost{opacity:.25;background:#22223a}
.drag{color:var(--mut);font-size:.95rem;cursor:grab;flex-shrink:0;user-select:none;padding:.1rem .12rem}
.drag:active{cursor:grabbing}
.qinfo{flex:1;min-width:0}
.qwho{font-size:.71rem;font-weight:600;color:#a78bfa;cursor:pointer;border-bottom:1px dashed transparent;transition:border-color .15s;display:inline-block}
.qwho:hover{border-color:var(--acc)}
.qwho-inp{font-size:.71rem;font-weight:600;color:#a78bfa;background:transparent;border:none;border-bottom:1.5px solid var(--acc);outline:none;width:100%}
.qsong{font-size:.83rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:.08rem}
.qact{display:flex;gap:.28rem;flex-shrink:0}
.ibtn{background:none;border:none;cursor:pointer;padding:.28rem .32rem;border-radius:.32rem;font-size:.88rem;line-height:1;color:var(--mut);transition:background .15s,color .15s}
.ibtn:hover{background:var(--brd)}
.i-top:hover{color:var(--blu)}
.i-del:hover{color:var(--red)}
.q-empty{text-align:center;color:var(--mut);padding:3rem;font-size:.86rem}
/* sidebar */
.col-side{width:272px;flex-shrink:0;overflow-y:auto;padding:.8rem;display:flex;flex-direction:column;gap:.8rem}
.card{background:var(--sur);border:1px solid var(--brd);border-radius:.68rem;padding:.85rem}
.card h3{font-size:.68rem;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.07em;margin-bottom:.75rem}
/* settings */
.set-row{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:.7rem}
.set-row:last-child{margin-bottom:0}
.set-lbl{font-size:.8rem}
.set-lbl small{display:block;font-size:.69rem;color:var(--mut);margin-top:.1rem}
.num-inp{width:54px;padding:.3rem .42rem;background:var(--bg);border:1.5px solid var(--brd);border-radius:.38rem;color:var(--txt);font-size:.84rem;text-align:center;outline:none}
.num-inp:focus{border-color:var(--acc)}
.toggle{position:relative;display:inline-block;width:38px;height:21px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:var(--brd);border-radius:10.5px;cursor:pointer;transition:background .2s}
.slider::before{content:'';position:absolute;width:15px;height:15px;left:3px;top:3px;background:#fff;border-radius:50%;transition:transform .2s}
input:checked+.slider{background:var(--acc)}
input:checked+.slider::before{transform:translateX(17px)}
/* upload */
.drop{border:2px dashed var(--brd);border-radius:.58rem;padding:.9rem;text-align:center;cursor:pointer;font-size:1.4rem;transition:border-color .2s,background .2s}
.drop:hover,.drop.over{border-color:var(--acc);background:#191928}
.drop p{font-size:.71rem;color:var(--mut);margin-top:.28rem}
.prog{height:3px;background:var(--brd);border-radius:2px;overflow:hidden;margin:.38rem 0}
.progbar{height:100%;background:linear-gradient(90deg,#a78bfa,#60a5fa);width:0;transition:width .2s}
.ulog{font-size:.7rem;color:var(--mut);max-height:76px;overflow-y:auto}
.ulog-row{padding:.13rem 0;border-bottom:1px solid var(--bg);display:flex;justify-content:space-between;gap:.4rem}
/* danger */
.danger-btn{width:100%;padding:.5rem;background:transparent;border:1px solid #5a1a1a;border-radius:.45rem;color:#f87171;font-size:.78rem;font-weight:600;cursor:pointer;transition:background .15s}
.danger-btn:hover{background:#1a0808}
</style>
</head>
<body>
<div class="root">
  <div class="bar">
    <span class="bar-logo">&#127908; Karaoke</span>
    <span class="chip">ADMIN</span>
    <a href="/" target="_blank">&#128100; Gäste-Ansicht</a>
  </div>
  <div class="body">
    <!-- Queue column -->
    <div class="col-q">
      <div class="now" id="now"><div class="now-nil">&#9899; Nichts spielt gerade.</div></div>
      <div class="q-hdr">
        <h2>Warteschlange</h2>
        <span class="q-cnt" id="q-cnt"></span>
      </div>
      <div class="q-scroll" id="q-scroll"><div class="q-empty">Warteschlange ist leer.</div></div>
    </div>
    <!-- Sidebar -->
    <div class="col-side">
      <div class="card">
        <h3>Einstellungen</h3>
        <div class="set-row">
          <div class="set-lbl">Songs-Limit pro Gast<small>Max. gleichzeitig in Queue</small></div>
          <input type="number" class="num-inp" id="s-limit" min="1" max="20" value="2" onchange="saveSetting()">
        </div>
        <div class="set-row">
          <div class="set-lbl">Queue sperren<small>Gäste können nicht hinzufügen</small></div>
          <label class="toggle"><input type="checkbox" id="s-locked" onchange="saveSetting()"><span class="slider"></span></label>
        </div>
      </div>

      <div class="card">
        <h3>Songs hochladen</h3>
        <div class="drop" id="dz"
             onclick="document.getElementById('fi').click()"
             ondragover="event.preventDefault();this.classList.add('over')"
             ondragleave="this.classList.remove('over')"
             ondrop="onDrop(event)">
          &#128190;
          <p>MP3 + CDG &nbsp;/&nbsp; MP4<br>Klicken oder Drag &amp; Drop</p>
          <input type="file" id="fi" multiple accept=".mp3,.cdg,.mp4,.webm,.m4v,.wav" style="display:none" onchange="uploadAll(this.files)">
        </div>
        <div class="prog"><div class="progbar" id="pbar"></div></div>
        <div class="ulog" id="ulog"></div>
      </div>

      <div class="card">
        <h3>Library</h3>
        <button class="danger-btn" onclick="clearLibrary()">&#128465; Alle Songs löschen</button>
      </div>
    </div>
  </div>
</div>
<script>
const socket=io();
let state={now_playing:null,queue:[],settings:{song_limit:2,queue_locked:false}};
let keyOffset=0, sortable=null;

socket.on('connect',()=>socket.emit('get_state'));
socket.on('state',s=>{state=s;render();});
socket.on('queue_update',s=>{state=s;render();});

function render(){renderNow();renderQueue();renderSettings();}

function renderNow(){
  const np=state.now_playing, el=document.getElementById('now');
  if(!np){el.innerHTML='<div class="now-nil">&#9899; Nichts spielt gerade.</div>';return;}
  el.innerHTML=`<div class="now-card">
    <div class="now-song">&#9654; ${esc(np.song.display)}</div>
    <div class="now-who">&#127908; ${esc(np.singer)}</div>
    <div class="ctrl">
      <button class="btn b-play"  onclick="socket.emit('play')">&#9654; Play</button>
      <button class="btn b-pause" onclick="socket.emit('pause')">&#9646;&#9646; Pause</button>
      <button class="btn b-skip"  onclick="socket.emit('skip')">&#9197; Skip</button>
      <button class="btn b-key"   onclick="changeKey(-1)">&#9660;</button>
      <span class="keyval" id="kv">${fmt(keyOffset)}</span>
      <button class="btn b-key"   onclick="changeKey(1)">&#9650;</button>
    </div>
  </div>`;
}

function renderQueue(){
  const el=document.getElementById('q-scroll'), qs=state.queue;
  document.getElementById('q-cnt').textContent=qs.length?'('+qs.length+')':'';
  if(!qs.length){el.innerHTML='<div class="q-empty">Warteschlange ist leer.</div>';if(sortable){sortable.destroy();sortable=null;}return;}
  el.innerHTML=qs.map(it=>`<div class="qitem" data-id="${it.id}">
    <span class="drag">&#8801;</span>
    <div class="qinfo">
      <span class="qwho" onclick="editSinger(this,'${it.id}')">${esc(it.singer)}</span>
      <div class="qsong">${esc(it.song.display)}</div>
    </div>
    <div class="qact">
      <button class="ibtn i-top" title="An erste Stelle" onclick="moveTop('${it.id}')">&#11014;</button>
      <button class="ibtn i-del" title="Entfernen"       onclick="del('${it.id}')">&#10005;</button>
    </div>
  </div>`).join('');
  if(sortable)sortable.destroy();
  sortable=Sortable.create(el,{animation:150,handle:'.drag',ghostClass:'sortable-ghost',onEnd(){
    const order=[...el.querySelectorAll('[data-id]')].map(n=>n.dataset.id);
    socket.emit('queue_reorder',{order});
  }});
}

function renderSettings(){
  document.getElementById('s-limit').value=state.settings.song_limit;
  document.getElementById('s-locked').checked=state.settings.queue_locked;
}

function saveSetting(){socket.emit('settings_update',{song_limit:parseInt(document.getElementById('s-limit').value)||2,queue_locked:document.getElementById('s-locked').checked});}
function changeKey(d){keyOffset+=d;const el=document.getElementById('kv');if(el)el.textContent=fmt(keyOffset);socket.emit('key_change',{offset:keyOffset});}
function moveTop(id){socket.emit('queue_move_top',{id});}
function del(id){socket.emit('queue_remove',{id});}

function editSinger(el,id){
  const old=el.textContent;
  const inp=document.createElement('input');inp.className='qwho-inp';inp.value=old;
  el.replaceWith(inp);inp.focus();inp.select();
  const done=()=>socket.emit('queue_edit_singer',{id,singer:inp.value.trim()||old});
  inp.addEventListener('blur',done);
  inp.addEventListener('keydown',e=>{if(e.key==='Enter')inp.blur();if(e.key==='Escape'){inp.value=old;inp.blur();}});
}

async function clearLibrary(){
  if(!confirm('Alle Songs unwiderruflich löschen?'))return;
  const r=await fetch('/admin/clear-library',{method:'POST'});
  if((await r.json()).ok)alert('Library gelöscht. Seite wird neu geladen.');
  location.reload();
}

function onDrop(e){e.preventDefault();document.getElementById('dz').classList.remove('over');uploadAll(e.dataTransfer.files);}
function uploadAll(files){[...files].forEach(uploadOne);}
async function uploadOne(file){
  const log=document.getElementById('ulog');
  const row=document.createElement('div');row.className='ulog-row';
  row.innerHTML=`<span>${file.name.slice(0,28)}</span><span>&#8230;</span>`;log.prepend(row);
  const fd=new FormData();fd.append('file',file);
  const xhr=new XMLHttpRequest();xhr.open('POST','/upload');
  xhr.upload.onprogress=e=>{if(e.lengthComputable)document.getElementById('pbar').style.width=(e.loaded/e.total*100)+'%';};
  await new Promise((ok,fail)=>{xhr.onload=()=>xhr.status===200?ok():fail();xhr.onerror=fail;xhr.send(fd);});
  const ok=xhr.status===200;
  row.querySelector('span:last-child').textContent=ok?'✓':'✗';
  row.querySelector('span:last-child').style.color=ok?'#22c55e':'#ef4444';
  document.getElementById('pbar').style.width='0%';
}

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function fmt(n){return(n>=0?'+':'')+n;}
</script>
</body>
</html>"""

# ── Player UI ─────────────────────────────────────────────────────────────────

PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Karaoke Player</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#000;overflow:hidden}

/* Click-to-unlock overlay – shown until first tap */
#unlock{
  position:fixed;inset:0;z-index:50;
  background:#000;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  cursor:pointer;
  font-family:'Segoe UI',sans-serif;
}
#unlock-icon{font-size:5rem}
#unlock-title{
  font-size:2rem;font-weight:900;letter-spacing:.15em;margin-top:1.5rem;
  background:linear-gradient(135deg,#a78bfa,#60a5fa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
#unlock-hint{font-size:.95rem;color:#444;margin-top:.75rem}

/* Standby (shown between songs) */
#standby{
  position:fixed;inset:0;
  display:none;flex-direction:column;align-items:center;justify-content:center;
  background:#000;font-family:'Segoe UI',sans-serif;
}
#standby h1{
  font-size:6rem;font-weight:900;letter-spacing:.15em;
  background:linear-gradient(135deg,#a78bfa,#60a5fa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
#sb-now{font-size:1.05rem;color:#22c55e;margin-top:1.5rem;max-width:80vw;text-align:center}
#sb-next{font-size:.82rem;color:#2a2a2a;margin-top:.5rem;max-width:80vw;text-align:center}

#cdg-wrap{display:none;position:fixed;inset:0;background:#000;align-items:center;justify-content:center}
#cdg-wrap.on{display:flex}
canvas{image-rendering:pixelated;image-rendering:crisp-edges;display:block}

#vid-wrap{display:none;position:fixed;inset:0;background:#000}
#vid-wrap.on{display:block}
video{width:100%;height:100%;object-fit:contain}

audio{position:fixed;bottom:0;left:0;right:0;width:100%;display:none}
audio.on{display:block}

.hud{position:fixed;bottom:.6rem;font-family:monospace;font-size:.7rem;color:rgba(255,255,255,.18);pointer-events:none}
#hud-l{left:.8rem}
#hud-r{right:.8rem}
</style>
</head>
<body>

<!-- Step 1: admin taps here once to unlock audio -->
<div id="unlock">
  <div id="unlock-icon">&#127908;</div>
  <div id="unlock-title">KARAOKE</div>
  <div id="unlock-hint">Einmal tippen zum Starten</div>
</div>

<!-- Step 2: standby between songs -->
<div id="standby">
  <h1>KARAOKE</h1>
  <div id="sb-now"></div>
  <div id="sb-next"></div>
</div>

<div id="cdg-wrap"><canvas id="cdg" width="300" height="216"></canvas></div>
<div id="vid-wrap"><video id="vid" preload="auto"></video></div>
<audio id="aud" preload="auto" controls></audio>

<div class="hud" id="hud-l"></div>
<div class="hud" id="hud-r"></div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
// ── CDG Decoder ───────────────────────────────────────────────────────────────
class CDGPlayer{
  constructor(cv){this.cv=cv;this.ctx=cv.getContext('2d');this.reset();}
  reset(){this.pkts=[];this.px=new Uint8Array(300*216);this.clr=new Uint32Array(16);this.img=this.ctx.createImageData(300,216);this.idx=0;this.ctx.fillStyle='#000';this.ctx.fillRect(0,0,300,216);}
  load(buf){
    this.reset();const u8=new Uint8Array(buf);
    for(let i=0;i+24<=u8.length;i+=24)this.pkts.push({cmd:u8[i]&63,inst:u8[i+1]&63,d:u8.slice(i+4,i+20)});
  }
  seek(sec){
    const t=Math.floor(sec*75);
    if(t<this.idx){this.px.fill(0);this.clr.fill(0);this.idx=0;}
    let dirty=false;
    while(this.idx<t&&this.idx<this.pkts.length){const p=this.pkts[this.idx++];if(p.cmd===9){this.exec(p);dirty=true;}}
    if(dirty)this.paint();
  }
  exec({inst,d}){
    switch(inst){
      case 1:if((d[1]&15)===0)this.px.fill(d[0]&15);break;
      case 2:{const c=d[0]&15;for(let x=0;x<300;x++){for(let y=0;y<12;y++)this.px[y*300+x]=c;for(let y=204;y<216;y++)this.px[y*300+x]=c;}for(let y=12;y<204;y++){for(let x=0;x<6;x++)this.px[y*300+x]=c;for(let x=294;x<300;x++)this.px[y*300+x]=c;}break;}
      case 6:case 38:{const c0=d[0]&15,c1=d[1]&15,row=(d[2]&31)*12,col=(d[3]&63)*6,xor=inst===38;for(let r=0;r<12;r++){const bits=d[4+r]&63;for(let c=0;c<6;c++){const b=(bits>>(5-c))&1,i=(row+r)*300+(col+c);this.px[i]=xor?this.px[i]^(b?c1:c0):(b?c1:c0);}}break;}
      case 30:case 31:{const off=inst===31?8:0;for(let i=0;i<8;i++){const hi=d[i*2]&63,lo=d[i*2+1]&63;this.clr[off+i]=(255<<24)|(((lo&15)*17)<<16)|((((hi&3)<<2)|((lo>>4)&3))*17<<8)|((hi>>2&15)*17);}break;}
      case 20:case 21:{const vc=(d[2]>>4)&3,fill=inst===20,color=d[0]&15,tmp=new Uint8Array(this.px);if(vc===2){for(let y=0;y<204;y++)this.px.copyWithin(y*300,(y+12)*300,(y+13)*300);for(let y=204;y<216;y++)this.px.fill(fill?color:tmp[y*300],y*300,(y+1)*300);}else if(vc===1){for(let y=215;y>=12;y--)this.px.copyWithin(y*300,(y-12)*300,(y-11)*300);for(let y=0;y<12;y++)this.px.fill(fill?color:tmp[(204+y)*300],y*300,(y+1)*300);}break;}
    }
  }
  paint(){
    const px=this.img.data,clr=this.clr,buf=this.px;
    for(let i=0;i<buf.length;i++){const rgb=clr[buf[i]];px[i*4]=rgb&255;px[i*4+1]=(rgb>>8)&255;px[i*4+2]=(rgb>>16)&255;px[i*4+3]=255;}
    this.ctx.putImageData(this.img,0,0);
  }
}

// ── Setup ─────────────────────────────────────────────────────────────────────
const cv=document.getElementById('cdg');
function scaleCanvas(){const s=Math.min(innerWidth/300,innerHeight/216);cv.style.width=(300*s)+'px';cv.style.height=(216*s)+'px';}
addEventListener('resize',scaleCanvas);scaleCanvas();

const socket=io({transports:['polling','websocket']});
const aud=document.getElementById('aud');
const vid=document.getElementById('vid');
const cdgW=document.getElementById('cdg-wrap');
const vidW=document.getElementById('vid-wrap');
const standby=document.getElementById('standby');
const unlock=document.getElementById('unlock');
const cdg=new CDGPlayer(cv);
let raf=null, sync=0, unlocked=false, pending=null;

// ── Unlock overlay ────────────────────────────────────────────────────────────
// Browser blocks autoplay until user gesture. Admin taps once to unlock.
unlock.addEventListener('click',()=>{
  unlocked=true;
  unlock.style.display='none';
  standby.style.display='flex';
  // If a song arrived before unlock, play it now
  if(pending){doLoad(pending);pending=null;}
});

// ── Standby helpers ───────────────────────────────────────────────────────────
function showStandby(){
  standby.style.display='flex';
  cdgW.classList.remove('on');
  vidW.classList.remove('on');
  aud.classList.remove('on');
}

function setSync(v){
  sync=v;
  document.getElementById('hud-l').textContent=v!==0?'sync '+(v>0?'+':'')+v.toFixed(1)+'s':'';
}

// ── CDG loop ──────────────────────────────────────────────────────────────────
function cdgLoop(){cdg.seek(Math.max(0,aud.currentTime+sync));raf=requestAnimationFrame(cdgLoop);}

// ── Keyboard sync adjust ──────────────────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.key==='ArrowRight')setSync(parseFloat((sync+0.5).toFixed(1)));
  if(e.key==='ArrowLeft') setSync(parseFloat((sync-0.5).toFixed(1)));
});

// Auto-advance when song ends
aud.addEventListener('ended',()=>socket.emit('song_ended'));
vid.addEventListener('ended',()=>socket.emit('song_ended'));

// ── Socket ────────────────────────────────────────────────────────────────────
socket.on('connect',()=>socket.emit('player_connect'));

socket.on('load_song',song=>{
  if(!unlocked){pending=song;return;}  // wait for user gesture
  doLoad(song);
});

async function doLoad(song){
  cancelAnimationFrame(raf);
  aud.pause();vid.pause();
  showStandby();
  setSync(0);

  if(song.type==='cdg'){
    standby.style.display='none';
    cdgW.classList.add('on');
    aud.classList.add('on');
    aud.src='/files/'+encodeURIComponent(song.mp3);
    const buf=await fetch('/files/'+encodeURIComponent(song.cdg)).then(r=>r.arrayBuffer());
    cdg.load(buf);
    raf=requestAnimationFrame(cdgLoop);
    aud.play().catch(()=>{});

  }else if(song.type==='video'){
    standby.style.display='none';
    vidW.classList.add('on');
    vid.src='/files/'+encodeURIComponent(song.video);
    vid.play().catch(()=>{});

  }else{
    standby.style.display='none';
    aud.classList.add('on');
    aud.src='/files/'+encodeURIComponent(song.audio);
    aud.play().catch(()=>{});
  }
}

socket.on('play', ()=>{aud.play().catch(()=>{});vid.play().catch(()=>{});});
socket.on('pause',()=>{aud.pause();vid.pause();});
socket.on('stop', ()=>{
  cancelAnimationFrame(raf);aud.pause();vid.pause();aud.src='';vid.src='';
  showStandby();
  document.getElementById('sb-now').textContent='';
  document.getElementById('sb-next').textContent='';
});
socket.on('key_change',({offset})=>{
  document.getElementById('hud-r').textContent=offset!==0?'key '+(offset>0?'+':'')+offset:'';
});
socket.on('queue_update',({now_playing,queue})=>{
  // Only update standby text if standby is visible
  if(standby.style.display==='none')return;
  document.getElementById('sb-now').textContent=now_playing?'▶ '+now_playing.singer+' – '+now_playing.song.display:'';
  const nx=queue[0];
  document.getElementById('sb-next').textContent=nx?'Als nächstes: '+nx.singer+' – '+nx.song.display:'';
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

@app.route('/admin/clear-library', methods=['POST'])
def clear_library():
    for path in glob.glob(os.path.join(LIBRARY_DIR, '*')):
        try:
            os.remove(path)
        except OSError:
            pass
    with db() as c:
        c.execute('DELETE FROM songs')
    return jsonify({'ok': True})

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
    emit('add_result', {'ok': True})
    if now_playing is None:
        advance_queue()
    else:
        save_queue()
        socketio.emit('queue_update', get_state())

@socketio.on('queue_remove')
def on_queue_remove(data):
    queue[:] = [it for it in queue if it['id'] != data.get('id')]
    save_queue()
    socketio.emit('queue_update', get_state())

@socketio.on('queue_reorder')
def on_queue_reorder(data):
    by_id = {it['id']: it for it in queue}
    queue[:] = [by_id[oid] for oid in data.get('order', []) if oid in by_id]
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
    tid, name = data.get('id'), str(data.get('singer', ''))[:30]
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
