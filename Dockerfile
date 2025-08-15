# Base Dockerfile for all services in this multi‑agent system
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && if [ -f /tmp/requirements.txt ]; then \
         pip install --no-cache-dir -r /tmp/requirements.txt; \
       fi \
    && rm -f /tmp/requirements.txt

WORKDIR /app
COPY . /app

# Entrypoint and command are defined at runtime via docker-compose.
CMD ["sleep", "infinity"]