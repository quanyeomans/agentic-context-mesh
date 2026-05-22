#!/usr/bin/env bash
# F40: Every Extractor plugin declares a version: str module attribute.
#
# See docs/architecture/connector-ingestion-architecture.md §3 (Extractor
# Protocol) and §5.6 (schema drift between extractor versions). The
# version is what 'documents_media.extractor_version' records per write;
# re-extract sweeps depend on it.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F40: an extractor plugin lacks a module-level version: str declaration
(or its make_extractor factory). Without it, derivatives can't be tracked across
extractor upgrades and re-extract sweeps lose their targeting signal.

fix: declare 'version: str = \"<semver-or-date>\"' in kairix/extractors/<name>/__init__.py.
next: see §3 (Extractor Protocol).
run: bash scripts/checks/check-f40-extractor-version.sh"

if ! python3 "${SCRIPT_DIR}/check_f40_extractor_version.py"; then
    printf '\n%s\n' "$REMEDIATION"
    exit 1
fi
exit 0
