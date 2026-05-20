from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import QueueEntry

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
def main_page(request: Request, db: Session = Depends(get_db)):
    queue = (
        db.query(QueueEntry)
        .filter(QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
        .all()
    )
    return templates.TemplateResponse("main.html", {
        "request": request,
        "queue": queue,
    })


@router.get("/queue")
def redirect_queue():
    return RedirectResponse(url="/", status_code=302)


@router.get("/library")
def redirect_library():
    return RedirectResponse(url="/settings", status_code=302)
