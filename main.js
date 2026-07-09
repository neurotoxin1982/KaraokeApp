const { app, BrowserWindow, ipcMain, dialog, protocol, net, Menu } = require('electron');
const path   = require('path');
const fs     = require('fs');
const crypto = require('crypto');

// Disable GPU sandbox issues on some Windows systems
app.commandLine.appendSwitch('--disable-gpu-sandbox');

let mainWindow = null;
let playerWindow = null;
let db = null;
let scanner = null;
let networkPlayer = null;
let requestServer = null;
let queueManager  = null;
let relayClient   = null;

// ── Protocol: serve local files as localfile:// ───────────────────────────────
const MIME_TYPES = {
  '.mp4': 'video/mp4',   '.m4v': 'video/mp4',
  '.webm': 'video/webm',
  '.avi': 'video/x-msvideo',
  '.mkv': 'video/x-matroska',
  '.mpeg': 'video/mpeg', '.mpg': 'video/mpeg',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',   '.aac': 'audio/aac',
  '.ogg': 'audio/ogg',   '.oga': 'audio/ogg',
  '.flac': 'audio/flac',
  '.wav': 'audio/wav',
  '.cdg': 'application/octet-stream',
};

protocol.registerSchemesAsPrivileged([
  { scheme: 'localfile', privileges: { secure: true, standard: true, supportFetchAPI: true, bypassCSP: true } }
]);

// ── App ready ─────────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  protocol.handle('localfile', async (request) => {
    let filePath = request.url.slice('localfile://'.length);
    filePath = decodeURIComponent(filePath);
    if (filePath.startsWith('/') && filePath[2] === ':') filePath = filePath.slice(1);
    filePath = filePath.replace(/\//g, path.sep);
    try {
      const data = await fs.promises.readFile(filePath);
      const rangeHeader = request.headers.get('Range');
      if (rangeHeader) {
        const m = rangeHeader.match(/bytes=(\d+)-(\d*)/);
        if (m) {
          const start = parseInt(m[1]);
          const end = m[2] ? parseInt(m[2]) : data.length - 1;
          const ext2 = path.extname(filePath).toLowerCase();
          return new Response(data.slice(start, end + 1), {
            status: 206,
            headers: {
              'Content-Type': MIME_TYPES[ext2] || 'application/octet-stream',
              'Content-Range': `bytes ${start}-${end}/${data.length}`,
              'Content-Length': String(end - start + 1),
              'Accept-Ranges': 'bytes',
            }
          });
        }
      }
      const ext = path.extname(filePath).toLowerCase();
      const contentType = MIME_TYPES[ext] || 'application/octet-stream';
      return new Response(data, {
        headers: { 'Content-Type': contentType, 'Accept-Ranges': 'bytes', 'Content-Length': String(data.length) }
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });

  db            = require('./src/database');
  scanner       = require('./src/scanner');
  networkPlayer = require('./src/network-player');
  requestServer = require('./src/request-server');
  await db.initialize();

  // Best-effort background start; YouTube playback falls back gracefully if this never comes up.
  require('./src/pot-server').ensure().catch(() => {});
  const QueueManager = require('./src/QueueManager');
  queueManager = new QueueManager(db);

  // Sort queue immediately on startup so the UI is correct from the first render
  queueManager.recalculateQueue();

  // Periodic maintenance every 10 s: purge cooldowns + re-sort.
  // If the order changed, push an instant update to the renderer.
  setInterval(() => {
    queueManager.purgeCooldowns();
    const changed = queueManager.recalculateQueue();
    if (changed) _notifyQueueChanged();
  }, 10_000);

  // The app has its own custom UI chrome; the default File/Edit/View/Window/Help
  // bar is Electron boilerplate that was never replaced.
  Menu.setApplicationMenu(null);

  createMainWindow();
  setupIPC();
  _initRelay();

  // Local WiFi request server: auto-start unless the admin left the venue offline last time.
  if (db.getSetting('venue_online') !== 'false') {
    requestServer.start(db, queueManager, _requestServerOnNewRequest).catch(() => {});
  }
});

function _requestServerOnNewRequest(singerName) {
  if (mainWindow && !mainWindow.isDestroyed())
    mainWindow.webContents.send('mobile-request-added', singerName);
}

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => require('./src/pot-server').stop());

