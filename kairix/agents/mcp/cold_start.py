"""Cold-start affordances for the Kairix MCP surface.

The MCP server can be alive before the retrieval stack is warm. LLM agents are
not reliable retry engines, so cold-start must be encoded as a mechanical,
retryable state rather than prose hidden in a generic error string.

Operator-facing documentation for the readiness-gate flow lives in
``docs/operations/MCP-DEPLOYMENT.md`` and the v2026.5.18 upgrade notes at
``docs/upgrades/v2026.5.18.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_KEY_ELAPSED_MS = "elapsed_ms"

DEFAULT_RETRY_AFTER_MS = 8000
DEFAULT_ESTIMATED_SECONDS = 8.0


@dataclass(frozen=True)
class ColdStartConfig:
    """Settings for a retryable not-ready MCP response."""

    retry_after_ms: int = DEFAULT_RETRY_AFTER_MS
    estimated_seconds_remaining: float = DEFAULT_ESTIMATED_SECONDS


def cold_start_envelope(
    *,
    tool_name: str,
    retry_after_ms: int = DEFAULT_RETRY_AFTER_MS,
    estimated_seconds_remaining: float = DEFAULT_ESTIMATED_SECONDS,
) -> dict[str, Any]:
    """Return the canonical Kairix MCP cold-start envelope.

    This shape is intentionally redundant. Different agents attend to different
    fields; the stable machine fields (``status`` / ``error_code`` /
    ``retry_after_ms``) are paired with a direct instruction that forbids
    fallback-from-memory behaviour.
    """

    retry_seconds = max(1.0, retry_after_ms / 1000)
    return {
        "status": "retryable_not_ready",
        "error": "ColdStart",
        "error_code": "KAIRIX_COLD_START",
        "tool": tool_name,
        "retry_after_ms": retry_after_ms,
        "estimated_seconds_remaining": estimated_seconds_remaining,
        "guidance": (
            f"kairix is warming (one-time cost per process). Retry this {tool_name!r} "
            f"call in ~{retry_seconds:.0f} seconds. Subsequent calls in this process will be fast."
        ),
        "agent_instruction": (
            "Do not answer from memory, do not use a lower-quality fallback, and do not treat this as a "
            f"completed retrieval. Wait retry_after_ms, retry the same {tool_name!r} call once, then "
            "surface the cold-start blocker if it is still not ready."
        ),
        "see_also": ["docs/operations/MCP-DEPLOYMENT.md"],
    }


def is_cold_start_envelope(payload: Any) -> bool:
    """Return True when ``payload`` is the canonical cold-start envelope."""

    return isinstance(payload, dict) and payload.get("error_code") == "KAIRIX_COLD_START"


def require_ready(
    tool_name: str,
    readiness_check: Callable[[], bool] | None,
    *,
    config: ColdStartConfig | None = None,
) -> dict[str, Any] | None:
    """Return a cold-start envelope when readiness_check says not-ready.

    ``None`` means the caller may proceed with the real tool implementation.
    """

    if readiness_check is None or readiness_check():
        return None
    cfg = config or ColdStartConfig()
    return cold_start_envelope(
        tool_name=tool_name,
        retry_after_ms=cfg.retry_after_ms,
        estimated_seconds_remaining=cfg.estimated_seconds_remaining,
    )


def warm_retrieval_stack() -> dict[str, Any]:
    """Pay the expensive retrieval initialisation cost once.

    This function deliberately constructs the production search pipeline and runs
    a tiny read-only probe. It should be called by long-running HTTP deployments
    before advertising readiness, and can also be exposed as a manual ``warm``
    MCP tool.
    """

    import time

    started = time.perf_counter()
    steps: list[dict[str, Any]] = []

    try:
        step_started = time.perf_counter()
        from kairix.core.factory import build_search_pipeline

        pipeline = build_search_pipeline()
        steps.append(
            {
                "name": "build_search_pipeline",
                "ok": True,
                _KEY_ELAPSED_MS: int((time.perf_counter() - step_started) * 1000),
            }
        )
    except Exception as exc:
        return {
            "status": "error",
            "ready": False,
            _KEY_ELAPSED_MS: int((time.perf_counter() - started) * 1000),
            "steps": [
                *steps,
                {
                    "name": "build_search_pipeline",
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            ],
        }

    try:
        step_started = time.perf_counter()
        pipeline.search(query="kairix warmup", budget=200, collections=[])
        steps.append(
            {
                "name": "probe_search",
                "ok": True,
                _KEY_ELAPSED_MS: int((time.perf_counter() - step_started) * 1000),
            }
        )
    except Exception as exc:
        return {
            "status": "error",
            "ready": False,
            _KEY_ELAPSED_MS: int((time.perf_counter() - started) * 1000),
            "steps": [
                *steps,
                {
                    "name": "probe_search",
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            ],
        }

    return {
        "status": "ok",
        "ready": True,
        _KEY_ELAPSED_MS: int((time.perf_counter() - started) * 1000),
        "steps": steps,
    }
