FROM python:3.12-slim

# ffmpeg để chuyển đổi/gộp audio, nodejs làm JS runtime cho yt-dlp khi tải YouTube.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

ENV L2A_DATA_DIR=/app/data PORT=8000
EXPOSE 8000
# --no-access-log: link tải file có ?key=..., không để lọt vào log.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
