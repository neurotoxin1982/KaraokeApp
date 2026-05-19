# Python 3.10 — compatible with spleeter 2.4.0
FROM python:3.10-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip
RUN pip install --upgrade pip

# Web & queue packages
RUN pip install --no-cache-dir flask redis rq yt-dlp

# Spleeter — pip resolves a compatible tensorflow automatically
RUN pip install --no-cache-dir spleeter==2.4.0

# Pre-download the spleeter 2stems model at build time so there's no
# network delay or failure on first job execution in production
RUN python -c "from spleeter.separator import Separator; Separator('spleeter:2stems')"

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]