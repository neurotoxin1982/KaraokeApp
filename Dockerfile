# Wir nutzen das offizielle, fertige Spleeter-Image von Deezer
FROM deezer/spleeter:3.8-5stems

# Wechseln zum Root-User, um Flask und Co. installieren zu dürfen
USER root

# Installiere die zusätzlichen Web- und Queue-Pakete
RUN pip install --no-cache-dir \
    flask \
    redis \
    rq \
    yt-dlp

WORKDIR /app

# Kopiere deine Skripte in den Container
COPY . .

EXPOSE 5000

# Standardbefehl (wird für den Web-Service genutzt)
CMD ["python", "app.py"]