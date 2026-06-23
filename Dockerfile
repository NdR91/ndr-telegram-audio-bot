# Dockerfile
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Crea cartella app
WORKDIR /app

# Copia requirements e installa dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia sorgenti bot
COPY bot/ ./bot/

# Crea directory per template e static web
RUN mkdir -p ./bot/web/templates ./bot/web/static

# Crea utente non-root e cartella per audio
RUN adduser --disabled-password --gecos "" appuser && mkdir -p ./audio_files && chown -R appuser:appuser /app

USER appuser

# Comando di avvio — web frontend control plane
# Legacy CLI: python -u bot/main.py
CMD ["python", "-u", "-m", "bot.web.main"]
