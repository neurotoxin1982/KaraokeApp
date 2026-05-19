# Wir bleiben bei Python 3.7
FROM python:3.7-slim

# System-Bibliotheken
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip
RUN pip install --upgrade pip

# Erst die Web-Pakete
RUN pip install --no-cache-dir flask redis rq yt-dlp

# Danach Spleeter mit der "no-deps" Option, um die automatische
# Installation von inkompatiblen TensorFlow-Versionen zu verhindern
RUN pip install --no-cache-dir --no-deps spleeter==2.3.0 \
    && pip install --no-cache-dir tensorflow==2.3.0 pandas numpy

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]