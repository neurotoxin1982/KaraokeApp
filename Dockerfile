# Wir nutzen Python 3.9 (schlank aber kompatibel mit Spleeter/TensorFlow)
FROM python:3.9-slim

# Installiere System-Abhängigkeiten (ffmpeg für Audio, libgomp für KI-Berechnungen)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    libgomp1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip und installiere die Pakete mit festen Versionen für maximale Stabilität
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

RUN pip install --no-cache-dir \
    flask \
    redis \
    rq \
    yt-dlp \
    spleeter==2.3.2

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]