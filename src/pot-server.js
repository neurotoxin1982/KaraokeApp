// Runs a local PO Token provider (https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
// so yt-dlp can satisfy YouTube's "Sign in to confirm you're not a bot" check without
// cookies or a YouTube login. The server generates tokens locally; it never touches
// the user's account. Vendored server + yt-dlp plugin live under src/vendor/pot-server.
const path = require('path');
const fs   = require('fs');
const http = require('http');
const { app } = require('electron');
const { spawn, execFile } = require('child_process');

const VENDOR_DIR = path.join(__dirname, 'vendor', 'pot-server');
const PLUGIN_SRC = path.join(VENDOR_DIR, 'plugin', 'yt_dlp_plugins', 'extractor');
const PORT = 4416;

let _serverProcess = null;
let _ensurePromise = null;
let _lastStatus = { ok: false, reason: 'not-started' };

function pluginDestDir() {
  return path.join(app.getPath('userData'), 'yt-dlp-plugins', 'bgutil-pot-provider', 'yt_dlp_plugins', 'extractor');
}

async function _installPlugin() {
  const dest = pluginDestDir();
  await fs.promises.mkdir(dest, { recursive: true });
  const files = await fs.promises.readdir(PLUGIN_SRC);
  for (const f of files) {
    await fs.promises.copyFile(path.join(PLUGIN_SRC, f), path.join(dest, f));
  }
}

function _ping(timeout = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${PORT}/ping`, { timeout }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function _npmCi() {
  return new Promise((resolve) => {
    const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
    execFile(npmCmd, ['ci', '--omit=dev', '--no-audit', '--no-fund'], { cwd: VENDOR_DIR, timeout: 180000 }, (err) => {
      if (err) console.error('[pot-server] npm ci failed:', err.message);
      resolve(!err);
    });
  });
}

function _startServer() {
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn(process.platform === 'win32' ? 'node.exe' : 'node', ['build/main.js'], {
        cwd: VENDOR_DIR,
        windowsHide: true,
        stdio: 'ignore',
      });
    } catch {
      return resolve({ ok: false, reason: 'node-not-found' });
    }
    proc.on('error', () => resolve({ ok: false, reason: 'node-not-found' }));
    proc.on('exit', () => { if (_serverProcess === proc) _serverProcess = null; });
    _serverProcess = proc;

    let attempts = 0;
    const check = async () => {
      if (await _ping()) return resolve({ ok: true, reason: 'started' });
      if (++attempts >= 10) return resolve({ ok: false, reason: 'start-timeout' });
      setTimeout(check, 500);
    };
    check();
  });
}

async function _doEnsure() {
  try {
    await _installPlugin();
  } catch (e) {
    console.error('[pot-server] failed to install yt-dlp plugin:', e.message);
    return { ok: false, reason: 'plugin-install-failed' };
  }

  if (await _ping()) return { ok: true, reason: 'already-running' };

  if (!fs.existsSync(path.join(VENDOR_DIR, 'node_modules'))) {
    if (!await _npmCi()) return { ok: false, reason: 'npm-install-failed' };
  }

  return _startServer();
}

// Best-effort: failures here just mean yt-dlp falls back to its existing
// cookie/client-arg behavior, so callers should not treat rejection as fatal.
async function ensure() {
  if (_ensurePromise) return _ensurePromise;
  _ensurePromise = _doEnsure().then((status) => { _lastStatus = status; return status; });
  return _ensurePromise;
}

function status() {
  return _lastStatus;
}

function stop() {
  if (_serverProcess && !_serverProcess.killed) _serverProcess.kill();
  _serverProcess = null;
}

module.exports = { ensure, stop, status };
