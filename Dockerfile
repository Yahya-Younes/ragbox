# syntax=docker/dockerfile:1
# ---- build: install dependencies into an isolated virtualenv
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt \
 && /opt/venv/bin/pip uninstall -y -q pip \
 && find /opt/venv -depth \( -name __pycache__ -o -name tests -o -name "*.pyi" \) -exec rm -rf {} +

# ---- runtime: small image, non-root user, no build tools
FROM python:3.12-slim
LABEL org.opencontainers.image.title="ragbox" \
      org.opencontainers.image.description="The simplest local RAG: drop your files, ask questions." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Yahya-Younes/ragbox"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAGBOX_DATA_DIR=/data
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin ragbox \
 && mkdir -p /data && chown ragbox:ragbox /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY ragbox ./ragbox
USER ragbox
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"]
CMD ["uvicorn", "ragbox.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--timeout-graceful-shutdown", "5"]
