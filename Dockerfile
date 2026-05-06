FROM python:3.11-slim

# scram — emergency kill-switch service.
#
# Runs `scram run` (evaluator + FastAPI). Migrations are applied via
# `scram migrate` as a release task; deploy pipelines should run that
# before flipping traffic to a new instance.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so layer cache survives source edits.
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY migrations /app/migrations

RUN pip install --upgrade pip && pip install -e .

ENV SCRAM_HTTP_PORT=8400 \
    SCRAM_MIGRATIONS_DIR=/app/migrations

EXPOSE 8400

# Run as non-root for defense-in-depth. scram has no privileged
# authority by design (ADR-001), but running as root would be
# gratuitous.
RUN useradd --system --uid 1000 scram && chown -R scram:scram /app
USER scram

CMD ["scram", "run"]
