const http   = require('http');
const path   = require('path');
const fs     = require('fs');
const os     = require('os');
const dgram  = require('dgram');

const PORT = 8766;
let httpServer     = null;
let wss            = null;
let _db            = null;
let _qm            = null;   // QueueManager instance
let _onNewRequest  = null;
let _activeIP      = null;
const clients      = new Set();

// ── IP detection ──────────────────────────────────────────────────────────────
function getRoutedIP() {
  return new Promise(resolve => {
    const socket = dgram.createSocket('udp4');
    socket.connect(80, '8.8.8.8', () => { resolve(socket.address().address); socket.close(); });
    socket.on('error', () => resolve(null));
  });
}

function getAllIPs() {
  const results = [];
  for (const [name, ifaces] of Object.entries(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) results.push({ name, address: iface.address });
    }
  }
  return results;
}

async function resolveIP() {
  const routed = await getRoutedIP();
  if (routed && routed !== '0.0.0.0') return routed;
  const all = getAllIPs();
  return (all.find(i => i.address.startsWith('192.168.')) || all[0])?.address || '127.0.0.1';
}

// ── Server ────────────────────────────────────────────────────────────────────
async function start(db, queueManager, onNewRequest) {
  if (httpServer) return getUrl();
  _db           = db;
  _qm           = queueManager;
  _onNewRequest = onNewRequest;
  _activeIP     = null;
  _activeIP     = await resolveIP();

  const { WebSocketServer } = require('ws');
  httpServer = http.createServer(handleRequest);
  wss = new WebSocketServer({ server: httpServer });
  wss.on('connection', ws => {
    clients.add(ws);
    ws.on('close', () => clients.delete(ws));
    ws.on('error', () => clients.delete(ws));
    _sendQueue(ws);
  });
  await new Promise(resolve => httpServer.listen(PORT, resolve));
  return getUrl();
}

async function handleRequest(req, res) {
  const url = new URL(req.url, 'http://localhost');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(200); res.end(); return; }

  // Mobile web app
  if (url.pathname === '/' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(fs.readFileSync(path.join(__dirname, '..', 'renderer', 'mobile-request.html')));
    return;
  }

  // Local song search
  if (url.pathname === '/api/songs' && req.method === 'GET') {
    const q = url.searchParams.get('q') || '';
    const songs = _db.searchSongs(q, 30);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(songs));
    return;
  }

  // YouTube search
  if (url.pathname === '/api/youtube' && req.method === 'GET') {
    const q = url.searchParams.get('q') || '';
    if (!q) { res.writeHead(400); res.end('{"error":"q required"}'); return; }
    try {
      const ytdlp = require('./ytdlp');
      const results = await ytdlp.search(q, 10);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(results));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: String(e) }));
    }
    return;
  }

  // Get queue
  if (url.pathname === '/api/queue' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(_db.getQueue()));
    return;
  }

  // Add to queue
  if (url.pathname === '/api/queue' && req.method === 'POST') {
    let body = '';
    req.on('data', d => body += d);
    req.on('end', async () => {
      try {
        const { songId, singerName, deviceId,
                youtubeId, youtubeTitle, youtubeArtist, youtubeDuration } = JSON.parse(body);
        const singer = singerName?.trim();
        if (!singer) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'singerName required' }));
          return;
        }

        // Rate limit: read limit from settings (default 3)
        const LIMIT = parseInt(_db.getSettings().request_song_limit || '3') || 3;
        if (deviceId && _db.getDeviceQueueCount(deviceId) >= LIMIT) {
          res.writeHead(429, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'limit', message: `Max ${LIMIT} songs per device in queue`, limit: LIMIT }));
          return;
        }

        let finalSongId = songId;

        // YouTube song: import first
        if (youtubeId) {
          const imported = _db.importSong({
            title:       youtubeTitle  || youtubeId,
            artist:      youtubeArtist || 'YouTube',
            file_path:   'yt:' + youtubeId,
            file_format: 'youtube',
            duration:    youtubeDuration || 0,
          });
          finalSongId = imported.id;
        }

        if (!finalSongId) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'songId or youtubeId required' }));
          return;
        }

        // QueueManager admission (per-singer limit / cooldown / inclusive-jam rules)
        if (_qm) {
          const admission = _qm.validateAdmission(singer);
          if (!admission.allowed) {
            const status = admission.reason === 'cooldown' ? 429 : 429;
            res.writeHead(status, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: admission.reason, message: admission.message || `Cannot add song: ${admission.reason}`, ...admission }));
            return;
          }
        }

        // Recently-played block
        const cfg = _db.getSettings();
        if (cfg.prevent_recent === 'true') {
          const hours = parseInt(cfg.recent_hours || '2') || 2;
          const remainingSec = _db.songPlayedRecently(finalSongId, hours);
          if (remainingSec !== null) {
            const totalMins = Math.ceil(remainingSec / 60);
            const h = Math.floor(totalMins / 60);
            const m = totalMins % 60;
            const waitStr = h > 0 ? (m > 0 ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
            res.writeHead(409, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'recent', hours, remainingSec, waitStr }));
            return;
          }
        }

        const result = _db.addToQueue(finalSongId, singer, deviceId || '');
        if (_qm) _qm.recalculateQueue();
        broadcastQueue();
        if (_onNewRequest) _onNewRequest(singer);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, ...result }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: String(e) }));
      }
    });
    return;
  }

  res.writeHead(404); res.end();
}

function _sendQueue(ws) {
  if (ws.readyState === 1) try {
    ws.send(JSON.stringify({ type: 'queue', queue: _db.getQueue() }));
  } catch {}
}

function broadcastQueue() {
  clients.forEach(ws => _sendQueue(ws));
}

function stop() {
  clients.forEach(ws => { try { ws.close(); } catch {} });
  clients.clear();
  if (httpServer) { httpServer.close(); httpServer = null; }
  wss = null; _activeIP = null;
}

function isRunning()   { return httpServer !== null; }
function getUrl()      { return _activeIP ? `http://${_activeIP}:${PORT}` : null; }
function clientCount() { return clients.size; }
function allUrls()     { return getAllIPs().map(i => ({ label: i.name, url: `http://${i.address}:${PORT}` })); }

module.exports = { start, stop, broadcastQueue, isRunning, getUrl, clientCount, allUrls };
