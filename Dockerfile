# ── Build stage: install all dependencies ────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.cfg* setup.py* README.md /opt/kairix/src/
COPY kairix/ /opt/kairix/src/kairix/

RUN pip install --no-cache-dir "/opt/kairix/src[neo4j,agents,nlp,rerank]"
RUN python -m spacy download en_core_web_sm || true

# ── Runtime stage: slim image with only installed packages ───────────────────
FROM python:3.12-slim AS runtime

# Copy installed Python packages from builder (no build-essential, no source)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create runtime directories
RUN mkdir -p /data/vault /data/kairix /data/kairix/workspaces /opt/kairix/bin /opt/kairix/cron

# Runtime-only system deps (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy entrypoint and default config
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY kairix.example.config.yaml /opt/kairix/kairix.config.yaml

ENV KAIRIX_DB_PATH=/data/kairix/index.sqlite \
    KAIRIX_DOCUMENT_ROOT=/data/vault \
    KAIRIX_VAULT_ROOT=/data/vault \
    KAIRIX_WORKSPACE_ROOT=/data/kairix/workspaces \
    KAIRIX_DATA_DIR=/data/kairix \
    KAIRIX_CONFIG_PATH=/opt/kairix/kairix.config.yaml

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
