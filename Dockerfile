# ---- Build stage: install dependencies with uv ----
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application code and install the project itself
COPY . .
RUN uv sync --frozen --no-dev


# ---- Runtime stage: lean image ----
FROM python:3.13-slim AS runtime

# Non-root user for security
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# Copy the virtual env and app code from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Ensure data directories exist and are writable
RUN mkdir -p /app/data /app/data/images /app/tts_cache && \
    chown -R app:app /app/data /app/tts_cache

# Use the venv Python
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/login')" || exit 1

# Production server: no reload, single worker is fine for SQLite
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
