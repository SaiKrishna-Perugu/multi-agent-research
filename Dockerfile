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

# `--no-create-home` leaves $HOME=/home/appuser pointing at a directory that
# was never created, so `uv run`'s own cache (~/.cache/uv by default) fails
# with a permission error at container startup. Point it inside /app, which
# is already appuser-owned.
ENV UV_CACHE_DIR=/app/.cache/uv

USER appuser

EXPOSE 8080
# --no-sync: the image already has the exact environment from the frozen,
# no-dev build-time sync. Without this flag, `uv run` re-syncs against
# pyproject.toml on every container start -- which includes the dev group
# (ruff, pytest, ...), so it re-downloads and installs them at startup,
# requiring network access production shouldn't need and slowing every boot.
CMD exec uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
