const { app, BrowserWindow, ipcMain, dialog, protocol, net } = require('electron');
const path = require('path');
const fs   = require('fs');

// Disable GPU sandbox issues on some Windows systems
app.commandLine.appendSwitch('--disable-gpu-sandbox');

let mainWindow = null;
let playerWindow = null;
let db = null;
let scanner = null;
let networkPlayer = null;
let requestServer = null;
let queueManager  = null;
let _qrDataUrl    = null;

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

  createMainWindow();
  setupIPC();
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

function _notifyQueueChanged() {
  if (mainWindow && !mainWindow.isDestroyed())
    mainWindow.webContents.send('queue-reordered');
}

function _broadcastToAll(msg) {
  BrowserWindow.getAllWindows().forEach(w => {
    if (!w.isDestroyed()) w.webContents.send('broadcast', msg);
  });
}

async function _updateQr(url) {
  if (!url) { _qrDataUrl = null; }
  else {
    try {
      const QRCode = require('qrcode');
      const svg = await QRCode.toString(url, { type: 'svg', margin: 1 });
      _qrDataUrl = 'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');
      console.log('[QR] generated for', url);
    } catch (e) {
      console.error('[QR] generation failed:', e);
      _qrDataUrl = null;
    }
  }
  const msg = { type: 'qr_url', dataUrl: _qrDataUrl };
  _broadcastToAll(msg);
  networkPlayer.broadcast(msg);
}

// ── Windows ───────────────────────────────────────────────────────────────────
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

  // YouTube (yt-dlp)
  ipcMain.handle('youtube:ensure', async (event) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.ensureYtDlp((p) => event.sender.send('youtube:progress', p));
  });
  ipcMain.handle('youtube:search', async (_, query) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.search(query);
  });
  ipcMain.handle('youtube:stream', async (_, videoId) => {
    const ytdlp = require('./src/ytdlp');
    return ytdlp.getStreamUrl(videoId);
  });

  // Player window
  ipcMain.handle('player:open', () => openPlayerWindow());

  // Singer request server
  ipcMain.handle('requests:start', async () => {
    const url = await requestServer.start(db, queueManager, (singerName) => {
      if (mainWindow && !mainWindow.isDestroyed())
        mainWindow.webContents.send('mobile-request-added', singerName);
    });
    await _updateQr(url);
    return url;
  });
  ipcMain.handle('requests:stop', () => { requestServer.stop(); _updateQr(null); });
  ipcMain.handle('requests:qr',   () => _qrDataUrl);
  ipcMain.handle('requests:status', ()      => ({
    running: requestServer.isRunning(),
    url:     requestServer.isRunning() ? requestServer.getUrl() : null,
    clients: requestServer.clientCount(),
    allUrls: requestServer.isRunning() ? requestServer.allUrls() : [],
  }));

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

  // Broadcast to all windows (for popout sync) + relay to network clients
  ipcMain.on('broadcast', (event, msg) => {
    BrowserWindow.getAllWindows().forEach(w => {
      if (!w.isDestroyed() && w.webContents !== event.sender)
        w.webContents.send('broadcast', msg);
    });
    networkPlayer.broadcast(msg);
  });
}
