FROM python:3.12-slim

# Install Azure SDK — only what the sidecar needs, no kairix deps
RUN pip install --no-cache-dir \
    azure-keyvault-secrets>=4.7.0 \
    azure-identity>=1.15.0

WORKDIR /app

COPY fetch_secrets.py /app/fetch_secrets.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Run as non-root
RUN useradd --system --no-create-home --uid 999 vaultagent
USER vaultagent

ENTRYPOINT ["/app/entrypoint.sh"]
