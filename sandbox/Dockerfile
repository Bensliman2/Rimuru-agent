# The worker sandbox: a plain Python with pytest, nothing else.
# Built once, automatically, the first time Rimuru starts with Docker available.
# Containers from it run with no network, a read-only root filesystem and every
# capability dropped (see rimuru/worker/shell.py).
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.4
WORKDIR /workspace
