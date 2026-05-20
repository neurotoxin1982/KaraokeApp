FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    flask \
    flask-socketio \
    eventlet \
    requests \
    yt-dlp \
    yt-dlp-get-pot \
    bgutil-yt-dlp-plugin

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
