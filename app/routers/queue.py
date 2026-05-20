from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import QueueEntry, Song, Singer, PlayHistory

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _reorder(db: Session):
    entries = (
        db.query(QueueEntry)
        .filter(QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .all()
    )
    for i, e in enumerate(entries):
        e.position = i
    db.commit()


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, db: Session = Depends(get_db)):
    pending = (
        db.query(QueueEntry)
        .filter(QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
        .all()
    )
    history = (
        db.query(QueueEntry)
        .filter(QueueEntry.status.in_(["done", "skipped"]))
        .order_by(QueueEntry.finished_at.desc())
        .limit(20)
        .all()
    )
    singers = db.query(Singer).order_by(Singer.name).all()
    return templates.TemplateResponse("queue.html", {
        "request": request,
        "queue": pending,
        "history": history,
        "singers": singers,
    })


@router.get("/api/queue")
def get_queue(db: Session = Depends(get_db)):
    entries = (
        db.query(QueueEntry)
        .filter(QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
        .all()
    )
    return [
        {
            "id": e.id,
            "position": e.position,
            "status": e.status,
            "song_id": e.song_id,
            "title": e.song.title,
            "artist": e.song.artist,
            "file_format": e.song.file_format,
            "singer_id": e.singer_id,
            "singer_name": e.singer.name if e.singer else None,
        }
        for e in entries
    ]


def _get_or_create_singer(db: Session, name: str):
    name = name.strip()
    if not name:
        return None
    singer = db.query(Singer).filter(Singer.name == name).first()
    if not singer:
        singer = Singer(name=name)
        db.add(singer)
        db.flush()
    return singer


@router.post("/api/queue")
def add_to_queue(song_id: int, singer_name: str = "", db: Session = Depends(get_db)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    singer = _get_or_create_singer(db, singer_name) if singer_name.strip() else None
    max_pos = db.query(QueueEntry).filter(QueueEntry.status == "pending").count()
    entry = QueueEntry(
        song_id=song_id,
        singer_id=singer.id if singer else None,
        position=max_pos,
        status="pending",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "position": entry.position}


@router.delete("/api/queue/{entry_id}")
def remove_from_queue(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    db.delete(entry)
    db.commit()
    _reorder(db)
    return {"ok": True}


@router.post("/api/queue/{entry_id}/move")
def move_entry(entry_id: int, direction: str, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    entries = (
        db.query(QueueEntry)
        .filter(QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .all()
    )
    idx = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
    if idx is None:
        raise HTTPException(status_code=400, detail="Entry not in pending queue")

    if direction == "up" and idx > 0:
        entries[idx].position, entries[idx - 1].position = (
            entries[idx - 1].position,
            entries[idx].position,
        )
    elif direction == "down" and idx < len(entries) - 1:
        entries[idx].position, entries[idx + 1].position = (
            entries[idx + 1].position,
            entries[idx].position,
        )
    db.commit()
    return {"ok": True}


@router.post("/api/queue/{entry_id}/skip")
def skip_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    entry.status = "skipped"
    entry.finished_at = datetime.utcnow()
    db.commit()
    _reorder(db)
    return {"ok": True}


@router.post("/api/queue/next")
def advance_queue(db: Session = Depends(get_db)):
    """Mark current playing as done, set next pending to playing."""
    playing = db.query(QueueEntry).filter(QueueEntry.status == "playing").first()
    if playing:
        playing.status = "done"
        playing.finished_at = datetime.utcnow()
        # Record play history
        ph = PlayHistory(song_id=playing.song_id, singer_id=playing.singer_id)
        db.add(ph)
        # Update song play count
        playing.song.play_count += 1
        playing.song.last_played = datetime.utcnow()
        # Update singer stats
        if playing.singer:
            playing.singer.total_songs_sung += 1
            playing.singer.last_seen = datetime.utcnow()
        db.commit()

    next_entry = (
        db.query(QueueEntry)
        .filter(QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .first()
    )
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.utcnow()
        db.commit()
        return {
            "id": next_entry.id,
            "song_id": next_entry.song_id,
            "title": next_entry.song.title,
            "artist": next_entry.song.artist,
            "file_format": next_entry.song.file_format,
            "singer_name": next_entry.singer.name if next_entry.singer else None,
        }
    return {"id": None}


@router.get("/api/queue/current")
def get_current(db: Session = Depends(get_db)):
    entry = db.query(QueueEntry).filter(QueueEntry.status == "playing").first()
    if not entry:
        return {"id": None}
    return {
        "id": entry.id,
        "song_id": entry.song_id,
        "title": entry.song.title,
        "artist": entry.song.artist,
        "file_format": entry.song.file_format,
        "file_path": entry.song.file_path,
        "audio_path": entry.song.audio_path,
        "duration": entry.song.duration,
        "singer_name": entry.singer.name if entry.singer else None,
    }
