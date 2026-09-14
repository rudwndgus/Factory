FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api /app/apps/api
ENV PYTHONPATH=/app/apps/api DATA_DIR=/data FFMPEG_PATH=/usr/bin/ffmpeg
EXPOSE 8000
CMD ["uvicorn","factory.main:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
