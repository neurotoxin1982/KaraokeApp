FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    flask \
    redis \
    rq \
    yt-dlp \
    spleeter

COPY . .

EXPOSE 5000