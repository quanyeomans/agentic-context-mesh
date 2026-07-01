"""MCP tool — ``ingest_chat``: agent-driven conversation ingest.

Wraps the :func:`kairix.use_cases.ingest_chat.ingest_chat` use case so an
agent can drive the conversation-paradigm ingest pipeline itself: the
agent posts a JSONL-shaped transcript body + ``conversation_id`` +
``namespace``; the tool writes the transcript to a tmp file inside the
agent's workspace, calls the use case, and returns the
:class:`IngestChatResult` counts as a flat dict.

Namespace contract:

- Every call MUST specify ``namespace``. The tool rejects calls that
  carry a namespace different from the agent-context's
  ``allowed_namespace`` (cross-engagement isolation). Production wires
  ``allowed_namespace`` from the agent's session state; tests inject
  any value via the ``allowed_namespace`` kwarg.

Dependency injection:

- ``paths`` / ``fact_store`` / ``fact_extractor`` are constructor-
  injected on every call so the tool is F1-clean. Production callers
  rely on the defaults (resolved KairixPaths + production FactStore /
  FactExtractor); tests pass fakes from ``tests/fakes.py``.

Errors:

- Returns a flat ``{"error": "<Name>", ...}`` envelope rather than
  raising — agents read the ``error`` key to decide whether the call
  succeeded.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from pathlib import Path
from typing import Any

from kairix.core.protocols import FactExtractor, FactStore
from kairix.paths import KairixPaths
from kairix.use_cases.ingest_chat import ingest_chat

logger = logging.getLogger(__name__)

__all__ = ["tool_ingest_chat"]


# Canonical error keys — agents read these to branch on failure mode.
ERROR_CROSS_ENGAGEMENT = "CrossEngagementNamespace"
ERROR_INVALID_INPUT = "InvalidInput"
ERROR_INGEST_FAILED = "IngestFailed"


def _failure_envelope(
    *,
    error: str,
    detail: str,
    namespace: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Build a zero-counts failure envelope.

    Centralises the "all counts at zero" shape so a future field rename
    has one edit site (F17 affordance) and so the envelope's contract
    stays in lockstep with the success shape built in ``tool_ingest_chat``.
    """
    return {
        "error": error,
        "detail": detail,
        "turns_ingested": 0,
        "facts_added": 0,
        "windows_extracted": 0,
        "facts_superseded": 0,
        "conversations_processed": 0,
        "namespace": namespace,
        "conversation_id": conversation_id,
    }


def _write_tmp_jsonl(workspace_root: Path, conversation_id: str, jsonl_content: str) -> Path:
    """Write the agent-supplied JSONL body to a fresh tmp file under workspace.

    Path shape: ``<workspace_root>/ingest_chat/<conversation_id>-<uuid>.jsonl``.
    The uuid suffix lets the same conversation_id be re-ingested without
    overwriting the prior tmp artefact (helpful for post-mortem debugging).
    """
    tmp_dir = workspace_root / "ingest_chat"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_id = conversation_id.replace("/", "_").replace("\\", "_") or "conversation"
    target = tmp_dir / f"{safe_id}-{uuid.uuid4().hex[:8]}.jsonl"
    target.write_text(jsonl_content, encoding="utf-8")
    return target


