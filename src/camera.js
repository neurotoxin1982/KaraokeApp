const { spawn }  = require('child_process');
const http        = require('http');
const ffmpegBin   = require('ffmpeg-static');

let _proc    = null;
let _server  = null;
let _clients = [];
let _port    = null;
let _buf     = Buffer.alloc(0);

function _pushFrame(frame) {
  const hdr = `--mjpegframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`;
  _clients = _clients.filter(c => {
    try { c.write(hdr); c.write(frame); c.write('\r\n'); return true; }
    catch (_) { return false; }
  });
}

const MAX_BUF = 4 * 1024 * 1024; // 4 MB — discard if a complete JPEG never arrives

function _extractFrames(chunk) {
  _buf = Buffer.concat([_buf, chunk]);

  // Guard against unbounded growth from a malformed/stalled stream
  if (_buf.length > MAX_BUF) { _buf = Buffer.alloc(0); return; }

  while (true) {
    let soi = -1;
    for (let i = 0; i < _buf.length - 1; i++) {
      if (_buf[i] === 0xFF && _buf[i + 1] === 0xD8) { soi = i; break; }
    }
    if (soi < 0) { _buf = Buffer.alloc(0); break; }
    if (soi > 0) _buf = _buf.slice(soi);

    let eoi = -1;
    for (let i = 2; i < _buf.length - 1; i++) {
      if (_buf[i] === 0xFF && _buf[i + 1] === 0xD9) { eoi = i + 2; break; }
    }
    if (eoi < 0) break;

    _pushFrame(_buf.slice(0, eoi));
    _buf = _buf.slice(eoi);
  }
}

async function startCamera(rtspUrl) {
  stopCamera();
  _clients = [];
  _buf = Buffer.alloc(0);

  _server = http.createServer((req, res) => {
    res.writeHead(200, {
      'Content-Type': 'multipart/x-mixed-replace;boundary=mjpegframe',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Pragma': 'no-cache',
      'Access-Control-Allow-Origin': '*',
    });
    _clients.push(res);
    req.on('close', () => { _clients = _clients.filter(c => c !== res); });
  });

  await new Promise((resolve, reject) => {
    _server.listen(0, '127.0.0.1', resolve);
    _server.on('error', reject);
  });
  _port = _server.address().port;

  _proc = spawn(ffmpegBin, [
    '-rtsp_transport', 'tcp',
    '-i', rtspUrl,
    '-f', 'mjpeg',
    '-r', '12',
    '-vf', 'scale=320:-1',
    'pipe:1',
  ], { stdio: ['ignore', 'pipe', 'ignore'] });

  _proc.stdout.on('data', _extractFrames);
  _proc.on('error', () => {});

  return _port;
}

function stopCamera() {
  if (_proc) { try { _proc.kill('SIGKILL'); } catch (_) {} _proc = null; }
  if (_server) {
    for (const c of _clients) { try { c.end(); } catch (_) {} }
    _clients = [];
    _server.close();
    _server = null;
    _port = null;
  }
  _buf = Buffer.alloc(0);
}

module.exports = { startCamera, stopCamera };
