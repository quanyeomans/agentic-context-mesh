FROM python:3.12-slim AS base
# Pin to a specific Python patch for production: FROM python:3.12.7-slim

# System dependencies for sqlite-vec native extension and Neo4j driver
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create runtime directories
RUN mkdir -p /data/vault /data/kairix /data/kairix/workspaces /opt/kairix/bin /opt/kairix/cron  # document store

# Install kairix from local source with all production extras
COPY pyproject.toml setup.cfg* setup.py* README.md /opt/kairix/src/
COPY kairix/ /opt/kairix/src/kairix/
RUN pip install --no-cache-dir "/opt/kairix/src[neo4j,agents,nlp]"
RUN python -m spacy download en_core_web_sm || true

# Copy entrypoint and default config
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY kairix.example.config.yaml /opt/kairix/kairix.config.yaml

# Default environment
# KAIRIX_DOCUMENT_ROOT = document store root (KAIRIX_VAULT_ROOT kept for backwards compat)
ENV KAIRIX_DB_PATH=/data/kairix/index.sqlite \
    KAIRIX_DOCUMENT_ROOT=/data/vault \
    KAIRIX_VAULT_ROOT=/data/vault \
    KAIRIX_WORKSPACE_ROOT=/data/kairix/workspaces \
    KAIRIX_DATA_DIR=/data/kairix \
    KAIRIX_CONFIG_PATH=/opt/kairix/kairix.config.yaml

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
