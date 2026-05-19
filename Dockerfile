# Python 3.10 — compatible with spleeter 2.3.2 + tensorflow 2.12
FROM python:3.10-slim

# System dependencies + Node.js (required by yt-dlp for YouTube JS extraction)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip

RUN pip install --no-cache-dir "tensorflow==2.12.0" \
    && pip install --no-cache-dir "spleeter==2.3.2"

RUN pip install --no-cache-dir "click>=8.0" flask redis rq yt-dlp

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]