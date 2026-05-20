from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine, Base
from app.routers import songs, singers, queue, library, player, media, settings

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Karaoke Manager")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(songs.router)
app.include_router(singers.router)
app.include_router(queue.router)
app.include_router(library.router)
app.include_router(player.router)
app.include_router(media.router)
app.include_router(settings.router)


@app.get("/")
def root():
    return RedirectResponse(url="/library")
