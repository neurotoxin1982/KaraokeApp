const fs   = require('fs');
const path = require('path');

const CDG_EXT   = new Set(['.cdg']);
const AUDIO_EXT = new Set(['.mp3', '.ogg', '.m4a', '.flac', '.wav']);
const VIDEO_EXT = new Set(['.mp4', '.avi', '.webm', '.mkv', '.m4v', '.mpeg', '.mpg']);
const KAR_EXT   = new Set(['.kar', '.mid']);

function walkDir(dir) {
  const results = [];
  try {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) results.push(...walkDir(full));
      else results.push(full);
    }
  } catch (_) {}
  return results;
}

function parseName(stem) {
  const m = stem.match(/^(.+?)\s*[-–]\s*(.+)$/);
  return m ? { artist: m[1].trim(), title: m[2].trim() } : { artist: 'Unknown', title: stem.trim() };
}

function guessDecade(year) {
  if (!year) return '';
  return `${Math.floor(year / 10) * 10}s`;
}

async function getAudioMeta(filePath) {
  try {
    const mm = require('music-metadata');
    const meta = await mm.parseFile(filePath, { duration: true, skipCovers: true });
    return {
      duration: meta.format.duration || 0,
      title:    meta.common.title    || null,
      artist:   meta.common.artist   || null,
      genre:    (meta.common.genre   || []).join(', ') || '',
      year:     meta.common.year     || null,
    };
  } catch (_) {
    return { duration: 0, title: null, artist: null, genre: '', year: null };
  }
}

async function scan(dir, db, onProgress) {
  const allFiles = walkDir(dir);
  const total    = allFiles.length;
  let done = 0, added = 0;

  // Build lookup maps
  const cdgMap   = new Map();
  const audioMap = new Map();

  for (const f of allFiles) {
    const ext  = path.extname(f).toLowerCase();
    const stem = f.slice(0, -ext.length).toLowerCase();
    if (CDG_EXT.has(ext))   cdgMap.set(stem, f);
    if (AUDIO_EXT.has(ext)) audioMap.set(stem, f);
  }

  // CDG + MP3 pairs
  for (const [stem, cdgPath] of cdgMap) {
    const audioPath = audioMap.get(stem);
    if (!audioPath) { done++; onProgress?.({ done, total, added }); continue; }

    const base = path.basename(cdgPath, '.cdg');
    const nameMeta = parseName(base);
    const audioMeta = await getAudioMeta(audioPath);

    const result = db.importSong({
      title:      audioMeta.title  || nameMeta.title,
      artist:     audioMeta.artist || nameMeta.artist,
      file_path:  cdgPath,
      audio_path: audioPath,
      file_format:'cdg',
      duration:   audioMeta.duration,
      genre:      audioMeta.genre,
      year:       audioMeta.year,
      decade:     guessDecade(audioMeta.year),
    });
    if (result.status === 'created') added++;
    done++;
    onProgress?.({ done, total, added });
  }

  // Video files
  for (const f of allFiles) {
    const ext = path.extname(f).toLowerCase();
    if (!VIDEO_EXT.has(ext)) { continue; }
    const base = path.basename(f, ext);
    const { artist, title } = parseName(base);
    const result = db.importSong({ title, artist, file_path: f, file_format: ext.slice(1) });
    if (result.status === 'created') added++;
    done++;
    onProgress?.({ done, total, added });
  }

  // KAR files
  for (const f of allFiles) {
    const ext = path.extname(f).toLowerCase();
    if (!KAR_EXT.has(ext)) continue;
    const base = path.basename(f, ext);
    const { artist, title } = parseName(base);
    const result = db.importSong({ title, artist, file_path: f, file_format: 'kar' });
    if (result.status === 'created') added++;
  }

  // Standalone audio files (MP3 etc. with no CDG partner)
  for (const [stem, audioPath] of audioMap) {
    if (cdgMap.has(stem)) continue; // already imported as CDG pair
    const ext  = path.extname(audioPath).toLowerCase();
    const base = path.basename(audioPath, ext);
    const nameMeta  = parseName(base);
    const audioMeta = await getAudioMeta(audioPath);
    const result = db.importSong({
      title:       audioMeta.title  || nameMeta.title,
      artist:      audioMeta.artist || nameMeta.artist,
      file_path:   audioPath,
      audio_path:  audioPath,
      file_format: ext.slice(1),
      duration:    audioMeta.duration,
      genre:       audioMeta.genre,
      year:        audioMeta.year,
      decade:      guessDecade(audioMeta.year),
    });
    if (result.status === 'created') added++;
    done++;
    onProgress?.({ done, total, added });
  }

  onProgress?.({ done: total, total, added, done: true });
  return { total, added };
}

module.exports = { scan };
