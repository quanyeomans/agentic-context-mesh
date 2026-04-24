FROM python:3.12-slim AS base

# System dependencies for sqlite-vec native extension and Neo4j driver
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create runtime directories
RUN mkdir -p /data/vault /data/kairix /opt/kairix/bin /opt/kairix/cron

# Install kairix with all production extras
# Pin to a specific version in production: kairix==2026.4.24
RUN pip install --no-cache-dir "kairix[neo4j,agents]"

# Copy entrypoint and default config
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY kairix.example.config.yaml /opt/kairix/kairix.config.yaml

# Default environment
ENV KAIRIX_DB_PATH=/data/kairix/index.sqlite \
    KAIRIX_VAULT_ROOT=/data/vault \
    KAIRIX_DATA_DIR=/data/kairix \
    KAIRIX_CONFIG_PATH=/opt/kairix/kairix.config.yaml

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
