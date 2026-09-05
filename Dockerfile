# ---------- builder stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency manifests first for layer caching
COPY pyproject.toml ./
COPY packages/ ./packages/
COPY tools/osm-import/ ./tools/osm-import/
COPY services/ ./services/

# Install all dependencies into a virtual-env we can copy later
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . ./packages/schemas ./tools/osm-import ./services/signal-rl ./services/routing \
        ./packages/persistence ./packages/observability

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime

# Install only the runtime C libraries needed by asyncpg / PostGIS bindings
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --gid 1000 marga && \
    useradd --uid 1000 --gid marga --shell /bin/bash --create-home marga

WORKDIR /app

# Copy virtual-env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Copy application code
COPY --chown=marga:marga services/ ./services/
COPY --chown=marga:marga packages/ ./packages/

USER marga

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
