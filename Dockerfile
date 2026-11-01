FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd
LABEL org.opencontainers.image.title="DwellMind HA" \
      org.opencontainers.image.description="Local-first, observation-only room intelligence for Home Assistant" \
      org.opencontainers.image.source="https://github.com/spikked27/dwell-mind-ha" \
      org.opencontainers.image.licenses="MIT"
LABEL net.unraid.docker.webui="http://[IP]:[PORT:8128]/ui" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/spikked27/dwell-mind-ha/main/web/icon.png"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data RUN_MODE=service
WORKDIR /app
COPY requirements-live.txt /app/
RUN python3 -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements-live.txt
COPY container_app.py drop_privileges.py ha_live_observer.py ws_states.py \
     passive_observer.py private_journal.py live_report.py rooms.py \
     office_collect.py policy.py upstream.py /app/
COPY docker-entrypoint.sh /app/
COPY service_app.py /app/
COPY capture_summary.py /app/
COPY thermal_forecast.py /app/
COPY shadow_learning.py shadow_campaign.py shadow_archive.py preference_learning.py /app/
COPY web/index.html web/studio.css web/studio.js web/overview.js web/icon.svg /app/web/
RUN chmod 0555 /app/docker-entrypoint.sh && mkdir /data
EXPOSE 8128
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8128/health', timeout=3).close()"
# Entry point initializes ONLY an empty mount, then drops to PUID:PGID.
# Advanced users can run --user UID:GID with an already prepared mode-0700 mount.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
