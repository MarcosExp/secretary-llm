FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 1000 --create-home app \
    && mkdir /data && chown app:app /data   # new named volumes inherit this owner

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY modules ./modules
RUN pip install --no-cache-dir ".[subscription]"

USER app
# The Claude Code CLI inside the Agent SDK writes config and session files under HOME;
# the root filesystem is read-only, so point it at the /tmp tmpfs.
ENV HOME=/tmp CLAUDE_CONFIG_DIR=/tmp/claude
# Loopback by default; docker-compose.yml sets APP_HOST for the published port.
CMD ["sh", "-c", "exec uvicorn app.api.main:app --host ${APP_HOST:-127.0.0.1} --port 8000"]
