const http = require('http');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

const PORT = 8765;
let httpServer = null;
let wss        = null;
const clients  = new Set();

// Cached state — sent to every new client that connects mid-session
const state = {
  song_info:        null,
  media:            null,
  position:         null,
  transition:       null,
  transitionPhaseEnd: 0,
  venue_path:       null,
  idle_config:      null,
  adActive:         false,
  ad_config:        null,
};

const MIMES = {
  '.mp3': 'audio/mpeg',  '.m4a': 'audio/mp4',  '.ogg': 'audio/ogg', '.aac': 'audio/aac',
  '.cdg': 'application/octet-stream',
  '.mp4': 'video/mp4',   '.webm': 'video/webm', '.avi': 'video/x-msvideo',
  '.mkv': 'video/x-matroska', '.mpeg': 'video/mpeg',
  '.jpg': 'image/jpeg',  '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif',   '.webp': 'image/webp',
};

// Returns all non-internal IPv4s grouped by interface name
function getAllIPs() {
  const results = [];
  for (const [name, ifaces] of Object.entries(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) {
        results.push({ name, address: iface.address });
      }
    }
  }
  return results;
}

// UDP routing trick: finds the IP actually used for outbound traffic
function getRoutedIP() {
  return new Promise(resolve => {
    const dgram = require('dgram');
    const socket = dgram.createSocket('udp4');
    socket.connect(80, '8.8.8.8', () => {
      resolve(socket.address().address);
      socket.close();
    });
    socket.on('error', () => resolve(null));
  });
}

let _activeIP = null;

async function getLocalIP() {
  if (_activeIP) return _activeIP;
  const routed = await getRoutedIP();
  if (routed && routed !== '0.0.0.0') { _activeIP = routed; return routed; }
  // Fallback: prefer 192.168.x.x, then any non-VPN range
  const all = getAllIPs();
  const pref = all.find(i => i.address.startsWith('192.168.'))
            || all.find(i => i.address.startsWith('10.') && !i.name.toLowerCase().includes('vpn'))
            || all[0];
  _activeIP = pref ? pref.address : '127.0.0.1';
  return _activeIP;
}

async function start() {
  if (httpServer) return getUrl();
  _activeIP = null; // re-detect on each start
  const ip = await getLocalIP();
  const { WebSocketServer } = require('ws');
  httpServer = http.createServer(handleRequest);
  wss = new WebSocketServer({ server: httpServer });
  wss.on('connection', ws => {
    clients.add(ws);
    ws.on('close', () => clients.delete(ws));
    ws.on('error', () => clients.delete(ws));
    // Echo pings straight back so the client can measure its own network
    // latency to us and compensate the position sync for it.
    ws.on('message', data => {
      try {
        const msg = JSON.parse(data);
        if (msg.type === 'ping') _send(ws, { type: 'pong', t: msg.t });
      } catch {}
    });
    // Push current playback state to the new client
    _sendState(ws);
  });
  await new Promise(resolve => httpServer.listen(PORT, resolve));
  return `http://${ip}:${PORT}`;
}

function handleRequest(req, res) {
  const url = new URL(req.url, 'http://localhost');

  if (url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(fs.readFileSync(path.join(__dirname, '..', 'renderer', 'web-player.html')));
    return;
  }

  if (url.pathname === '/js/cdg-player.js') {
    res.writeHead(200, { 'Content-Type': 'application/javascript' });
    res.end(fs.readFileSync(path.join(__dirname, '..', 'renderer', 'js', 'cdg-player.js')));
    return;
  }

  if (url.pathname === '/media') {
    const filePath = url.searchParams.get('p');
    if (!filePath) { res.writeHead(400); res.end(); return; }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end(); return; }
      const ct = MIMES[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
      const range = req.headers['range'];
      if (range) {
        const m = range.match(/bytes=(\d+)-(\d*)/);
        if (m) {
          const start = +m[1], end = m[2] ? +m[2] : data.length - 1;
          res.writeHead(206, {
            'Content-Type': ct, 'Accept-Ranges': 'bytes',
            'Content-Range': `bytes ${start}-${end}/${data.length}`,
            'Content-Length': String(end - start + 1),
          });
          return res.end(data.slice(start, end + 1));
        }
      }
      res.writeHead(200, { 'Content-Type': ct, 'Accept-Ranges': 'bytes', 'Content-Length': String(data.length) });
      res.end(data);
    });
    return;
  }

  res.writeHead(404); res.end();
}

