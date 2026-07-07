const express    = require('express');
const { WebSocketServer } = require('ws');
const http       = require('http');
const path       = require('path');

const app    = express();
const server = http.createServer(app);
const wss    = new WebSocketServer({ server, path: '/ws' });

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// venueId -> { ws, name, connectedAt }
const venues = new Map();

// requestId -> { resolve, timer } for request/response over WS
const pending = new Map();

function _awaitReply(requestId, timeoutMs = 6000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => { pending.delete(requestId); resolve(null); }, timeoutMs);
    pending.set(requestId, { resolve, timer });
  });
}

function _uid() { return Math.random().toString(36).slice(2) + Date.now().toString(36); }

// ── Electron app connects here ────────────────────────────────────────────────
wss.on('connection', (ws) => {
  let venueId = null;

  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return; }

    if (msg.type === 'register') {
      venueId = msg.venueId;
      venues.set(venueId, {
        ws, name: msg.venueName || venueId, connectedAt: new Date(),
        sourcesEnabled: msg.sourcesEnabled || { local: true, youtube: true },
      });
      ws.send(JSON.stringify({
        type: 'registered',
        venueId,
        url: `http://${process.env.PUBLIC_HOST || '89.167.78.11:3000'}/v/${venueId}`,
      }));
      console.log(`[+] venue connected: ${venueId} ("${msg.venueName || ''}")`);
    } else if (msg.type === 'search-results' || msg.type === 'add-to-queue-result' ||
               msg.type === 'youtube-search-results' || msg.type === 'add-youtube-to-queue-result') {
      const p = pending.get(msg.requestId);
      if (p) { clearTimeout(p.timer); pending.delete(msg.requestId); p.resolve(msg); }
    }
  });

  ws.on('close', () => {
    if (venueId) { venues.delete(venueId); console.log(`[-] venue disconnected: ${venueId}`); }
  });

  ws.on('error', () => {
    if (venueId) venues.delete(venueId);
  });
});

// ── Customer-facing routes ────────────────────────────────────────────────────
app.get('/v/:venueId', (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.status(404).sendFile(path.join(__dirname, 'public', 'offline.html'));
  res.sendFile(path.join(__dirname, 'public', 'request.html'));
});

app.get('/v/:venueId/info', (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.status(404).json({ online: false });
  res.json({ online: true, name: venue.name, sourcesEnabled: venue.sourcesEnabled || { local: true, youtube: true } });
});

app.post('/v/:venueId/request', (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.status(404).json({ error: 'Venue offline' });

  const { singerName, songTitle, artist } = req.body;
  if (!singerName?.trim() || !songTitle?.trim())
    return res.status(400).json({ error: 'Singer name and song title are required' });

  try {
    venue.ws.send(JSON.stringify({
      type:       'request',
      singerName: singerName.trim(),
      songTitle:  songTitle.trim(),
      artist:     (artist || '').trim(),
      timestamp:  new Date().toISOString(),
    }));
    res.json({ ok: true });
  } catch {
    res.status(500).json({ error: 'Failed to forward request' });
  }
});

app.get('/v/:venueId/search', async (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.json([]);
  const requestId = _uid();
  try {
    venue.ws.send(JSON.stringify({ type: 'search', requestId, query: (req.query.q || '').trim() }));
    const reply = await _awaitReply(requestId);
    res.json(reply?.results || []);
  } catch {
    res.json([]);
  }
});

app.post('/v/:venueId/queue', async (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.status(404).json({ error: 'Venue offline' });

  const { songId, singerName, songTitle, artist } = req.body;
  if (!singerName?.trim()) return res.status(400).json({ error: 'Singer name required' });
  if (!songId)             return res.status(400).json({ error: 'Song ID required' });

  const requestId = _uid();
  try {
    venue.ws.send(JSON.stringify({
      type: 'add-to-queue', requestId,
      songId, singerName: singerName.trim(),
      songTitle, artist,
    }));
    const reply = await _awaitReply(requestId);
    if (!reply) return res.status(504).json({ error: 'Venue did not respond in time' });
    if (!reply.ok) return res.status(400).json({ error: reply.error || 'Could not add to queue' });
    res.json({ ok: true });
  } catch {
    res.status(500).json({ error: 'Failed to forward request' });
  }
});

// ── Utility ───────────────────────────────────────────────────────────────────
app.get('/v/:venueId/youtube-search', async (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.json([]);
  const requestId = _uid();
  try {
    venue.ws.send(JSON.stringify({ type: 'youtube-search', requestId, query: (req.query.q || '').trim() }));
    const reply = await _awaitReply(requestId, 30000);
    res.json(reply?.results || []);
  } catch { res.json([]); }
});

app.post('/v/:venueId/youtube-queue', async (req, res) => {
  const venue = venues.get(req.params.venueId);
  if (!venue) return res.status(404).json({ error: 'Venue offline' });
  const { videoId, singerName, title, artist, duration } = req.body;
  if (!singerName?.trim()) return res.status(400).json({ error: 'Singer name required' });
  if (!videoId)            return res.status(400).json({ error: 'Video ID required' });
  const requestId = _uid();
  try {
    venue.ws.send(JSON.stringify({ type: 'add-youtube-to-queue', requestId, videoId, singerName: singerName.trim(), title, artist, duration }));
    const reply = await _awaitReply(requestId, 10000);
    if (!reply) return res.status(504).json({ error: 'Venue did not respond in time' });
    if (!reply.ok) return res.status(400).json({ error: reply.error || 'Could not add to queue' });
    res.json({ ok: true });
  } catch { res.status(500).json({ error: 'Failed to forward request' }); }
});

app.get('/health', (_req, res) => res.json({ ok: true, venues: venues.size }));

app.get('/venues', (_req, res) => {
  const list = [];
  venues.forEach((v, id) => list.push({ id, name: v.name }));
  res.json(list);
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`karaoke-relay listening on :${PORT}`));
