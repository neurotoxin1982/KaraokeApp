import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://karaoke:karaoke@db:5432/karaoke"
)
MUSIC_DIR = os.getenv("MUSIC_DIR", "/music")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

SUPPORTED_VIDEO = {".mp4", ".avi", ".webm", ".mkv", ".m4v", ".mpeg", ".mpg"}
SUPPORTED_AUDIO = {".mp3", ".ogg", ".m4a", ".flac", ".wav"}
SUPPORTED_CDG = {".cdg"}
SUPPORTED_KAR = {".kar", ".mid"}
SUPPORTED_ZIP = {".zip"}

ALL_SUPPORTED = SUPPORTED_VIDEO | SUPPORTED_AUDIO | SUPPORTED_CDG | SUPPORTED_KAR | SUPPORTED_ZIP
