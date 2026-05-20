from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import Song

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    genre: str = "",
    language: str = "",
    decade: str = "",
    fmt: str = "",
    rating: int = 0,
    favorites: bool = False,
    sort: str = "artist",
    page: int = 1,
    db: Session = Depends(get_db),
):
    page_size = 50
    query = db.query(Song).filter(Song.is_active == True)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Song.title.ilike(like), Song.artist.ilike(like))
        )
    if genre:
        query = query.filter(Song.genre.ilike(f"%{genre}%"))
    if language:
        query = query.filter(Song.language.ilike(f"%{language}%"))
    if decade:
        query = query.filter(Song.decade == decade)
    if fmt:
        query = query.filter(Song.file_format == fmt)
    if rating:
        query = query.filter(Song.rating >= rating)
    if favorites:
        query = query.filter(Song.is_favorite == True)

    sort_map = {
        "artist": Song.artist,
        "title": Song.title,
        "date_added": Song.date_added.desc(),
        "play_count": Song.play_count.desc(),
        "rating": Song.rating.desc(),
    }
    query = query.order_by(sort_map.get(sort, Song.artist))

    total = query.count()
    songs = query.offset((page - 1) * page_size).limit(page_size).all()

    genres = [r[0] for r in db.query(Song.genre).filter(Song.genre != "").distinct().order_by(Song.genre).all()]
    languages = [r[0] for r in db.query(Song.language).filter(Song.language != "").distinct().order_by(Song.language).all()]
    decades = [r[0] for r in db.query(Song.decade).filter(Song.decade != "").distinct().order_by(Song.decade).all()]
    formats = [r[0] for r in db.query(Song.file_format).distinct().order_by(Song.file_format).all()]

    return templates.TemplateResponse("library.html", {
        "request": request,
        "songs": songs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "q": q,
        "genre": genre,
        "language": language,
        "decade": decade,
        "fmt": fmt,
        "rating": rating,
        "favorites": favorites,
        "sort": sort,
        "genres": genres,
        "languages": languages,
        "decades": decades,
        "formats": formats,
    })


class SongImport(BaseModel):
    title: str
    artist: str
    file_path: str
    audio_path: Optional[str] = None
    file_format: str
    duration: Optional[float] = 0.0
    genre: Optional[str] = ""
    language: Optional[str] = ""
    decade: Optional[str] = ""
    year: Optional[int] = None


@router.post("/api/songs/import")
def import_song(data: SongImport, db: Session = Depends(get_db)):
    existing = db.query(Song).filter(Song.file_path == data.file_path).first()
    if existing:
        return {"id": existing.id, "status": "exists"}
    song = Song(
        title=data.title,
        artist=data.artist,
        file_path=data.file_path,
        audio_path=data.audio_path,
        file_format=data.file_format,
        duration=data.duration or 0.0,
        genre=data.genre or "",
        language=data.language or "",
        decade=data.decade or "",
        year=data.year,
        is_active=True,
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    return {"id": song.id, "status": "created"}


# Must come before /{song_id} to avoid route conflict
@router.get("/api/songs/search")
def search_songs(q: str = "", db: Session = Depends(get_db)):
    like = f"%{q}%"
    songs = (
        db.query(Song)
        .filter(Song.is_active == True)
        .filter(or_(Song.title.ilike(like), Song.artist.ilike(like)))
        .order_by(Song.artist, Song.title)
        .limit(20)
        .all()
    )
    return [
        {"id": s.id, "title": s.title, "artist": s.artist, "file_format": s.file_format}
        for s in songs
    ]


@router.get("/api/songs/{song_id}")
def get_song(song_id: int, db: Session = Depends(get_db)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    return {
        "id": song.id, "title": song.title, "artist": song.artist,
        "file_format": song.file_format, "duration": song.duration,
        "file_path": song.file_path, "audio_path": song.audio_path,
    }


@router.post("/api/songs/{song_id}/favorite")
def toggle_favorite(song_id: int, db: Session = Depends(get_db)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    song.is_favorite = not song.is_favorite
    db.commit()
    return {"is_favorite": song.is_favorite}


@router.post("/api/songs/{song_id}/rate")
def rate_song(song_id: int, rating: int, db: Session = Depends(get_db)):
    if rating < 0 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be 0-5")
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    song.rating = rating
    db.commit()
    return {"rating": song.rating}


@router.post("/api/songs/{song_id}/edit")
def edit_song(
    song_id: int,
    title: str = "",
    artist: str = "",
    language: str = "",
    genre: str = "",
    decade: str = "",
    notes: str = "",
    db: Session = Depends(get_db),
):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if title:
        song.title = title
    if artist:
        song.artist = artist
    song.language = language
    song.genre = genre
    song.decade = decade
    song.notes = notes
    db.commit()
    return {"ok": True}


@router.delete("/api/songs/{song_id}")
def delete_song(song_id: int, db: Session = Depends(get_db)):
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    db.delete(song)
    db.commit()
    return {"ok": True}
