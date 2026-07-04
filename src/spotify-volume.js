const { spawn } = require('child_process');
const path       = require('path');

const SCRIPT = path.join(__dirname, 'spotify-mute.ps1');

let _ps           = null;
let _ready        = false;
let _queue        = [];   // commands buffered while PS is still compiling
let _trackResolve = null; // pending resolve for getCurrentTrack()

function _start() {
  if (_ps && !_ps.killed) return;
  _ready = false;

  _ps = spawn('powershell.exe', [
    '-NonInteractive', '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', SCRIPT,
  ], { windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'] });

  _ps.unref(); // don't block Electron from quitting

  let outBuf = '';
  _ps.stdout.on('data', chunk => {
    outBuf += chunk.toString();
    const lines = outBuf.split('\n');
    outBuf = lines.pop(); // keep any partial line
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (!_ready && trimmed === 'READY') {
        _ready = true;
        for (const cmd of _queue) _write(cmd);
        _queue = [];
      } else if (trimmed.startsWith('{') && _trackResolve) {
        const resolve = _trackResolve;
        _trackResolve = null;
        try { resolve(JSON.parse(trimmed)); }
        catch { resolve(null); }
      }
    }
  });

  _ps.on('exit',  () => { _ps = null; _ready = false; });
  _ps.on('error', () => { _ps = null; _ready = false; });
}

function _write(cmd) {
  try {
    if (_ps && !_ps.killed && _ps.stdin && !_ps.stdin.destroyed)
      _ps.stdin.write(cmd + '\n');
  } catch {}
}

function _send(cmd) {
  if (!_ps || _ps.killed) _start(); // auto-restart if dead
  if (_ready) {
    _write(cmd);
  } else {
    // Keep only the last mute/unmute per target — earlier ones are superseded
    _queue = _queue.filter(c => c.split(' ')[1] !== cmd.split(' ')[1]);
    _queue.push(cmd);
  }
}

function getCurrentTrack() {
  return new Promise((resolve) => {
    if (!_ready || !_ps || _ps.killed) { resolve(null); return; }
    _trackResolve = resolve;
    _write('get-track');
    // Timeout after 3 s in case PS hangs
    setTimeout(() => {
      if (_trackResolve === resolve) { _trackResolve = null; resolve(null); }
    }, 3000);
  });
}

// Pre-start immediately so the C# assembly is compiled before the first song
_start();

module.exports = {
  /** Mute a process by name (default: Spotify) */
  mute:   (proc = 'Spotify') => _send(`mute ${proc}`),
  /** Unmute a process by name (default: Spotify) */
  unmute: (proc = 'Spotify') => _send(`unmute ${proc}`),
  /** Query SMTC for the current Spotify track → { title, artist } or null */
  getCurrentTrack,
};