function _ytCookieOpts() {
  return {
    file:    db.getSetting('youtube_cookies_file'),
    browser: db.getSetting('youtube_cookies_browser'),
  };
}

function _ytFilterOpts() {
  return require('./src/ytdlp').filterOptsFromSettings((k) => db.getSetting(k));
}

function _sourcesEnabled() {
  return {
    local:   db.getSetting('source_local_enabled')   !== 'false',
    youtube: db.getSetting('source_youtube_enabled') !== 'false',
  };
}

function _notifyQueueChanged() {
  if (mainWindow && !mainWindow.isDestroyed())
    mainWindow.webContents.send('queue-reordered');
}

function _broadcastToAll(msg) {
  BrowserWindow.getAllWindows().forEach(w => {
    if (!w.isDestroyed()) w.webContents.send('broadcast', msg);
  });
}

// ── Windows ───────────────────────────────────────────────────────────────────
// F12 still opens DevTools even with the app menu removed (that menu was the
// only thing wiring up that accelerator).
function _registerDevToolsToggle(win) {
  win.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') win.webContents.toggleDevTools();
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 900,
    minWidth: 900, minHeight: 600,
    backgroundColor: '#111827',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile('renderer/index.html');
  mainWindow.on('closed', () => { mainWindow = null; app.quit(); });
  _registerDevToolsToggle(mainWindow);
}

