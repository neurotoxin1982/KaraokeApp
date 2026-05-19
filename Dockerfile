# Python 3.7 ist die stabilste Basis für Spleeter und TensorFlow 2.3
FROM python:3.7-slim

# System-Werkzeuge installieren (ffmpeg für Audio, git für Pakete)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Wichtig: Pip und Setup-Tools aktualisieren, damit die Installation klappt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Spleeter und alle Web-Pakete installieren
RUN pip install --no-cache-dir \
    flask \
    redis \
    rq \
    yt-dlp \
    spleeter==2.3.0

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]