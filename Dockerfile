# Dockerfile
FROM python:3.10-slim

# Install FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Crea cartella app
WORKDIR /app

# Copia requirements e installa dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia sorgenti bot
COPY bot/ ./bot/
COPY authorized.json .

# Crea cartella per audio
RUN mkdir -p ./audio_files

# Comando di avvio
CMD ["python", "-u", "bot/main.py"]