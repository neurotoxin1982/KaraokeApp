const WebSocket = require('ws');

let _ws            = null;
let _reconnectTimer = null;
let _cfg           = null;
let _status        = { connected: false, url: null };
let _onRequest     = null;
let _onStatus      = null;
let _rejected      = false; // true once the server has told us this venueId belongs to someone else

function init(cfg, onRequest, onStatus) {
  _cfg       = { ...cfg };
  _onRequest = onRequest;
  _onStatus  = onStatus;
  if (_cfg.enabled) _connect();
}

function _connect() {
  if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
  if (_ws) { try { _ws.terminate(); } catch {} _ws = null; }
  if (!_cfg?.serverUrl) return;
  _rejected = false;

  try {
    _ws = new WebSocket(_cfg.serverUrl);
  } catch {
    _scheduleReconnect(); return;
  }

  _ws.once('open', () => {
    _ws.send(JSON.stringify({
      type:           'register',
      venueId:        _cfg.venueId,
      venueName:      _cfg.venueName || _cfg.venueId,
      venueSecret:    _cfg.venueSecret,
      sourcesEnabled: _cfg.sourcesEnabled || { local: true, youtube: true },
    }));
  });

  _ws.on('message', raw => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return; }
    if (msg.type === 'registered') {
      _status = { connected: true, url: msg.url, venueId: _cfg.venueId };
      _onStatus?.(_status);
    } else if (msg.type === 'register-rejected') {
      // Someone else already holds this venueId with a different secret — retrying
      // would just fail the same way forever, so stop and surface it instead.
      _rejected = true;
      _status = { connected: false, url: null, error: msg.reason || 'register-rejected' };
      _onStatus?.(_status);
      try { _ws.close(); } catch {}
    } else {
      _onRequest?.(msg);
    }
  });

  // 'error' fires before 'close' — suppress so Node doesn't crash, let 'close' handle reconnect
  _ws.on('error', () => {});

  _ws.on('close', () => {
    _status = _rejected ? _status : { connected: false, url: null };
    _onStatus?.(_status);
    if (_cfg?.enabled && !_rejected) _scheduleReconnect();
  });
}

function _scheduleReconnect() {
  if (_reconnectTimer) return;
  _reconnectTimer = setTimeout(() => { _reconnectTimer = null; _connect(); }, 5000);
}

function setEnabled(enabled) {
  if (!_cfg) return;
  _cfg.enabled = enabled;
  if (enabled) {
    _connect();
  } else {
    if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
    if (_ws) { _ws.close(); _ws = null; }
    _status = { connected: false, url: null };
    _onStatus?.(_status);
  }
}

function updateConfig(patch) {
  if (!_cfg) return;
  Object.assign(_cfg, patch);
  // Reconnect if running so new settings take effect
  if (_cfg.enabled) _connect();
}

function getStatus() { return _status; }

function send(msg) {
  if (_ws && _ws.readyState === 1 /* OPEN */) {
    _ws.send(JSON.stringify(msg));
  }
}

module.exports = { init, setEnabled, updateConfig, getStatus, send };
