FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY . .

# Run as non-root for defense-in-depth (Cloud Run best practice).
RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 8080
CMD exec uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
