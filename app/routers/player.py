from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import QueueEntry, Song

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/player", response_class=HTMLResponse)
def player_page(request: Request, db: Session = Depends(get_db)):
    current = db.query(QueueEntry).filter(QueueEntry.status == "playing").first()
    upcoming = (
        db.query(QueueEntry)
        .filter(QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .limit(5)
        .all()
    )
    return templates.TemplateResponse("player.html", {
        "request": request,
        "current": current,
        "upcoming": upcoming,
    })
