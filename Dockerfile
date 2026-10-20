FROM python:3.12-slim

# ffmpeg: the DJ analysis decodes arbitrary audio formats with it; git: install plugins from repositories
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data /music /app /plugins && chown app:app /data /app /plugins

WORKDIR /app
USER app
ENV PATH=/home/app/.local/bin:$PATH PYTHONUNBUFFERED=1

COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=app:app . .

# The entrypoint runs as root for a moment (fixes the owner of mounted folders), then continues as the app user
USER root

# Version in the UI: docker compose reads GIT_COMMIT from the environment
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT MUSIC_DIR=/music COVER_DIR=/data/covers DB_PATH=/data/server.db PLUGINS_DIR=/plugins PLUGIN_DATA_DIR=/data/plugin-data

VOLUME /data /plugins
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/server-info', timeout=4)"
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
