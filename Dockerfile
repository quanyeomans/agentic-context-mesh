# ── Build stage: install kairix + dependencies into /install ────────────────
FROM python:3.12-slim AS builder

# Version passed in by the publishing workflow (docker-publish.yml). The
# .dockerignore excludes .git from the build context, so setuptools-scm
# can't auto-derive the version and would fall back to "0.0.0" (see
# fallback_version in pyproject.toml). Passing it as a build-arg and
# exporting SETUPTOOLS_SCM_PRETEND_VERSION lets pip install . compute the
# real version without needing the git history in-context (#267).
ARG KAIRIX_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${KAIRIX_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Explicit, glob-free file list — kairix is pyproject.toml-only; the legacy
# setup.cfg/setup.py globs from earlier images were a Sonar S6470 hotspot
# and didn't actually match anything in the source tree.
COPY pyproject.toml README.md ./
COPY kairix/ ./kairix/

# Install PyTorch CPU-only first (prevents pulling ~5GB CUDA libs on GPU-less
# servers), then the kairix package with all the extras the runtime image
# actually needs, all targeted at --prefix=/install so the runtime stage
# can COPY a single directory tree.
RUN pip install --no-cache-dir --prefix=/install \
        torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --prefix=/install \
        ".[neo4j,agents,nlp,rerank,markitdown,pdf_fallback,docx,pptx,xlsx,ocr]" \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       /install/bin/python -m spacy download en_core_web_sm || true

# ── Runtime stage: slim image with s6 supervisor + kairix package ───────────
FROM python:3.12-slim

# s6-overlay v3.1.6.2 — current stable. Pin the version so future
# operators reading this Dockerfile know exactly which release of the
# overlay supplies /init, /etc/services.d/, and /etc/cont-init.d/.
# Upgrade by bumping the ARG, rebuilding, and re-running the Plan 2
# integration tests (tests/integration/test_container_supervisor.py).
ARG S6_OVERLAY_VERSION=3.1.6.2
# xz-utils is required to extract the .tar.xz overlay archives; python:slim
# images don't include it by default. Install before the s6 ADD/tar step.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp/
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz \
    && rm /tmp/s6-overlay-*.tar.xz

# Runtime-only system deps:
# - curl: HEALTHCHECK probe against /healthz/ready
# - tesseract-ocr: the C++ engine pytesseract wraps (the ocr extractor's
#   Python deps come from the [ocr] extra above; without the tesseract
#   binary pytesseract.image_to_string raises TesseractNotFoundError)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Create the kairix system user with uid + gid that match the host
# convention used across the deployed fleet. The matching ids mean
# bind-mounted files written by the container land as kairix:kairix on
# the host volume — no per-host UID coordination needed.
RUN groupadd --system --gid 985 kairix && \
    useradd --system --uid 995 --gid 985 --no-create-home \
            --shell /usr/sbin/nologin --home-dir /var/lib/kairix kairix && \
    mkdir -p /var/lib/kairix /var/cache/kairix /etc/kairix \
             /opt/kairix/plugins /opt/kairix/reference-library /opt/kairix/suites && \
    chown -R kairix:kairix /var/lib/kairix /var/cache/kairix /etc/kairix

# Copy installed kairix package from builder stage
COPY --from=builder /install /usr/local

# Expose the kairix-bundled openclaw plugins at a stable path (#246 W5).
# Symlink survives Python minor-version moves of site-packages; matches
# the canonical openclaw config snippet in docs/operations/MCP-DEPLOYMENT.md.
RUN ln -s /usr/local/lib/python3.12/site-packages/kairix/plugins/openclaw \
          /opt/kairix/plugins/openclaw

# Default config + reference library + evaluation suites (stable test corpus
# ships with the image so `docker exec <c> kairix eval ...` examples in
# docs/operations/MCP-DEPLOYMENT.md stay stable across releases).
COPY kairix.example.config.yaml /etc/kairix/kairix.config.yaml
COPY reference-library/ /opt/kairix/reference-library/
COPY suites/ /opt/kairix/suites/

# Copy s6 service definitions + cont-init scripts. /etc/services.d/<name>/
# is the long-running supervised service shape; /etc/cont-init.d/<n>-name
# scripts run once at boot, in numeric order, before any service starts.
COPY docker/s6/services/ /etc/services.d/
COPY docker/s6/cont-init.d/ /etc/cont-init.d/

# Default kairix path env vars use the FHS layout (was /data/kairix on the
# pre-Plan-2 image). The s6 cont-init script checks /var/lib/kairix/index.sqlite
# for first-boot detection, so these must line up.
ENV KAIRIX_CONTAINER=1 \
    S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    KAIRIX_DB_PATH=/var/lib/kairix/index.sqlite \
    KAIRIX_DOCUMENT_ROOT=/var/lib/kairix/documents \
    KAIRIX_REFLIB_ROOT=/opt/kairix/reference-library \
    KAIRIX_WORKSPACE_ROOT=/var/lib/kairix/workspaces \
    KAIRIX_DATA_DIR=/var/lib/kairix \
    KAIRIX_CACHE_DIR=/var/cache/kairix \
    KAIRIX_CONFIG_PATH=/etc/kairix/kairix.config.yaml \
    KAIRIX_EVAL_CORPORA_ROOT=/opt/kairix/reference-library/conversations \
    KAIRIX_PERF_BUDGETS=/opt/kairix/suites/perf/budgets.json \
    TMPDIR=/var/cache/kairix/tmp

USER kairix
WORKDIR /var/lib/kairix

# Healthcheck mirrors the kairix /healthz/ready endpoint served by
# `kairix mcp serve` (s6 supervises that process, see /etc/services.d/kairix-api).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz/ready || exit 1

EXPOSE 8080

# s6's /init becomes pid 1: runs /etc/cont-init.d/* once, then supervises
# every /etc/services.d/<name>/run script. Forwards SIGTERM to children,
# reaps zombies, exits when all services exit.
ENTRYPOINT ["/init"]
