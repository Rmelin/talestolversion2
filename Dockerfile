# Multi-arch: bygger på arm64 (Raspberry Pi 4/5, 64-bit) og x86-64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 talerstol && mkdir -p /data && chown talerstol:talerstol /data
USER talerstol
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

# Én worker: backend er autoritativ og holder tilstand + WebSockets i én proces.
CMD ["uvicorn", "--factory", "app.main:build", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
