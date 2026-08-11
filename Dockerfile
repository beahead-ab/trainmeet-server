FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/beahead-ab/trainmeet-server" \
      org.opencontainers.image.title="TrainMeet Server" \
      org.opencontainers.image.description="Portable local-first TrainMeet runtime"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAINMEET_MQTT_HOST=127.0.0.1

RUN groupadd --system --gid 10001 trainmeet \
    && useradd --system --uid 10001 --gid trainmeet --home-dir /var/lib/trainmeet-server trainmeet

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir '.[mqtt]' \
    && install -d -o trainmeet -g trainmeet -m 0750 /var/lib/trainmeet-server

USER trainmeet
EXPOSE 8787
VOLUME ["/var/lib/trainmeet-server"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/v1/info', timeout=2)" || exit 1

ENTRYPOINT ["python", "-m", "tambox_gateway.local_server"]
CMD ["--external-broker", "--bind", "0.0.0.0", "--state-dir", "/var/lib/trainmeet-server"]
