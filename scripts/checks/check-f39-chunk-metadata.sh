#!/usr/bin/env bash
# F39: Every chunk write must carry source_uri, source_modified_at, and sensitivity.
#
# See docs/architecture/connector-ingestion-architecture.md §6 (rule
# summary) and §7 (schema additions). The shape mirrors F15 — boundary
# enforcement at the write surface.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F39: a Chunk(...) constructor call is missing source_uri / source_modified_at / sensitivity.
The schema default for sensitivity is 'public' — an omitted kwarg silently demotes
confidential content into general search.

fix: pass source_uri=..., source_modified_at=..., sensitivity=... explicitly.
next: see connector-ingestion-architecture.md §7 (schema additions).
run: bash scripts/checks/check-f39-chunk-metadata.sh"

if ! python3 "${SCRIPT_DIR}/check_f39_chunk_metadata.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0
