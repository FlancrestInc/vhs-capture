FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        v4l-utils \
        alsa-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app /app/app
COPY scripts /app/scripts
COPY vhs-capture.sh /app/vhs-capture.sh

RUN pip install --no-cache-dir fastapi uvicorn jinja2 python-multipart

ENV VHS_OUTPUT_DIR=/output
ENV VHS_UI_HOST=0.0.0.0
ENV VHS_UI_PORT=8099

EXPOSE 8099

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099"]