function stop() {
  clients.forEach(ws => { try { ws.close(); } catch {} });
  clients.clear();
  if (httpServer) { httpServer.close(); httpServer = null; }
  wss = null;
}

function _send(ws, msg) {
  if (ws.readyState === 1) try { ws.send(JSON.stringify(toWebMsg(msg))); } catch {}
}

function _sendState(ws) {
  _send(ws, { type: 'setting', key: 'audio', value: _audioEnabled });
  if (state.venue_path)  _send(ws, state.venue_path);
  if (state.song_info)   _send(ws, state.song_info);
  if (state.transition) {
    const t = state.transition;
    if (t.type === 'tr_phase2' && state.transitionPhaseEnd > 0) {
      _send(ws, { ...t, bgMs: Math.max(0, state.transitionPhaseEnd - Date.now()) });
    } else {
      _send(ws, t);
    }
  } else if (state.media) {
    const mediaMsg = { ...state.media };
    if (state.position) mediaMsg.pos = state.position.pos;
    _send(ws, mediaMsg);
    if (state.position) _send(ws, state.position);
  } else if (state.adActive) {
    // Neither a transition nor a song is active, and the Ad Screen was the
    // last thing shown -- replay it so a client joining mid-ad sees it
    // immediately instead of the default idle screen for up to a full
    // rotation interval.
    _send(ws, { type: 'show_ad' });
    if (state.ad_config) _send(ws, state.ad_config);
  } else {
    // Default "nothing playing" state.
    if (state.idle_config) _send(ws, state.idle_config);
  }
}

function broadcast(msg) {
  // Keep state cache up to date
  switch (msg.type) {
    case 'song_info':                                   state.song_info  = msg; break;
    case 'cdg_path': case 'video_path': case 'video_url':
      state.media = msg; state.transition = null; state.adActive = false; break;
    case 'position':                                    state.position   = msg; break;
    case 'tr_phase1':  state.transition = msg; state.adActive = false; break;
    case 'tr_phase2':  state.transition = msg; state.transitionPhaseEnd = Date.now() + (msg.bgMs || 0); state.adActive = false; break;
    case 'transition_end': state.transition = null; state.transitionPhaseEnd = 0; break;
    case 'venue_path':                                  state.venue_path = msg; break;
    case 'idle_config':                                 state.idle_config = msg; break;
    case 'ad_config':                                   state.ad_config  = msg; break;
    case 'show_ad':    state.adActive = true; break;
    case 'stop':
      state.song_info = null; state.media = null;
      state.position  = null; state.transition = null; state.adActive = false; break;
  }
  if (!wss || clients.size === 0) return;
  const data = JSON.stringify(toWebMsg(msg));
  clients.forEach(ws => { if (ws.readyState === 1) try { ws.send(data); } catch {} });
}

// Only non-empty paths get rewritten -- an empty string means "not set" to
// the sender (e.g. ad_config with no picture uploaded), and turning that into
// a non-empty "/media?p=" URL would make the client think one exists.
function toWebMsg(msg) {
  const m = { ...msg };
  if (m.path)        m.path        = `/media?p=${encodeURIComponent(m.path)}`;
  if (m.audioPath)   m.audioPath   = `/media?p=${encodeURIComponent(m.audioPath)}`;
  if (m.venuePath)   m.venuePath   = `/media?p=${encodeURIComponent(m.venuePath)}`;
  if (m.picturePath) m.picturePath = `/media?p=${encodeURIComponent(m.picturePath)}`;
  if (m.videoPath)   m.videoPath   = `/media?p=${encodeURIComponent(m.videoPath)}`;
  return m;
}

let _audioEnabled = false;

function setAudio(enabled) {
  _audioEnabled = !!enabled;
  // Push the setting to all connected web clients
  const data = JSON.stringify({ type: 'setting', key: 'audio', value: _audioEnabled });
  clients.forEach(ws => { if (ws.readyState === 1) try { ws.send(data); } catch {} });
}

function isRunning()    { return httpServer !== null; }
function getUrl()       { return _activeIP ? `http://${_activeIP}:${PORT}` : null; }
function clientCount()  { return clients.size; }
function audioEnabled() { return _audioEnabled; }
function allUrls()      { return getAllIPs().map(i => ({ label: i.name, url: `http://${i.address}:${PORT}` })); }

module.exports = { start, stop, broadcast, setAudio, isRunning, getUrl, clientCount, audioEnabled, allUrls };