function openPlayerWindow() {
  if (playerWindow && !playerWindow.isDestroyed()) { playerWindow.focus(); return; }
  playerWindow = new BrowserWindow({
    width: 1280, height: 720,
    backgroundColor: '#000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  playerWindow.loadFile('renderer/player.html');
  playerWindow.on('closed', () => { playerWindow = null; });
  _registerDevToolsToggle(playerWindow);
}

// ── IPC bridge ────────────────────────────────────────────────────────────────
function setupIPC() {
  // Songs
  ipcMain.handle('songs:search',  (_, q, limit)  => db.searchSongs(q, limit));
  ipcMain.handle('songs:getAll',  (_, opts)       => db.getSongs(opts));
  ipcMain.handle('songs:import',  (_, meta)       => db.importSong(meta));
  ipcMain.handle('songs:edit',    (_, id, data)   => db.editSong(id, data));
  ipcMain.handle('songs:delete',  (_, id)         => db.deleteSong(id));
  ipcMain.handle('songs:rate',    (_, id, rating) => db.rateSong(id, rating));
  ipcMain.handle('songs:favorite',(_, id)         => db.toggleFavorite(id));

  // Queue
  ipcMain.handle('queue:restoreInfo',   ()  => db.getQueueRestoreInfo());
  ipcMain.handle('queue:restore',       ()  => db.restoreQueue());
  ipcMain.handle('queue:clearPending',  ()  => db.clearPendingQueue());
  ipcMain.handle('queue:get',           ()  => db.getQueue());
  ipcMain.handle('queue:history', ()              => db.getHistory());
  ipcMain.handle('queue:current', ()              => db.getCurrentSong());

  const _addRecalc = () => { if (queueManager.recalculateQueue()) _notifyQueueChanged(); };

  ipcMain.handle('queue:add', (_, sid, name) => {
    const admission = queueManager.validateAdmission(name);
    if (!admission.allowed) return { error: admission.reason, ...admission };
    const result = db.addToQueue(sid, name);
    _addRecalc();
    return result;
  });

  ipcMain.handle('queue:remove', (_, id) => {
    const r = db.removeFromQueue(id);
    _addRecalc();
    return r;
  });

  ipcMain.handle('queue:skip', (_, id) => {
    const r = db.skipEntry(id);
    _addRecalc();
    return r;
  });

  ipcMain.handle('queue:move', (_, id, dir) => {
    db.setPinned(id, true);
    return db.moveEntry(id, dir);
  });

  ipcMain.handle('queue:moveto', (_, id, idx) => {
    db.setPinned(id, true);
    return db.moveToPosition(id, idx);
  });

  ipcMain.handle('queue:setPinned', (_, id, pinned) => {
    db.setPinned(id, pinned);
    if (!pinned) queueManager.recalculateQueue();
    _notifyQueueChanged();
    return { ok: true };
  });

  ipcMain.handle('queue:next', () => {
    const current = db.getCurrentSong();
    const result  = db.nextSong();
    if (current?.singer_name) queueManager.applyCooldown(current.singer_name);
    _addRecalc();
    return result;
  });

  // QueueManager – algorithm controls
  ipcMain.handle('queue:algo:get',       ()        => queueManager.status());
  ipcMain.handle('queue:algo:validate',  (_, name) => queueManager.validateAdmission(name));

  ipcMain.handle('queue:algo:setPreset', (_, p) => {
    db.setSetting('queue_preset', p);
    queueManager.recalculateQueue();
    _notifyQueueChanged();          // instant push to renderer
    return { ok: true };
  });

  ipcMain.handle('queue:algo:setCooldown', (_, sec) => {
    db.setSetting('queue_cooldown_sec', String(sec));
    return { ok: true };
  });

  // Library
  ipcMain.handle('library:openDialog', async () => {
    const r = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle('library:scan', async (event, dir) => {
    return scanner.scan(dir, db, (p) => event.sender.send('scan:progress', p));
  });
  ipcMain.handle('library:stats', () => db.getStats());

  // Settings
  ipcMain.handle('settings:get', ()           => db.getSettings());
  ipcMain.handle('settings:set', (_, k, v)    => db.setSetting(k, v));

  // Song sources (local library / YouTube) — which are offered for requests
  ipcMain.handle('sources:get', () => _sourcesEnabled());
  ipcMain.handle('sources:set', (_, patch) => {
    if (patch.local   !== undefined) db.setSetting('source_local_enabled',   String(patch.local));
    if (patch.youtube !== undefined) db.setSetting('source_youtube_enabled', String(patch.youtube));
    const sources = _sourcesEnabled();
    relayClient?.updateConfig({ sourcesEnabled: sources });
    return sources;
  });

  // File reading (bypasses URL encoding issues with custom protocol)
  ipcMain.handle('read:file', async (_, filePath) => {
    return fs.promises.readFile(filePath);
  });

  // Image file picker (for venue image)
  ipcMain.handle('dialog:openImage', async () => {
    const r = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      filters: [{ name: 'Images', extensions: ['jpg','jpeg','png','gif','webp','bmp'] }]
    });
    return r.canceled ? null : r.filePaths[0];
  });

  ipcMain.handle('dialog:openVideo', async () => {
    const r = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      filters: [{ name: 'Videos', extensions: ['mp4','webm','mkv','avi','mov','m4v'] }]
    });
    return r.canceled ? null : r.filePaths[0];
  });

  // Cookies file picker (Netscape format, for yt-dlp)
  ipcMain.handle('dialog:openCookiesFile', async () => {
    const r = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
      filters: [{ name: 'Cookies file', extensions: ['txt'] }]
    });
    return r.canceled ? null : r.filePaths[0];
  });

  ipcMain.handle('pot:status', () => require('./src/pot-server').status());

  // YouTube (yt-dlp)
  ipcMain.handle('youtube:ensure', async (event) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.ensureYtDlp((p) => event.sender.send('youtube:progress', p));
  });
  ipcMain.handle('youtube:search', async (_, query) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.search(query, 10, _ytCookieOpts(), _ytFilterOpts());
  });
  ipcMain.handle('youtube:stream', async (_, videoId) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.getStreamUrl(videoId, _ytCookieOpts());
  });

  // Software update (manual: check -> download -> install, each user-triggered)
  ipcMain.handle('update:getVersion', () => app.getVersion());
  ipcMain.handle('update:check', () => require('./src/updater').checkForUpdate());
  ipcMain.handle('update:download', async (event, downloadUrl, assetName) => {
    const updater = require('./src/updater');
    return updater.downloadUpdate(downloadUrl, assetName, (pct) => event.sender.send('update:progress', pct));
  });
  ipcMain.handle('update:install', (_, filePath) => require('./src/updater').installUpdate(filePath));

  // Player window
  ipcMain.handle('player:open', () => openPlayerWindow());

  // Singer request server
  ipcMain.handle('requests:start', async () => requestServer.start(db, queueManager, _requestServerOnNewRequest));
  ipcMain.handle('requests:stop', () => { requestServer.stop(); });
  ipcMain.handle('requests:status', ()      => ({
    running: requestServer.isRunning(),
    url:     requestServer.isRunning() ? requestServer.getUrl() : null,
    clients: requestServer.clientCount(),
    allUrls: requestServer.isRunning() ? requestServer.allUrls() : [],
  }));

  // Venue online/offline — master switch for whether remote singers (local WiFi
  // or internet relay) can add songs at all.
  ipcMain.handle('venue:getOnline', () => db.getSetting('venue_online') !== 'false');
  ipcMain.handle('venue:setOnline', async (_, online) => {
    db.setSetting('venue_online', String(online));
    if (online) {
      if (!requestServer.isRunning()) await requestServer.start(db, queueManager, _requestServerOnNewRequest).catch(() => {});
      if (db.getSetting('relay_enabled') === 'true') relayClient?.setEnabled(true);
    } else {
      requestServer.stop();
      relayClient?.setEnabled(false);
    }
    return { online };
  });

  // Wireless network player
  ipcMain.handle('network:start',    async () => networkPlayer.start());
  ipcMain.handle('network:stop',     ()       => networkPlayer.stop());
  ipcMain.handle('network:setAudio', (_, val) => networkPlayer.setAudio(val));
  ipcMain.handle('network:status',   ()       => ({
    running: networkPlayer.isRunning(),
    url:     networkPlayer.isRunning() ? networkPlayer.getUrl() : null,
    clients: networkPlayer.clientCount(),
    audio:   networkPlayer.audioEnabled(),
    allUrls: networkPlayer.isRunning() ? networkPlayer.allUrls() : [],
  }));

  // Background music — list audio files in a folder
  ipcMain.handle('bgmusic:list', async (_, dirPath) => {
    const AUDIO_EXT = new Set(['.mp3', '.ogg', '.m4a', '.aac', '.flac', '.wav']);
    try {
      const entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
      return entries
        .filter(e => e.isFile() && AUDIO_EXT.has(path.extname(e.name).toLowerCase()))
        .map(e => path.join(dirPath, e.name));
    } catch { return []; }
  });

  // ── Spotify native mute/unmute + SMTC track query ────────────────────────────
  const spotifyVol = require('./src/spotify-volume');
  ipcMain.handle('spotify:mute',       () => spotifyVol.mute());
  ipcMain.handle('spotify:unmute',     () => spotifyVol.unmute());
  ipcMain.handle('spotify:now-playing',() => spotifyVol.getCurrentTrack());

  // Broadcast to all windows (for popout sync) + relay to network clients
  ipcMain.on('broadcast', (event, msg) => {
    BrowserWindow.getAllWindows().forEach(w => {
      if (!w.isDestroyed() && w.webContents !== event.sender)
        w.webContents.send('broadcast', msg);
    });
    networkPlayer.broadcast(msg);
  });

  // ── Relay (remote song requests) ─────────────────────────────────────────────
  ipcMain.handle('relay:status', () => relayClient?.getStatus() || { connected: false });

  ipcMain.handle('relay:getConfig', () => {
    const s = db.getSettings();
    return {
      enabled:   s.relay_enabled === 'true',
      serverUrl: s.relay_server_url || 'ws://89.167.78.11:3000/ws',
      venueId:   s.relay_venue_id  || '',
      venueName: s.relay_venue_name || '',
    };
  });

  ipcMain.handle('relay:setConfig', (_, cfg) => {
    if (cfg.enabled   !== undefined) { db.setSetting('relay_enabled',    String(cfg.enabled)); relayClient?.setEnabled(cfg.enabled); }
    if (cfg.serverUrl !== undefined)   db.setSetting('relay_server_url', cfg.serverUrl);
    if (cfg.venueId   !== undefined)   db.setSetting('relay_venue_id',   cfg.venueId);
    if (cfg.venueName !== undefined)   db.setSetting('relay_venue_name', cfg.venueName);
    if (cfg.serverUrl || cfg.venueName) relayClient?.updateConfig(cfg);
    return { ok: true };
  });

  ipcMain.handle('relay:qrcode', async (_, url) => {
    const QRCode = require('qrcode');
    return QRCode.toDataURL(url, { width: 256, margin: 2, color: { dark: '#fff', light: '#1f2937' } });
  });
}

