// Emergency rollback switch for the KaraokeDisplay rendering migration.
//
// Backed by a flag FILE, not a settings DB row, on purpose: it must be
// readable (and toggle-able) even if the new renderer is broken badly enough
// that the app's own Settings UI can't be reached, and even before the DB is
// initialized. Presence of the file = legacy (pre-KaraokeDisplay) renderer;
// absence = the new shared-component renderer.
//
// Toggled in-app via Ctrl+Shift+L in either window (see main.js), which is
// caught at the Electron webContents level (before-input-event), so it keeps
// working even if the page's own JavaScript has crashed.
const fs = require('fs');
const path = require('path');
const { app } = require('electron');

function _flagPath() {
  return path.join(app.getPath('userData'), 'legacy-renderer.flag');
}

function isLegacyMode() {
  try { return fs.existsSync(_flagPath()); } catch { return false; }
}

function setLegacyMode(on) {
  const p = _flagPath();
  try {
    if (on) {
      fs.writeFileSync(
        p,
        'This file switches Karaoke Manager back to the pre-KaraokeDisplay renderer.\n' +
        'Delete it, or press Ctrl+Shift+L again in the app, to return to the normal renderer.\n'
      );
    } else if (fs.existsSync(p)) {
      fs.unlinkSync(p);
    }
  } catch {}
}

module.exports = { isLegacyMode, setLegacyMode };
