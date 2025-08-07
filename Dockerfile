# Base Dockerfile for all services in this multi‑agent system

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && if [ -f /tmp/requirements.txt ]; then \
         pip install --no-cache-dir -r /tmp/requirements.txt; \
       fi \
    && rm -f /tmp/requirements.txt

# Set the working directory where service code will live
WORKDIR /app

# Copy the rest of the application code into the image.  The build
# context determines which files are available here.  In production
# you might want to copy only the necessary files for the service.
COPY . /app

# Entrypoint and command are defined at runtime via docker-compose.  The
# default command is a no‑op to ensure the container doesn’t exit
# immediately when no command is provided.  docker-compose will
# override this CMD with the appropriate service startup command.
CMD ["sleep", "infinity"]