const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Songs
  searchSongs:    (q, limit)      => ipcRenderer.invoke('songs:search', q, limit),
  getSongs:       (opts)          => ipcRenderer.invoke('songs:getAll', opts),
  importSong:     (meta)          => ipcRenderer.invoke('songs:import', meta),
  editSong:       (id, data)      => ipcRenderer.invoke('songs:edit', id, data),
  deleteSong:     (id)            => ipcRenderer.invoke('songs:delete', id),
  rateSong:       (id, rating)    => ipcRenderer.invoke('songs:rate', id, rating),
  toggleFavorite: (id)            => ipcRenderer.invoke('songs:favorite', id),

  // Queue
  getQueueRestoreInfo: ()          => ipcRenderer.invoke('queue:restoreInfo'),
  restoreQueue:        ()          => ipcRenderer.invoke('queue:restore'),
  clearPendingQueue:   ()          => ipcRenderer.invoke('queue:clearPending'),
  getQueue:            ()          => ipcRenderer.invoke('queue:get'),
  getHistory:         ()           => ipcRenderer.invoke('queue:history'),

  // Queue algorithm
  algoGet:            ()           => ipcRenderer.invoke('queue:algo:get'),
  algoSetPreset:      (p)          => ipcRenderer.invoke('queue:algo:setPreset', p),
  algoSetCooldown:    (sec)        => ipcRenderer.invoke('queue:algo:setCooldown', sec),
  algoValidate:       (name)       => ipcRenderer.invoke('queue:algo:validate', name),
  onQueueReordered:   (cb)         => ipcRenderer.on('queue-reordered', cb),
  setPinned:          (id, pinned) => ipcRenderer.invoke('queue:setPinned', id, pinned),
  getCurrentSong: ()              => ipcRenderer.invoke('queue:current'),
  addToQueue:     (sid, name)     => ipcRenderer.invoke('queue:add', sid, name),
  removeFromQueue:(id)            => ipcRenderer.invoke('queue:remove', id),
  skipEntry:      (id)            => ipcRenderer.invoke('queue:skip', id),
  moveEntry:      (id, dir)       => ipcRenderer.invoke('queue:move', id, dir),
  moveToPosition: (id, idx)       => ipcRenderer.invoke('queue:moveto', id, idx),
  nextSong:       ()              => ipcRenderer.invoke('queue:next'),

  // Library
  openFolderDialog: ()            => ipcRenderer.invoke('library:openDialog'),
  scanLibrary:    (dir)           => ipcRenderer.invoke('library:scan', dir),
  getStats:       ()              => ipcRenderer.invoke('library:stats'),
  onScanProgress: (cb)            => ipcRenderer.on('scan:progress', (_, d) => cb(d)),

  // Settings
  getSettings:    ()              => ipcRenderer.invoke('settings:get'),
  setSetting:     (k, v)          => ipcRenderer.invoke('settings:set', k, v),

  // Song sources (local library / YouTube)
  sourcesGet:     ()              => ipcRenderer.invoke('sources:get'),
  sourcesSet:     (patch)         => ipcRenderer.invoke('sources:set', patch),

  // Player window
  openPlayer:     ()              => ipcRenderer.invoke('player:open'),

  // Singer request server
  requestsStart:  ()              => ipcRenderer.invoke('requests:start'),
  requestsStop:   ()              => ipcRenderer.invoke('requests:stop'),
  requestsStatus: ()              => ipcRenderer.invoke('requests:status'),
  onMobileRequest:(cb)            => ipcRenderer.on('mobile-request-added', (_, name) => cb(name)),

  // Venue online/offline
  venueGetOnline: ()              => ipcRenderer.invoke('venue:getOnline'),
  venueSetOnline: (online)        => ipcRenderer.invoke('venue:setOnline', online),

  // Wireless network player
  networkStart:    ()             => ipcRenderer.invoke('network:start'),
  networkStop:     ()             => ipcRenderer.invoke('network:stop'),
  networkSetAudio: (v)            => ipcRenderer.invoke('network:setAudio', v),
  networkStatus:   ()             => ipcRenderer.invoke('network:status'),

  // Broadcast (main↔popout sync)
  broadcast:      (msg)           => ipcRenderer.send('broadcast', msg),
  onBroadcast:    (cb)            => ipcRenderer.on('broadcast', (_, msg) => cb(msg)),

  // Read a local file by path — returns Uint8Array (avoids URL encoding issues)
  readFile: (filePath) => ipcRenderer.invoke('read:file', filePath),

  // Background music
  bgmusicList: (dir) => ipcRenderer.invoke('bgmusic:list', dir),

  // Dialogs
  openImageDialog: () => ipcRenderer.invoke('dialog:openImage'),
  openVideoDialog: () => ipcRenderer.invoke('dialog:openVideo'),
  openCookiesFileDialog: () => ipcRenderer.invoke('dialog:openCookiesFile'),

  // Spotify native app — mute/unmute via Windows audio session API + SMTC track query
  spotifyMute:       () => ipcRenderer.invoke('spotify:mute'),
  spotifyUnmute:     () => ipcRenderer.invoke('spotify:unmute'),
  spotifyNowPlaying: () => ipcRenderer.invoke('spotify:now-playing'),

  potStatus: () => ipcRenderer.invoke('pot:status'),

  // Software update
  updateGetVersion: ()             => ipcRenderer.invoke('update:getVersion'),
  updateCheck:    ()               => ipcRenderer.invoke('update:check'),
  updateDownload: (url, name)      => ipcRenderer.invoke('update:download', url, name),
  updateInstall:  (filePath)       => ipcRenderer.invoke('update:install', filePath),
  onUpdateProgress: (cb)           => ipcRenderer.on('update:progress', (_, pct) => cb(pct)),
  platform: process.platform,

  // YouTube
  youtubeEnsure:  ()        => ipcRenderer.invoke('youtube:ensure'),
  youtubeSearch:  (q)       => ipcRenderer.invoke('youtube:search', q),
  youtubeStream:  (id)      => ipcRenderer.invoke('youtube:stream', id),
  onYtProgress:   (cb)      => ipcRenderer.on('youtube:progress', (_, p) => cb(p)),

  // Relay — remote song requests from internet
  relayStatus:    ()        => ipcRenderer.invoke('relay:status'),
  relayGetConfig: ()        => ipcRenderer.invoke('relay:getConfig'),
  relaySetConfig: (cfg)     => ipcRenderer.invoke('relay:setConfig', cfg),
  relayQrCode:    (url)     => ipcRenderer.invoke('relay:qrcode', url),
  onRelayRequest: (cb)      => ipcRenderer.on('relay-request',  (_, r) => cb(r)),
  onRelayStatus:  (cb)      => ipcRenderer.on('relay-status',   (_, s) => cb(s)),
});
