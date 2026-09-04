# Stage 1: Build virtual environment with uv
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock* ./
# Frozen, reproducible dependency resolution without dev tools
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Hardened minimal runtime container
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy application source code
COPY app/ /app/app/
COPY pyproject.toml /app/

# Run as non-root user for defense-in-depth (Cloud Run best practice)
RUN adduser --disabled-password --no-create-home appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Native healthcheck using python standard library (no curl dependency required)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
