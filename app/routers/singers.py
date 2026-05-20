from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Singer, PlayHistory, Song

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/singers", response_class=HTMLResponse)
def singers_page(request: Request, db: Session = Depends(get_db)):
    singers = db.query(Singer).order_by(Singer.name).all()
    return templates.TemplateResponse("singers.html", {
        "request": request,
        "singers": singers,
    })


@router.post("/api/singers")
def create_singer(name: str, db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    existing = db.query(Singer).filter(Singer.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Singer already exists")
    singer = Singer(name=name)
    db.add(singer)
    db.commit()
    db.refresh(singer)
    return {"id": singer.id, "name": singer.name}


@router.put("/api/singers/{singer_id}")
def update_singer(singer_id: int, name: str, notes: str = "", db: Session = Depends(get_db)):
    singer = db.query(Singer).filter(Singer.id == singer_id).first()
    if not singer:
        raise HTTPException(status_code=404, detail="Singer not found")
    singer.name = name.strip()
    singer.notes = notes
    db.commit()
    return {"ok": True}


@router.delete("/api/singers/{singer_id}")
def delete_singer(singer_id: int, db: Session = Depends(get_db)):
    singer = db.query(Singer).filter(Singer.id == singer_id).first()
    if not singer:
        raise HTTPException(status_code=404, detail="Singer not found")
    db.delete(singer)
    db.commit()
    return {"ok": True}


@router.get("/api/singers/{singer_id}/history")
def singer_history(singer_id: int, db: Session = Depends(get_db)):
    history = (
        db.query(PlayHistory)
        .filter(PlayHistory.singer_id == singer_id)
        .order_by(PlayHistory.played_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "song_id": h.song_id,
            "title": h.song.title,
            "artist": h.song.artist,
            "played_at": h.played_at.isoformat(),
        }
        for h in history
    ]


@router.get("/api/singers")
def list_singers(db: Session = Depends(get_db)):
    singers = db.query(Singer).order_by(Singer.name).all()
    return [{"id": s.id, "name": s.name} for s in singers]
