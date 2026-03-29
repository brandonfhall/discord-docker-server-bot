FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install "requests<2.32.0"

COPY src/ ./src/
COPY entrypoint.sh /entrypoint.sh

RUN useradd -r -m botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app \
    && chmod +x /entrypoint.sh

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${STATUS_PORT:-8000}/')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "src.bot"]
