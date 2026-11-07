FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py pipeline.py avatar.png ./

ENV HOST=0.0.0.0 PORT=7860 AVATAR_PATH=/app/avatar.png
EXPOSE 7860
CMD ["python3", "server.py"]
