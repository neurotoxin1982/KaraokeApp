import threading
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Song
from app.services.scanner import scan_library, get_scan_progress, rescan_missing

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _run_scan_thread(music_dir: str):
    db = SessionLocal()
    try:
        scan_library(db, music_dir)
    finally:
        db.close()


@router.post("/api/library/scan")
def start_scan(music_dir: str = "", db: Session = Depends(get_db)):
    from app.config import MUSIC_DIR
    progress = get_scan_progress()
    if progress.get("running"):
        return {"status": "already_running"}
    dir_to_scan = music_dir or MUSIC_DIR
    t = threading.Thread(target=_run_scan_thread, args=(dir_to_scan,), daemon=True)
    t.start()
    return {"status": "started", "dir": dir_to_scan}


@router.get("/api/library/scan/progress")
def scan_progress():
    return get_scan_progress()


@router.post("/api/library/rescan-missing")
def rescan(db: Session = Depends(get_db)):
    fixed = rescan_missing(db)
    return {"fixed": fixed}


@router.get("/api/library/stats")
def library_stats(db: Session = Depends(get_db)):
    total = db.query(Song).filter(Song.is_active == True).count()
    by_format = {}
    for row in db.query(Song.file_format, Song.file_format).filter(Song.is_active == True).all():
        by_format[row[0]] = by_format.get(row[0], 0) + 1
    return {"total": total, "by_format": by_format}
