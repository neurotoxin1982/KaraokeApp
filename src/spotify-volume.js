const { spawn } = require('child_process');
const path       = require('path');

const SCRIPT = path.join(__dirname, 'spotify-mute.ps1');

function run(mute) {
  return new Promise((resolve) => {
    const ps = spawn('powershell.exe', [
      '-NonInteractive', '-NoProfile', '-ExecutionPolicy', 'Bypass',
      '-File', SCRIPT,
      mute ? '1' : '0',
    ], { windowsHide: true, stdio: 'ignore', detached: true });
    ps.unref(); // let process survive parent exit (fixes unmute-on-close)
    ps.on('close',  () => resolve());
    ps.on('error',  () => resolve());
  });
}

module.exports = {
  mute:   () => run(true),
  unmute: () => run(false),
};
