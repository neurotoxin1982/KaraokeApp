import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Setting, Song, Singer, PlayHistory

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str):
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    music_dir = get_setting(db, "music_dir", "/music")
    total_songs = db.query(Song).filter(Song.is_active == True).count()
    total_singers = db.query(Singer).count()
    total_plays = db.query(PlayHistory).count()

    top_songs = (
        db.query(Song)
        .filter(Song.play_count > 0)
        .order_by(Song.play_count.desc())
        .limit(10)
        .all()
    )
    top_singers = (
        db.query(Singer)
        .filter(Singer.total_songs_sung > 0)
        .order_by(Singer.total_songs_sung.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "music_dir": music_dir,
        "total_songs": total_songs,
        "total_singers": total_singers,
        "total_plays": total_plays,
        "top_songs": top_songs,
        "top_singers": top_singers,
    })


@router.post("/api/settings")
def update_settings(music_dir: str = "", db: Session = Depends(get_db)):
    if music_dir:
        set_setting(db, "music_dir", music_dir)
    return {"ok": True}


@router.get("/api/fs/browse")
def browse_filesystem(path: str = "/"):
    path = os.path.normpath(path) or "/"
    if not os.path.isdir(path):
        path = "/"
    try:
        entries = sorted(
            [e for e in os.scandir(path) if e.is_dir(follow_symlinks=True)],
            key=lambda e: e.name.lower(),
        )
        dirs = [{"name": e.name, "path": e.path} for e in entries]
    except PermissionError:
        dirs = []
    parent = str(os.path.dirname(path)) if path != "/" else None
    return {"current": path, "parent": parent, "dirs": dirs}