def tool_ingest_chat(
    jsonl_content: str,
    conversation_id: str,
    namespace: str,
    *,
    allowed_namespace: str,
    paths: KairixPaths | None = None,
    fact_store: FactStore | None = None,
    fact_extractor: FactExtractor | None = None,
    window_turns: int = 5,
    no_extract: bool = False,
    memory_fallback_root: Path | None = None,
) -> dict[str, Any]:
    """Ingest a JSONL chat transcript supplied inline by an agent.

    Parameters
    ----------
    jsonl_content:
        Raw JSONL — one line per turn. Empty string is rejected.
    conversation_id:
        Operator-supplied stable id for the conversation. Used as the
        tmp-file basename and as the ``default_conversation_id`` for
        turns that don't carry their own ``conversation_id`` field.
    namespace:
        Engagement-scope namespace the agent is requesting. MUST equal
        ``allowed_namespace`` or the call is rejected with the
        ``CrossEngagementNamespace`` envelope.
    allowed_namespace:
        Constructor-injected agent-scope namespace. Production callers
        derive this from the agent's session state; tests pass any
        value to exercise both the accept and reject branches.
    paths / fact_store / fact_extractor:
        Optional DI seams — production callers leave them ``None`` and
        the tool resolves real implementations. Tests inject fakes.
    window_turns:
        Sliding-window size for fact extraction. Default 5 mirrors the
        use case default.
    no_extract:
        Skip fact extraction entirely (chunks-only mode).
    memory_fallback_root:
        PLA-296 test seam — the writable data-dir base used when the
        ``04-Agent-Knowledge`` overlay is read-only. Production leaves it
        ``None`` (resolves the real data dir); tests pin it under ``tmp_path``.

    Returns
    -------
    dict
        Success: ``{"turns_ingested", "facts_added", "windows_extracted",
        "facts_superseded", "conversations_processed", "namespace",
        "conversation_id", "error": ""}``.
        Failure: ``{"error": "<Name>", "detail": "...", ...}`` — never
        raises out of the tool body.
    """
    if not jsonl_content:
        return _failure_envelope(
            error=ERROR_INVALID_INPUT,
            detail="jsonl_content was empty",
            namespace=namespace,
            conversation_id=conversation_id,
        )
    if not conversation_id:
        return _failure_envelope(
            error=ERROR_INVALID_INPUT,
            detail="conversation_id was empty",
            namespace=namespace,
            conversation_id=conversation_id,
        )
    if namespace != allowed_namespace:
        # Agents are pinned to their session's engagement namespace; any
        # cross-engagement call is rejected. Reveals neither the allowed
        # value nor any internal state — just the rejection.
        return _failure_envelope(
            error=ERROR_CROSS_ENGAGEMENT,
            detail=(
                "Requested namespace does not match the agent's session namespace. "
                "fix: pass the namespace the agent was bootstrapped with."
            ),
            namespace=namespace,
            conversation_id=conversation_id,
        )

    resolved_paths = paths if paths is not None else KairixPaths.resolve()

    # Resolve fact_store / fact_extractor lazily — keep the production
    # heavy imports off the module-level path so test runs that pass
    # fakes don't pay for SQLite/LLM stack imports.
    if fact_store is None:
        from kairix.core.facts import SQLiteFactStore

        fact_store = SQLiteFactStore(db_path=resolved_paths.db_path)
    if fact_extractor is None:
        from kairix.core.facts import LLMFactExtractor
        from kairix.platform.llm import get_default_backend

        fact_extractor = LLMFactExtractor(llm=get_default_backend())

    try:
        jsonl_path = _write_tmp_jsonl(resolved_paths.workspace_root, conversation_id, jsonl_content)
    except OSError as exc:
        logger.warning("tool_ingest_chat: failed to write tmp jsonl: %s", exc)
        return _failure_envelope(
            error=ERROR_INGEST_FAILED,
            detail=f"failed to stage transcript on disk: {type(exc).__name__}",
            namespace=namespace,
            conversation_id=conversation_id,
        )

    try:
        result = ingest_chat(
            jsonl_path,
            paths=resolved_paths,
            fact_store=fact_store,
            fact_extractor=fact_extractor,
            namespace=namespace,
            window_turns=window_turns,
            no_extract=no_extract,
            memory_fallback_root=memory_fallback_root,
        )
    except OSError as exc:
        # Conversations write to {document_root}/04-Agent-Knowledge/conversations
        # — the writable submount on the stock compose (PLA-275). If even that
        # is read-only (misconfigured / missing writable mount), hand the agent
        # an actionable envelope rather than letting the OSError crash the call.
        logger.warning("tool_ingest_chat: ingest_chat hit a filesystem error: %s", exc, exc_info=True)
        return _failure_envelope(
            error=ERROR_INGEST_FAILED,
            detail=(
                f"could not write the conversation under the agent-knowledge area ({type(exc).__name__}). "
                "fix: mount 04-Agent-Knowledge writable — on the standard compose the document root is "
                "read-only and 04-Agent-Knowledge is the one writable submount kairix writes to. "
                "next: `mkdir -p documents/04-Agent-Knowledge` and grant the kairix container user "
                "write access before `docker compose up`."
            ),
            namespace=namespace,
            conversation_id=conversation_id,
        )
    except (ValueError, RuntimeError) as exc:
        logger.warning("tool_ingest_chat: ingest_chat raised: %s", exc, exc_info=True)
        return _failure_envelope(
            error=ERROR_INGEST_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            namespace=namespace,
            conversation_id=conversation_id,
        )

    envelope: dict[str, Any] = dataclasses.asdict(result)
    envelope["namespace"] = namespace
    envelope["conversation_id"] = conversation_id
    envelope["error"] = ""
    return envelope
