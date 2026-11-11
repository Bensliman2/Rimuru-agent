# Rimuru itself, in a container. Use it with docker-compose.yml:
#
#     docker compose up -d        then open http://localhost:8800
#
# The clones' sandbox is not in here: their commands run in containers of their
# own, started in the "sandbox" service (a Docker of Rimuru's own - see
# docker-compose.yml). This image only needs the docker command to talk to it.

FROM docker:29-cli AS docker-cli

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: GitHub work keeps a git copy of each repository. docker: the command only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock
COPY . .
RUN chmod +x docker-entrypoint.sh

# Listen on the container's own addresses, so the published port reaches the
# app - but answer only as localhost. docker-compose.yml publishes the port on
# this computer only (127.0.0.1).
ENV RIMURU_BIND=0.0.0.0 \
    RIMURU_HOST=127.0.0.1 \
    RIMURU_ALLOWED_HOSTS=localhost,127.0.0.1 \
    RIMURU_ENV_FILE=/rimuru/data/.env \
    RIMURU_MODELS=/rimuru/data/models.toml \
    RIMURU_SKILLS=/rimuru/skills
EXPOSE 8800 8810 8820

# Runs as you, not as root: see docker-entrypoint.sh.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/api/health', timeout=4)"

CMD ["python", "run.py", "--speed", "1", "--data", "/rimuru/data"]
