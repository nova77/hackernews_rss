# syntax=docker/dockerfile:1

# Both the patch level and the Debian release float with this tag. To pin them:
#   docker run --rm python:3.12-slim \
#     sh -c 'python -V; grep VERSION_CODENAME /etc/os-release'
# then use the result, e.g. FROM python:3.12.7-slim-bookworm.
FROM python:3.12-slim

# Unbuffered so the logs reach `docker logs` as they happen, and no .pyc files,
# which are dead weight in a container that is rebuilt rather than updated.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copied on its own, so the dependency layer is rebuilt only when the pinned
# requirements change and not on every code edit.
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app

# The port docker-compose.yml publishes on. Documentation only.
EXPOSE 80

ENTRYPOINT ["gunicorn"]

# One worker is plenty for a personal feed: the view is I/O bound and
# already fans out to MAX_WORKERS threads of its own per request, so the 4
# threads here only bound how many feeds are built concurrently. The timeout
# is well above a normal request (TIMEOUT_SECS * 1.5) to recycle a worker
# stuck on an unresponsive site rather than a slow one.
CMD ["-b", "0.0.0.0:80", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "main:app"]
