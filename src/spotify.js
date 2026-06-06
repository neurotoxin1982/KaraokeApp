/**
 * Spotify OAuth + Web API helper (runs in main process).
 * Uses PKCE flow – no client secret needed.
 *
 * Redirect URI: http://127.0.0.1:8888/callback
 *   → Use 127.0.0.1, NOT "localhost" — Spotify accepts it without the
 *     "not secure" warning. A real temporary HTTP server is started during
 *     the OAuth flow so Spotify can actually complete the redirect.
 */

const { BrowserWindow } = require('electron');
const crypto = require('crypto');
const https  = require('https');
const http   = require('http');

const REDIRECT_URI = 'http://127.0.0.1:8888/callback';
const SCOPES = [
  'streaming',
  'user-read-email',
  'user-read-private',
  'user-modify-playback-state',
  'user-read-playback-state',
  'playlist-read-private',
  'playlist-read-collaborative',
].join(' ');

let _clientId = null;
let _tokens   = { access: null, refresh: null, expiry: 0 };
let _authWin  = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

function _pkce() {
  const verifier  = crypto.randomBytes(32).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

function _httpsPost(url, body) {
  return new Promise((resolve, reject) => {
    const u    = new URL(url);
    const data = Buffer.from(body);
    const req  = https.request({
      hostname: u.hostname,
      path: u.pathname + u.search,
      method: 'POST',
      headers: {
        'Content-Type':   'application/x-www-form-urlencoded',
        'Content-Length': data.length,
      },
    }, res => {
      let raw = '';
      res.on('data', c => raw += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(raw) }); }
        catch { resolve({ status: res.statusCode, body: raw }); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

/** Start a one-shot HTTP server on 127.0.0.1:8888 that resolves with the
 *  callback URL when Spotify redirects to it. */
function _startCallbackServer(state) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      // Only handle our callback path
      if (!req.url.startsWith('/callback')) {
        res.writeHead(404); res.end(); return;
      }

      const u   = new URL(req.url, 'http://127.0.0.1:8888');
      const code = u.searchParams.get('code');
      const err  = u.searchParams.get('error');
      const st   = u.searchParams.get('state');

      // Send a friendly closing page
      const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
        <style>body{font-family:sans-serif;display:flex;align-items:center;
          justify-content:center;height:100vh;margin:0;background:#111827;color:#f3f4f6}
          .box{text-align:center;padding:40px;border-radius:16px;background:#1f2937}
          h2{color:#${err ? 'ef4444' : '22c55e'};margin:0 0 12px}</style></head>
        <body><div class="box">
          <h2>${err ? '✗ Login failed' : '✓ Connected!'}</h2>
          <p>${err ? err : 'You can close this window and return to Karaoke.'}</p>
          <script>setTimeout(()=>window.close(),1500)</script>
        </div></body></html>`;
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(html);

      server.close();

      if (err || !code)   return reject(new Error(err || 'No code returned'));
      if (st !== state)   return reject(new Error('State mismatch'));
      resolve(code);
    });

    server.on('error', reject);
    server.listen(8888, '127.0.0.1');
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

function configure(clientId, refreshToken) {
  _clientId = clientId || null;
  if (refreshToken) _tokens.refresh = refreshToken;
}

function isAuthenticated() {
  return !!( (_tokens.access && Date.now() < _tokens.expiry - 10_000) || _tokens.refresh );
}

function disconnect() {
  _tokens = { access: null, refresh: null, expiry: 0 };
}

async function authorize() {
  if (!_clientId) throw new Error('No Client ID configured');

  const { verifier, challenge } = _pkce();
  const state  = crypto.randomBytes(8).toString('hex');
  const params = new URLSearchParams({
    client_id:             _clientId,
    response_type:         'code',
    redirect_uri:          REDIRECT_URI,
    scope:                 SCOPES,
    state,
    code_challenge_method: 'S256',
    code_challenge:        challenge,
  });

  // Start the callback server BEFORE opening the browser window
  const codePromise = _startCallbackServer(state);

  if (_authWin && !_authWin.isDestroyed()) _authWin.close();
  _authWin = new BrowserWindow({
    width: 480, height: 680,
    title: 'Connect Spotify',
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  _authWin.loadURL('https://accounts.spotify.com/authorize?' + params);
  _authWin.setMenuBarVisibility(false);
  _authWin.on('closed', () => { _authWin = null; });

  let code;
  try {
    code = await codePromise;
  } catch(e) {
    if (_authWin && !_authWin.isDestroyed()) _authWin.close();
    throw e;
  }
  if (_authWin && !_authWin.isDestroyed()) _authWin.close();
  return _exchange(code, verifier);
}

async function _exchange(code, verifier) {
  const res = await _httpsPost('https://accounts.spotify.com/api/token', new URLSearchParams({
    grant_type:    'authorization_code',
    code,
    redirect_uri:  REDIRECT_URI,
    client_id:     _clientId,
    code_verifier: verifier,
  }).toString());
  if (res.status !== 200) throw new Error('Token exchange failed (' + res.status + '): ' + JSON.stringify(res.body));
  const d = res.body;
  _tokens = { access: d.access_token, refresh: d.refresh_token, expiry: Date.now() + d.expires_in * 1000 };
  return { access: d.access_token, refresh: d.refresh_token };
}

async function getToken() {
  if (_tokens.access && Date.now() < _tokens.expiry - 60_000) return _tokens.access;
  if (!_tokens.refresh) throw new Error('Not authenticated – please connect Spotify');
  const res = await _httpsPost('https://accounts.spotify.com/api/token', new URLSearchParams({
    grant_type:    'refresh_token',
    refresh_token: _tokens.refresh,
    client_id:     _clientId,
  }).toString());
  if (res.status !== 200) {
    // Wipe the bad refresh token so future calls fail fast instead of looping
    _tokens = { access: null, refresh: null, expiry: 0 };
    const err = new Error('Token refresh failed – please reconnect Spotify');
    err.code = 'TOKEN_REFRESH_FAILED';
    throw err;
  }
  const d = res.body;
  _tokens.access = d.access_token;
  _tokens.expiry = Date.now() + d.expires_in * 1000;
  if (d.refresh_token) _tokens.refresh = d.refresh_token;
  return _tokens.access;
}

module.exports = { configure, isAuthenticated, disconnect, authorize, getToken };
