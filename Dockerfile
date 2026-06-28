FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8765 \
    DB=data/navimow.sqlite \
    VIEWER_OUTPUT=viewer/navimow-map \
    STATUS_ONLY=auto \
    SATELLITE=0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl make zstd \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh

EXPOSE 8765

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["serve"]
