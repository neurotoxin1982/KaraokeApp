import io
import os
import zipfile
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Song

router = APIRouter()

MIME_MAP = {
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "avi": "video/x-msvideo",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "m4v": "video/mp4",
    "mpeg": "video/mpeg",
    "mpg": "video/mpeg",
    "cdg": "application/octet-stream",
}


def _resolve_path(song_id: int, track: str) -> tuple[bytes | None, str, str]:
    """
    Return (data_bytes_or_none, file_path_for_range, mime).
    For ZIP entries, read the bytes directly.
    """
    db = SessionLocal()
    try:
        song = db.query(Song).filter(Song.id == song_id).first()
        if not song:
            return None, "", ""

        if track == "audio":
            path = song.audio_path or song.file_path
        else:
            path = song.file_path

        if not path:
            return None, "", ""

        ext = os.path.splitext(path)[-1].lstrip(".").lower()
        mime = MIME_MAP.get(ext, "application/octet-stream")

        if "::" in path:
            zip_path, inner = path.split("::", 1)
            with zipfile.ZipFile(zip_path, "r") as zf:
                data = zf.read(inner)
            return data, "", mime

        return None, path, mime
    finally:
        db.close()


def _range_response(file_path: str, mime: str, request: Request) -> Response:
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("Range")

    if range_header:
        start, end = 0, file_size - 1
        parts = range_header.replace("bytes=", "").split("-")
        if parts[0]:
            start = int(parts[0])
        if parts[1]:
            end = int(parts[1])
        end = min(end, file_size - 1)
        chunk = end - start + 1

        def gen():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk
                while remaining > 0:
                    buf = f.read(min(65536, remaining))
                    if not buf:
                        break
                    remaining -= len(buf)
                    yield buf

        return StreamingResponse(
            gen(),
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk),
            },
        )

    def gen_full():
        with open(file_path, "rb") as f:
            while True:
                buf = f.read(65536)
                if not buf:
                    break
                yield buf

    return StreamingResponse(
        gen_full(),
        media_type=mime,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@router.get("/media/{song_id}/{track}")
async def serve_media(song_id: int, track: str, request: Request):
    if track not in ("audio", "video", "cdg"):
        raise HTTPException(status_code=400, detail="track must be audio, video, or cdg")

    data, file_path, mime = _resolve_path(song_id, track)

    if data is not None:
        return Response(content=data, media_type=mime)

    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return _range_response(file_path, mime, request)
