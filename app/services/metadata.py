import os
import re
import zipfile
from pathlib import Path
from typing import Optional

try:
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import FLAC
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False


def parse_filename(stem: str) -> tuple[str, str]:
    """Extract artist and title from filename stem using common patterns."""
    patterns = [
        r"^(.+?)\s*-\s*(.+)$",
        r"^(.+?)\s*_\s*(.+)$",
    ]
    for pattern in patterns:
        m = re.match(pattern, stem)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return "Unknown Artist", stem.strip()


def guess_decade(year: Optional[int]) -> str:
    if not year:
        return ""
    decade = (year // 10) * 10
    return f"{decade}s"


def get_audio_duration(path: str) -> float:
    if not MUTAGEN_AVAILABLE:
        return 0.0
    ext = Path(path).suffix.lower()
    try:
        if ext == ".mp3":
            audio = MP3(path)
            return audio.info.length
        elif ext in {".mp4", ".m4a"}:
            audio = MP4(path)
            return audio.info.length
        elif ext == ".ogg":
            audio = OggVorbis(path)
            return audio.info.length
        elif ext == ".flac":
            audio = FLAC(path)
            return audio.info.length
    except Exception:
        pass
    return 0.0


def get_id3_tags(path: str) -> dict:
    if not MUTAGEN_AVAILABLE:
        return {}
    ext = Path(path).suffix.lower()
    tags = {}
    try:
        if ext == ".mp3":
            audio = MP3(path)
            raw = audio.tags
            if raw:
                if "TIT2" in raw:
                    tags["title"] = str(raw["TIT2"])
                if "TPE1" in raw:
                    tags["artist"] = str(raw["TPE1"])
                if "TALB" in raw:
                    tags["album"] = str(raw["TALB"])
                if "TCON" in raw:
                    tags["genre"] = str(raw["TCON"])
                if "TDRC" in raw:
                    try:
                        tags["year"] = int(str(raw["TDRC"])[:4])
                    except (ValueError, TypeError):
                        pass
                if "TBPM" in raw:
                    try:
                        tags["bpm"] = int(str(raw["TBPM"]))
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass
    return tags


def find_companion_audio(cdg_path: str) -> Optional[str]:
    """Find the audio file paired with a CDG file."""
    base = os.path.splitext(cdg_path)[0]
    for ext in [".mp3", ".ogg", ".m4a", ".flac", ".wav"]:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
        candidate = base + ext.upper()
        if os.path.isfile(candidate):
            return candidate
    return None


def get_zip_karaoke_pairs(zip_path: str) -> list[dict]:
    """Return a list of CDG+audio pairs found inside a ZIP."""
    pairs = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            cdg_files = {n for n in names if n.lower().endswith(".cdg")}
            for cdg in cdg_files:
                base = os.path.splitext(cdg)[0]
                audio = None
                for ext in [".mp3", ".ogg", ".m4a", ".flac", ".wav",
                             ".MP3", ".OGG", ".M4A", ".FLAC", ".WAV"]:
                    candidate = base + ext
                    if candidate in names:
                        audio = candidate
                        break
                if audio:
                    pairs.append({"cdg": cdg, "audio": audio})
    except Exception:
        pass
    return pairs


def build_song_meta(file_path: str, audio_path: Optional[str], file_format: str) -> dict:
    """Build a metadata dict for a song entry."""
    stem = Path(file_path).stem
    artist, title = parse_filename(stem)

    duration = 0.0
    tags = {}

    if audio_path:
        duration = get_audio_duration(audio_path)
        tags = get_id3_tags(audio_path)
    elif file_format in {"mp4", "avi", "webm", "mkv", "m4v", "mpeg", "mpg"}:
        duration = get_audio_duration(file_path)

    if tags.get("title"):
        title = tags["title"]
    if tags.get("artist"):
        artist = tags["artist"]

    year = tags.get("year")

    return {
        "title": title,
        "artist": artist,
        "duration": duration,
        "genre": tags.get("genre", ""),
        "year": year,
        "decade": guess_decade(year),
        "bpm": tags.get("bpm"),
        "file_size": os.path.getsize(file_path) if os.path.isfile(file_path) else 0,
    }
