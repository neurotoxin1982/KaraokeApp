# Python 3.10 — compatible with spleeter 2.3.2 + tensorflow 2.12
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

# Install tensorflow first, then spleeter.
# spleeter pulls in typer which downgrades click — so we install Flask AFTER
# spleeter and force click>=8.0 so Flask's CLI doesn't break.
RUN pip install --no-cache-dir "tensorflow==2.12.0" \
    && pip install --no-cache-dir "spleeter==2.3.2"

# Install web packages last so they override spleeter's click downgrade
RUN pip install --no-cache-dir "click>=8.0" flask redis rq yt-dlp

# Pre-download the 2stems model at build time — avoids first-run delays
RUN python -c "from spleeter.separator import Separator; Separator('spleeter:2stems')"

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
