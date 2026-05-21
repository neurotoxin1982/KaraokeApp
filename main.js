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

  createMainWindow();
  setupIPC();
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

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
  ipcMain.handle('queue:get',     ()              => db.getQueue());
  ipcMain.handle('queue:history', ()              => db.getHistory());
  ipcMain.handle('queue:current', ()              => db.getCurrentSong());
  ipcMain.handle('queue:add',     (_, sid, name)  => db.addToQueue(sid, name));
  ipcMain.handle('queue:remove',  (_, id)         => db.removeFromQueue(id));
  ipcMain.handle('queue:skip',    (_, id)         => db.skipEntry(id));
  ipcMain.handle('queue:move',    (_, id, dir)    => db.moveEntry(id, dir));
  ipcMain.handle('queue:moveto',  (_, id, idx)    => db.moveToPosition(id, idx));
  ipcMain.handle('queue:next',    ()              => db.nextSong());

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
  ipcMain.handle('requests:start', async () =>
    requestServer.start(db, (singerName) => {
      if (mainWindow && !mainWindow.isDestroyed())
        mainWindow.webContents.send('mobile-request-added', singerName);
    })
  );
  ipcMain.handle('requests:stop',   ()      => requestServer.stop());
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
