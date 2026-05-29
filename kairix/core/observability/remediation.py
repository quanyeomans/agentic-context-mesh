"""Remediation registry for the pipeline observability surface (ADR-025 Pattern C).

Every non-OK status code maps to a :class:`Remediation` carrying the
F21 affordance template (``fix:`` / ``next:`` / ``run:``) plus an
``agent_action`` line that flows into the search-result ``provenance``
block in Phase 2 (Pattern E).

One source of truth — the same remediation appears in dead-letter
rows, agent search-result envelopes, and CLI failure output.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairix.core.observability.status_codes import StatusCode

_RUN_REEXTRACT = "kairix worker reextract --item <item_id>"
_RUN_REEXTRACT_OCR = "kairix worker reextract --item <item_id> --extractor markitdown+ocr"
_RUN_INSPECT = "kairix worker inspect <source_name> <item_id>"


@dataclass(frozen=True)
class Remediation:
    """F21-shaped remediation for a status code.

    ``agent_action`` is the runtime-facing message that flows into search
    result envelopes (Phase 2). Use ``<source_uri>`` / ``<item_id>`` as
    interpolation placeholders — callers substitute at lookup time.
    """

    fix: str
    next: str
    run: str
    agent_action: str


REMEDIATION: dict[StatusCode, Remediation] = {
    StatusCode.FETCH_TIMEOUT: Remediation(
        fix="Source-side timeout during fetch. Transient — most often network or upstream load.",
        next="Apply this pattern: auto-reextract once self-healing is enabled (Phase 3).",
        run=_RUN_REEXTRACT,
        agent_action=(
            "Source is reachable in principle; this is a transient infra issue. Inspect <source_uri> directly."
        ),
    ),
    StatusCode.FETCH_THROTTLED: Remediation(
        fix="Source returned 429/503 with Retry-After. The connector honoured the wait.",
        next="No operator action required; the connector retries on the next tick.",
        run="kairix worker status-summary --code FETCH_THROTTLED --since 1h",
        agent_action=(
            "Source throttled the read. Re-run your query in a few minutes; "
            "if it persists, inspect <source_uri> directly."
        ),
    ),
    StatusCode.FETCH_NOT_FOUND: Remediation(
        fix="Source returned 404. The upstream document was deleted or moved.",
        next="Apply this pattern: mark the item archived once confirmed gone.",
        run="kairix worker archive --item <item_id>",
        agent_action="Source has been deleted upstream. Treat any cached chunk as stale.",
    ),
    StatusCode.FETCH_FORBIDDEN: Remediation(
        fix="Source returned 403. Access was revoked for this connector's identity.",
        next="Reconcile permissions at the source (admin grants, OAuth scope, group membership).",
        run="kairix worker probe --connector <connector_id>",
        agent_action="Access was lost. The cached chunks are the last visible state.",
    ),
    StatusCode.FETCH_ZERO_BYTES: Remediation(
        fix="Source returned an empty body. Item exists but carries no extractable content.",
        next="Apply this pattern: flag for human review or auto-archive based on policy.",
        run=_RUN_INSPECT,
        agent_action="The source document is empty. Inspect <source_uri> directly to confirm intent.",
    ),
    StatusCode.EXTRACT_OK_EMPTY: Remediation(
        fix="Extractor ran but produced empty markdown — typical for scanned PDFs without OCR.",
        next="Retry with the OCR-enabled extractor variant if the source carries embedded images.",
        run=_RUN_REEXTRACT_OCR,
        agent_action=(
            "This chunk is a stub — extractor produced no text. Inspect <source_uri> directly for the full document."
        ),
    ),
    StatusCode.EXTRACT_OUTPUT_EMPTY: Remediation(
        fix="Extractor returned empty output (silent-drop class). Bronze recorded the hash but silver wrote nothing.",
        next="Diagnose with a reextract under the OCR variant; classify the resulting code.",
        run=_RUN_REEXTRACT_OCR,
        agent_action=(
            "This chunk is a stub. Inspect <source_uri> directly; "
            "if you need the full text, request a reextract via operator."
        ),
    ),
    StatusCode.EXTRACT_UNSUPPORTED_MIME: Remediation(
        fix="No extractor matched the source MIME type.",
        next="Add an extractor plugin for the format, or exclude the path in the connector config.",
        run="kairix worker status-summary --code EXTRACT_UNSUPPORTED_MIME --since 7d",
        agent_action="Source format is not supported by current extractors. Inspect <source_uri> directly.",
    ),
    StatusCode.EXTRACT_DISK_FULL: Remediation(
        fix="Worker container hit ENOSPC during extraction (transient infra issue).",
        next="Confirm disk headroom; auto-reextract once self-healing is enabled (Phase 3).",
        run=_RUN_REEXTRACT,
        agent_action="Source is reachable; this was a transient infra issue. Inspect <source_uri> directly.",
    ),
    StatusCode.EXTRACT_MISSING_DEPS: Remediation(
        fix="Extractor raised on a missing optional dependency (e.g. markitdown extras).",
        next="Rebuild the container with the missing extras; auto-reextract once dependencies are in place.",
        run=_RUN_REEXTRACT,
        agent_action=(
            "The extractor needs additional support to handle this format. "
            "Inspect <source_uri> directly while it's resolved."
        ),
    ),
    StatusCode.EXTRACT_CORRUPT_INPUT: Remediation(
        fix="Extractor raised on malformed input bytes — source file is likely corrupt.",
        next="Inspect the source document; if corruption is real, exclude or archive the item.",
        run=_RUN_INSPECT,
        agent_action="Source document appears corrupt. Inspect <source_uri> directly to confirm.",
    ),
    StatusCode.SILVER_PRUNED_BY_MAINTENANCE: Remediation(
        fix="Maintenance orphan-cleanup removed the content row.",
        next="Phase 3 prune guard prevents this for active bronze items; until then, retrigger silver via reextract.",
        run=_RUN_REEXTRACT,
        agent_action="This chunk was removed by routine maintenance. A reextract will restore it.",
    ),
    StatusCode.SILVER_NO_CHUNKS_WRITTEN: Remediation(
        fix="Silver chunker received empty markdown (paired with EXTRACT_OUTPUT_EMPTY).",
        next="Address at the extract stage — silver behaves correctly given empty input.",
        run=_RUN_REEXTRACT_OCR,
        agent_action="This chunk is a stub — extractor produced no text. Inspect <source_uri> directly.",
    ),
    StatusCode.EMBED_RATE_LIMITED: Remediation(
        fix="Embedding provider returned 429. The embed pipeline honoured the wait.",
        next="No operator action required; the embed retries on the next tick.",
        run="kairix worker status-summary --code EMBED_RATE_LIMITED --since 1h",
        agent_action="Embedding deferred briefly. Re-run your query in a few minutes.",
    ),
    StatusCode.ENTITY_DRAIN_FAILED: Remediation(
        fix="Neo4j write rejected the entity signal — Neo4j unreachable or schema mismatch.",
        next="Inspect Neo4j connectivity; retry-eligible once the connection recovers.",
        run=_RUN_INSPECT,
        agent_action="Entity-graph view of this item is stale. Vector + BM25 results still reflect the latest content.",
    ),
    StatusCode.INFERRED_SILENT_DROP: Remediation(
        fix="Pre-Phase-1 backfill: bronze recorded the item but no silver content landed and no error was captured.",
        next="Phase 3 diagnostic retry assigns a real status code; until then, this is a known gap.",
        run=_RUN_REEXTRACT,
        agent_action="This item's processing trail is incomplete. Inspect <source_uri> directly while diagnosis runs.",
    ),
    StatusCode.INFERRED_FROM_DEAD_LETTER: Remediation(
        fix="Pre-Phase-1 backfill: parsed from connector_deadletter free-text error.",
        next="The real status code is captured on the next reextract.",
        run=_RUN_REEXTRACT,
        agent_action="See the inferred status detail for the parsed error class. Inspect <source_uri> for the source.",
    ),
    StatusCode.PIPELINE_STAGE_NO_EMIT: Remediation(
        fix="A pipeline stage exited without calling emit (P1 fail-safe; should not occur once F74 is paid down).",
        next="File a bug with the stage name + item_id; F74 ban should have caught this at pre-commit.",
        run=_RUN_INSPECT,
        agent_action="Pipeline observability gap — operator-only. The chunk content remains usable.",
    ),
}
