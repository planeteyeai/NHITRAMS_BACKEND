# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────
# NHIT AI Identifier / Distress API
# Multi-stage production image
# ─────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.12-slim-bookworm

# ── Stage 1: build dependencies ──────────────────────────────
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=nhit.settings \
    APP_HOME=/app \
    PORT=8000 \
    GUNICORN_WORKERS=3 \
    GUNICORN_TIMEOUT=120 \
    GUNICORN_BIND=0.0.0.0:8000

WORKDIR ${APP_HOME}

# Runtime libs only (no compilers)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 appuser \
    && useradd --system --uid 1000 --gid appuser --home-dir ${APP_HOME} --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

# Application source
COPY --chown=appuser:appuser manage.py ./
COPY --chown=appuser:appuser nhit ./nhit
COPY --chown=appuser:appuser authentication ./authentication
COPY --chown=appuser:appuser reports ./reports
COPY --chown=appuser:appuser distress ./distress
COPY --chown=appuser:appuser inventory ./inventory
COPY --chown=appuser:appuser pdf ./pdf
COPY --chown=appuser:appuser pis ./pis
COPY --chown=appuser:appuser traffic ./traffic
COPY --chown=appuser:appuser pavement ./pavement
COPY --chown=appuser:appuser roadside ./roadside
COPY --chown=appuser:appuser docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p ${APP_HOME}/staticfiles ${APP_HOME}/media \
    && chown -R appuser:appuser ${APP_HOME}

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health/" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "nhit.wsgi:application"]
