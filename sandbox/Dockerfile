# The worker sandbox: Python with the packages most jobs reach for, and Node.js.
# Built once, automatically, the first time Rimuru starts with Docker available.
#
# Work containers run with NO network, a read-only root filesystem and every
# capability dropped (see rimuru/worker/shell.py). Anything a job needs that is
# not here is installed on demand - in a separate container that has the
# internet but cannot see the person's files (DockerShell.install).

# Node.js 22 (LTS), from the official image: the same Debian release as below.
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN pip install --no-cache-dir \
      pytest==8.3.4 \
      requests==2.32.3 httpx==0.28.1 beautifulsoup4==4.12.3 \
      flask==3.1.0 fastapi==0.115.6 uvicorn==0.34.0 jinja2==3.1.5 \
      numpy==2.2.1 pandas==2.2.3 matplotlib==3.10.0 pillow==11.1.0 \
      openpyxl==3.1.5 pyyaml==6.0.2 sqlalchemy==2.0.36

WORKDIR /workspace
