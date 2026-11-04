# syntax=docker/dockerfile:1
# Build from the repo root: docker build -f docker/Dockerfile .
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Run from source so BASE_DIR (/app) resolves configs/sites and sql/.
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first (cached layer). requirements.lock is exported from uv.lock
# with hashes (`make lock`), so the image gets exactly the tested versions.
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

COPY alembic.ini ./
COPY migrations ./migrations
COPY sql ./sql
COPY configs ./configs
COPY src ./src

RUN useradd --system --no-create-home appuser
USER appuser

CMD ["python", "-m", "realestate.entrypoints.scheduler"]
