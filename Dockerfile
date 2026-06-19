FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORK_ROOT=/tmp/yks \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rubberband-cli libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/yks \
    && chown appuser:appuser /tmp/yks

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY app ./app
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.md ./

USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]

