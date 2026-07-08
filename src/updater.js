const path = require('path');
const fs   = require('fs');
const { app, shell } = require('electron');

const REPO = 'neurotoxin1982/KaraokeApp';

function _parseVersion(v) {
  return (v || '').replace(/^v/, '').split('.').map(n => parseInt(n, 10) || 0);
}

// True if `remote` is a strictly newer semver than `local`.
function _isNewer(remote, local) {
  const r = _parseVersion(remote), l = _parseVersion(local);
  for (let i = 0; i < Math.max(r.length, l.length); i++) {
    const a = r[i] || 0, b = l[i] || 0;
    if (a !== b) return a > b;
  }
  return false;
}

// Picks the right release asset for this machine. macOS ships separate
// Intel/Apple Silicon dmgs (see build.mac in package.json); Windows has one exe.
function _pickAsset(assets) {
  if (process.platform === 'win32') {
    return assets.find(a => a.name.endsWith('.exe') && !a.name.endsWith('.blockmap'));
  }
  if (process.platform === 'darwin') {
    const wantArm = process.arch === 'arm64';
    return assets.find(a => a.name.endsWith('.dmg') && a.name.includes('arm64') === wantArm);
  }
  return null;
}

async function checkForUpdate() {
  const resp = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { 'User-Agent': 'karaoke-manager-updater' },
  });
  if (!resp.ok) throw new Error(`GitHub API error: HTTP ${resp.status}`);
  const release = await resp.json();

  const currentVersion = app.getVersion();
  const latestVersion  = (release.tag_name || '').replace(/^v/, '');
  const available      = _isNewer(latestVersion, currentVersion);
  const asset          = available ? _pickAsset(release.assets || []) : null;

  return {
    available,
    currentVersion,
    latestVersion,
    releaseNotes: release.body || '',
    assetName:    asset?.name || null,
    downloadUrl:  asset?.browser_download_url || null,
  };
}

// macOS downloads land in ~/Downloads (so "reveal in Finder" makes sense);
// Windows lands in a temp dir since it's launched directly, never shown to the user.
async function downloadUpdate(downloadUrl, assetName, onProgress) {
  const dir  = process.platform === 'darwin' ? app.getPath('downloads') : app.getPath('temp');
  const dest = path.join(dir, assetName);

  const resp = await fetch(downloadUrl);
  if (!resp.ok) throw new Error(`Download failed: HTTP ${resp.status}`);

  const total  = parseInt(resp.headers.get('content-length') || '0', 10);
  const reader = resp.body.getReader();
  const chunks = [];
  let loaded   = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    if (total) onProgress?.(Math.round(loaded / total * 100));
  }

  await fs.promises.writeFile(dest, Buffer.concat(chunks.map(c => Buffer.from(c))));
  return dest;
}

// Windows: launch the installer (it detects and closes the running app itself),
// then quit so it can replace files that are currently locked by this process.
// macOS: there's no signed app yet, so silently swapping the bundle isn't safe —
// reveal the downloaded dmg and let the user drag it to Applications as usual.
async function installUpdate(filePath) {
  if (process.platform === 'win32') {
    await shell.openPath(filePath);
    app.quit();
  } else {
    shell.showItemInFolder(filePath);
  }
}

module.exports = { checkForUpdate, downloadUpdate, installUpdate };
