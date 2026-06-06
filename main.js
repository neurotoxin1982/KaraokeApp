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
  // Restore Spotify auth state from DB
  const spotify = require('./src/spotify');
  const spClientId = db.getSetting('spotify_client_id');
  const spRefresh  = db.getSetting('spotify_refresh_token');
  if (spClientId) spotify.configure(spClientId, spRefresh || null);
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

  // Allow Spotify embed login popups to open in a real browser window
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes('spotify.com') || url.includes('accounts.spotify.com')) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width: 500, height: 700,
          title: 'Spotify Login',
          webPreferences: { nodeIntegration: false, contextIsolation: true },
        },
      };
    }
    return { action: 'deny' };
  });
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
    return url;
  });
  ipcMain.handle('requests:stop', () => { requestServer.stop(); });
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

  // ── Spotify ────────────────────────────────────────────────────────────────

  // Opens a login window. After login navigates to open.spotify.com and fetches
  // the access token directly from within that page's browser context
  // (same-origin fetch → no 403, all cookies included automatically).
  ipcMain.handle('spotify:openLogin', () => {
    return new Promise((resolve) => {
      const win = new BrowserWindow({
        width: 500, height: 720,
        title: 'Spotify – Einloggen',
        webPreferences: { nodeIntegration: false, contextIsolation: true },
      });
      win.loadURL('https://accounts.spotify.com/en/login');
      win.setMenuBarVisibility(false);

      let tokenFetched = false;
      const tryFetchToken = async (url) => {
        if (tokenFetched) return;
        if (!url.startsWith('https://open.spotify.com/')) return;
        tokenFetched = true;
        console.log('[Spotify] on open.spotify.com, fetching token from page context…');

        try {
          // Run a same-origin fetch inside the page — Chromium adds all the
          // right headers/cookies automatically, so Spotify won't 403 it.
          const result = await win.webContents.executeJavaScript(`
            fetch('/get_access_token?reason=transport&productType=web_player')
              .then(r => r.json())
              .then(d => JSON.stringify(d))
              .catch(e => JSON.stringify({ error: e.message }))
          `);
          const data = JSON.parse(result);
          console.log('[Spotify] token result:', JSON.stringify(data).slice(0, 120));
          if (data.accessToken && !data.isAnonymous) {
            resolve({ token: data.accessToken });
          } else {
            resolve({ error: 'not_logged_in' });
          }
        } catch (e) {
          console.error('[Spotify] executeJavaScript error:', e);
          resolve({ error: e.message });
        }
        if (!win.isDestroyed()) win.close();
      };

      win.webContents.on('did-navigate', (_, url) => {
        console.log('[Spotify login] navigated to:', url);
        // After login, force navigation to open.spotify.com (ensures sp_dc is set)
        if (!url.includes('accounts.spotify.com') && !url.startsWith('https://open.spotify.com/')) {
          win.loadURL('https://open.spotify.com/');
        } else {
          tryFetchToken(url);
        }
      });

      // User closed window manually without completing login
      win.on('closed', () => { if (!tokenFetched) resolve({ error: 'window_closed' }); });
    });
  });

  // ── Spotify Connect API helpers ────────────────────────────────────────────
  // Makes an authenticated call to api.spotify.com using the stored OAuth token.
  // Throws with err.code === 'TOKEN_REFRESH_FAILED' when the refresh token is bad.
  async function _spAPI(path, method = 'GET', body = null) {
    const spotify = require('./src/spotify');
    const https   = require('https');
    let token;
    try {
      token = await spotify.getToken();
    } catch(e) {
      if (e.code === 'TOKEN_REFRESH_FAILED') {
        // Wipe from DB so the bad token isn't loaded on next restart
        db.setSetting('spotify_refresh_token', '');
      }
      throw e;
    }
    return new Promise((resolve, reject) => {
      const data = body ? Buffer.from(JSON.stringify(body)) : null;
      const req  = https.request({
        hostname: 'api.spotify.com',
        path,
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
          ...(data ? { 'Content-Length': data.length } : {}),
        },
      }, (res) => {
        let raw = '';
        res.on('data', c => raw += c);
        res.on('end', () => {
          if (res.statusCode === 204) return resolve({ ok: true });
          try { resolve({ status: res.statusCode, data: JSON.parse(raw) }); }
          catch { resolve({ status: res.statusCode, data: raw }); }
        });
      });
      req.on('error', reject);
      if (data) req.write(data);
      req.end();
    });
  }

  ipcMain.handle('spotify:connect-play', async (_, playlistUri) => {
    try {
      const body = playlistUri ? { context_uri: playlistUri } : undefined;
      let r = await _spAPI('/v1/me/player/play', 'PUT', body);

      // 404 = no active device. Try to find one and transfer playback to it, then retry.
      if (r.status === 404) {
        const devRes = await _spAPI('/v1/me/player/devices', 'GET');
        const devices = devRes?.data?.devices || [];
        if (!devices.length) return { error: 'No Spotify device found — open Spotify on your phone or laptop first.' };
        const device = devices.find(d => !d.is_restricted) || devices[0];
        await _spAPI('/v1/me/player', 'PUT', { device_ids: [device.id], play: false });
        await new Promise(res => setTimeout(res, 600)); // give Spotify a moment to transfer
        r = await _spAPI('/v1/me/player/play', 'PUT', body);
      }

      if (r.status === 404) return { error: 'No active Spotify device — open Spotify on your phone or laptop.' };
      if (r.status === 403) return { error: 'Spotify Premium required.' };
      return { ok: true };
    } catch(e) {
      if (e.code === 'TOKEN_REFRESH_FAILED') return { error: e.message, needsReconnect: true };
      return { error: e.message };
    }
  });

  ipcMain.handle('spotify:connect-pause', async () => {
    try { await _spAPI('/v1/me/player/pause', 'PUT'); return { ok: true }; }
    catch(e) { return { error: e.message }; }
  });

  ipcMain.handle('spotify:connect-current', async () => {
    try {
      const r = await _spAPI('/v1/me/player/currently-playing');
      if (!r || r.status === 204 || !r.data) return { playing: false };
      const item = r.data.item;
      return {
        playing: true,
        isPlaying: r.data.is_playing,
        trackName: item?.name || '',
        artist: item?.artists?.map(a => a.name).join(', ') || '',
        albumArt: item?.album?.images?.[2]?.url || item?.album?.images?.[0]?.url || null,
      };
    } catch(e) {
      if (e.code === 'TOKEN_REFRESH_FAILED') return { error: e.message, needsReconnect: true };
      return { error: e.message };
    }
  });

  ipcMain.handle('spotify:status', () => {
    const spotify = require('./src/spotify');
    return { authenticated: spotify.isAuthenticated() };
  });

  ipcMain.handle('spotify:configure', (_, clientId) => {
    const spotify = require('./src/spotify');
    const refresh = db.getSetting('spotify_refresh_token') || null;
    spotify.configure(clientId, refresh);
    return { ok: true };
  });

  ipcMain.handle('spotify:auth', async () => {
    try {
      const spotify = require('./src/spotify');
      const tokens  = await spotify.authorize();
      db.setSetting('spotify_refresh_token', tokens.refresh);
      return { ok: true, access: tokens.access };
    } catch(e) { return { error: e.message }; }
  });

  ipcMain.handle('spotify:token', async () => {
    try {
      const spotify = require('./src/spotify');
      const token   = await spotify.getToken();
      return { token };
    } catch(e) { return { error: e.message }; }
  });

  ipcMain.handle('spotify:disconnect', () => {
    const spotify = require('./src/spotify');
    spotify.disconnect();
    db.setSetting('spotify_refresh_token', '');
    return { ok: true };
  });

  // Broadcast to all windows (for popout sync) + relay to network clients
  ipcMain.on('broadcast', (event, msg) => {
    BrowserWindow.getAllWindows().forEach(w => {
      if (!w.isDestroyed() && w.webContents !== event.sender)
        w.webContents.send('broadcast', msg);
    });
    networkPlayer.broadcast(msg);
  });
}
