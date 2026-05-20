from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import engine, Base
from app.routers import songs, queue, library, player, media, settings, main_page

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Karaoke Manager")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(main_page.router)
app.include_router(songs.router)
app.include_router(queue.router)
app.include_router(library.router)
app.include_router(player.router)
app.include_router(media.router)
app.include_router(settings.router)
