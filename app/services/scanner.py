import os
import zipfile
from pathlib import Path
from typing import Generator, Optional
from sqlalchemy.orm import Session

from app.config import (
    MUSIC_DIR, SUPPORTED_CDG, SUPPORTED_VIDEO, SUPPORTED_KAR, SUPPORTED_ZIP
)
from app.models import Song
from app.services.metadata import (
    build_song_meta, find_companion_audio, get_zip_karaoke_pairs
)

ScanResult = dict

_scan_progress: dict = {"running": False, "total": 0, "done": 0, "added": 0, "errors": []}


def get_scan_progress() -> dict:
    return dict(_scan_progress)


def _iter_files(root: str) -> Generator[str, None, None]:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            yield os.path.join(dirpath, name)


def _song_exists(db: Session, file_path: str) -> bool:
    return db.query(Song).filter(Song.file_path == file_path).first() is not None


def _add_song(db: Session, file_path: str, audio_path: Optional[str], file_format: str) -> Optional[Song]:
    if _song_exists(db, file_path):
        return None
    try:
        meta = build_song_meta(file_path, audio_path, file_format)
        song = Song(
            file_path=file_path,
            audio_path=audio_path,
            file_format=file_format,
            title=meta["title"],
            artist=meta["artist"],
            duration=meta["duration"],
            genre=meta.get("genre", ""),
            year=meta.get("year"),
            decade=meta.get("decade", ""),
            bpm=meta.get("bpm"),
            file_size=meta.get("file_size", 0),
        )
        db.add(song)
        db.commit()
        db.refresh(song)
        return song
    except Exception as e:
        db.rollback()
        _scan_progress["errors"].append(str(e))
        return None


def _process_cdg(db: Session, cdg_path: str) -> Optional[Song]:
    audio_path = find_companion_audio(cdg_path)
    if audio_path is None:
        return None
    return _add_song(db, cdg_path, audio_path, "cdg")


def _process_video(db: Session, path: str, fmt: str) -> Optional[Song]:
    return _add_song(db, path, None, fmt)


def _process_kar(db: Session, path: str) -> Optional[Song]:
    return _add_song(db, path, None, "kar")


def _process_zip(db: Session, zip_path: str) -> int:
    added = 0
    pairs = get_zip_karaoke_pairs(zip_path)
    for pair in pairs:
        virtual_cdg = f"{zip_path}::{pair['cdg']}"
        virtual_audio = f"{zip_path}::{pair['audio']}"
        if not _song_exists(db, virtual_cdg):
            song = _add_song(db, virtual_cdg, virtual_audio, "zip-cdg")
            if song:
                added += 1
    return added


def scan_library(db: Session, music_dir: str = MUSIC_DIR) -> dict:
    global _scan_progress
    _scan_progress = {"running": True, "total": 0, "done": 0, "added": 0, "errors": []}

    if not os.path.isdir(music_dir):
        _scan_progress["running"] = False
        _scan_progress["errors"].append(f"Music directory not found: {music_dir}")
        return _scan_progress

    # Count files first
    all_files = list(_iter_files(music_dir))
    _scan_progress["total"] = len(all_files)

    seen_audio: set[str] = set()

    for path in all_files:
        ext = Path(path).suffix.lower()
        _scan_progress["done"] += 1

        if ext in SUPPORTED_CDG:
            song = _process_cdg(db, path)
            if song:
                _scan_progress["added"] += 1
                if song.audio_path:
                    seen_audio.add(song.audio_path)

        elif ext in SUPPORTED_VIDEO:
            song = _process_video(db, path, ext.lstrip("."))
            if song:
                _scan_progress["added"] += 1

        elif ext in SUPPORTED_KAR:
            song = _process_kar(db, path)
            if song:
                _scan_progress["added"] += 1

        elif ext in SUPPORTED_ZIP:
            n = _process_zip(db, path)
            _scan_progress["added"] += n

    # Mark missing files as inactive
    all_songs = db.query(Song).all()
    for song in all_songs:
        if "::" in song.file_path:
            zip_path, _ = song.file_path.split("::", 1)
            active = os.path.isfile(zip_path)
        else:
            active = os.path.isfile(song.file_path)
        if song.is_active != active:
            song.is_active = active
    db.commit()

    _scan_progress["running"] = False
    return _scan_progress


def rescan_missing(db: Session) -> int:
    """Mark songs as inactive if their files are gone."""
    songs = db.query(Song).filter(Song.is_active == True).all()
    fixed = 0
    for song in songs:
        if "::" in song.file_path:
            zip_path, _ = song.file_path.split("::", 1)
            exists = os.path.isfile(zip_path)
        else:
            exists = os.path.isfile(song.file_path)
        if not exists:
            song.is_active = False
            fixed += 1
    db.commit()
    return fixed
