FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    flask \
    flask-socketio \
    eventlet \
    yt-dlp \
    requests

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
