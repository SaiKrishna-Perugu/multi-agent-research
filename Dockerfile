# python3.13-bookworm-slim, pinned by digest for reproducibility -- a floating
# tag can change under you between a CI-tested build and a later deploy.
# Re-resolve with: docker buildx imagetools inspect ghcr.io/astral-sh/uv:python3.13-bookworm-slim
FROM ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

WORKDIR /app

COPY pyproject.toml uv.lock* ./
# No fallback to an unfrozen sync: CI only ever tests `--frozen`, so a lock
# mismatch here should fail the build loudly, not silently ship dependency
# versions that were never tested.
RUN uv sync --frozen --no-dev

COPY . .

# Run as non-root for defense-in-depth (Cloud Run best practice). The user is
# created and ownership fixed up before switching -- app/main.py writes
# logs/ and the SQLite checkpoint into this same tree at startup, and a plain
# `COPY . .` followed by `USER appuser` leaves /app root-owned, which would
# make that first write a PermissionError.
RUN adduser --disabled-password --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD exec uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