// ── Relay init ────────────────────────────────────────────────────────────────
async function _initRelay() {
  const settings = db.getSettings();
  let venueId = settings.relay_venue_id;
  if (!venueId) {
    venueId = crypto.randomBytes(4).toString('hex');
    db.setSetting('relay_venue_id', venueId);
  }
  // Proves to the relay server that reconnects are really this venue, not someone
  // else registering a guessed/observed venueId. Never exposed to the renderer/UI.
  let venueSecret = settings.relay_venue_secret;
  if (!venueSecret) {
    venueSecret = crypto.randomBytes(16).toString('hex');
    db.setSetting('relay_venue_secret', venueSecret);
  }
  relayClient = require('./src/relay-client');
  relayClient.init(
    {
      enabled:   settings.relay_enabled === 'true' && settings.venue_online !== 'false',
      serverUrl: settings.relay_server_url || 'ws://89.167.78.11:3000/ws',
      venueId,
      venueSecret,
      venueName: settings.relay_venue_name || 'My Karaoke Venue',
      sourcesEnabled: _sourcesEnabled(),
    },
    async (msg) => {
      if (msg.type === 'search') {
        if (!_sourcesEnabled().local) {
          relayClient.send({ type: 'search-results', requestId: msg.requestId, results: [] });
          return;
        }
        const rows = db.searchSongs(msg.query || '', 20);
        relayClient.send({
          type:      'search-results',
          requestId: msg.requestId,
          results:   rows.map(s => ({ id: s.id, title: s.title, artist: s.artist || '' })),
        });
      } else if (msg.type === 'add-to-queue') {
        if (!_sourcesEnabled().local) {
          relayClient.send({ type: 'add-to-queue-result', requestId: msg.requestId, ok: false, error: 'disabled' });
          return;
        }
        const admission = queueManager.validateAdmission(msg.singerName || '');
        if (!admission.allowed) {
          relayClient.send({ type: 'add-to-queue-result', requestId: msg.requestId, ok: false, error: admission.reason });
          return;
        }
        const result = db.addToQueue(msg.songId, msg.singerName);
        if (queueManager.recalculateQueue()) _notifyQueueChanged();
        relayClient.send({ type: 'add-to-queue-result', requestId: msg.requestId, ok: !!result, error: result?.error });
        if (mainWindow && !mainWindow.isDestroyed())
          mainWindow.webContents.send('relay-request', { ...msg, type: 'request' });
      } else if (msg.type === 'youtube-search') {
        if (!_sourcesEnabled().youtube) {
          relayClient.send({ type: 'youtube-search-results', requestId: msg.requestId, results: [] });
          return;
        }
        try {
          const ytdlp = require('./src/ytdlp');
          const results = await ytdlp.search(msg.query || '', 8, _ytCookieOpts(), _ytFilterOpts());
          relayClient.send({ type: 'youtube-search-results', requestId: msg.requestId, results });
        } catch (err) {
          relayClient.send({ type: 'youtube-search-results', requestId: msg.requestId, results: [], error: err.message });
        }
      } else if (msg.type === 'add-youtube-to-queue') {
        if (!_sourcesEnabled().youtube) {
          relayClient.send({ type: 'add-youtube-to-queue-result', requestId: msg.requestId, ok: false, error: 'disabled' });
          return;
        }
        const { singerName, videoId, title, artist, duration } = msg;
        const admission = queueManager.validateAdmission(singerName || '');
        if (!admission.allowed) {
          relayClient.send({ type: 'add-youtube-to-queue-result', requestId: msg.requestId, ok: false, error: admission.reason });
          return;
        }
        const song   = db.importSong({ title, artist: artist || '', file_path: 'yt:' + videoId, file_format: 'youtube', duration: duration || 0 });
        const result = db.addToQueue(song.id, singerName);
        if (queueManager.recalculateQueue()) _notifyQueueChanged();
        relayClient.send({ type: 'add-youtube-to-queue-result', requestId: msg.requestId, ok: !!result });
        if (mainWindow && !mainWindow.isDestroyed())
          mainWindow.webContents.send('relay-request', { type: 'request', singerName, songTitle: title, artist: artist || '' });
      } else if (msg.type === 'request') {
        if (mainWindow && !mainWindow.isDestroyed())
          mainWindow.webContents.send('relay-request', msg);
      }
    },
    (status) => {
      if (mainWindow && !mainWindow.isDestroyed())
        mainWindow.webContents.send('relay-status', status);
    }
  );
}
