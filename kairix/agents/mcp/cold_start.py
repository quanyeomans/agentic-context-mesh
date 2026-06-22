"""Cold-start affordances for the Kairix MCP surface.

The MCP server can be alive before the retrieval stack is warm. LLM agents are
not reliable retry engines, so cold-start must be encoded as a mechanical,
retryable state rather than prose hidden in a generic error string.

The end-to-end three-layer contract (HTTP 503 + Retry-After at the transport,
this application-layer envelope, and the structured startup-log events that
operators pivot on for restart frequency) is documented in
``docs/operations/MCP-DEPLOYMENT.md`` under "Cold-start affordance contract".
Agent-side retry guidance ("If you see fetch_failed from kairix") lives in
``docs/user-guide/agent-usage-guide.md`` — the searchable corpus the
``kairix onboard guide`` installer ships into operator vaults. The
v2026.5.18 upgrade notes at ``docs/upgrades/v2026.5.18.md`` carry the
historical context for the readiness gate introduction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kairix.platform.warm.state import WarmProgress

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
    warm_progress: WarmProgress | None = None,
) -> dict[str, Any]:
    """Return the canonical Kairix MCP cold-start envelope.

    This shape is intentionally redundant. Different agents attend to different
    fields; the stable machine fields (``status`` / ``error_code`` /
    ``retry_after_ms``) are paired with a direct instruction that forbids
    fallback-from-memory behaviour.

    When ``warm_progress`` is provided (a live
    :class:`kairix.platform.warm.state.WarmProgress` snapshot), the envelope
    surfaces the real ``elapsed_seconds`` and ``estimated_seconds_remaining``
    so agents back off for the actual remaining time rather than the static
    8s default that pre-dated #390. When ``warm_progress`` is ``None`` (warm
    has not started), the static kwargs are returned unchanged for backward
    compatibility.
    """

    elapsed_seconds: float | None = None
    if warm_progress is not None:
        elapsed_seconds = round(warm_progress.elapsed_seconds(), 3)
        estimated_seconds_remaining = round(warm_progress.remaining_seconds(), 3)
        retry_after_ms = max(1000, int(estimated_seconds_remaining * 1000))

    retry_seconds = max(1.0, retry_after_ms / 1000)
    payload: dict[str, Any] = {
        "status": "retryable_not_ready",
        "error": "ColdStart",
        "error_code": "KAIRIX_COLD_START",
        "tool": tool_name,
        "retry_after_ms": retry_after_ms,
        "estimated_seconds_remaining": estimated_seconds_remaining,
        "guidance": (
            f"kairix is warming (one-time cost per process). "
            f"next: wait ~{retry_seconds:.0f}s and retry — subsequent calls in this process return immediately."
        ),
        "agent_instruction": (
            f"next: pause retry_after_ms then call {tool_name!r} again. "
            f"fix: if the second call still returns ColdStart, surface "
            f'"kairix still warming after ~{retry_seconds:.0f}s" to the user and ask whether to proceed '
            f"without retrieval — this is a transient process-boot state, not a hard failure."
        ),
        "see_also": ["docs/operations/MCP-DEPLOYMENT.md"],
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = elapsed_seconds
    return payload


def is_cold_start_envelope(payload: Any) -> bool:
    """Return True when ``payload`` is the canonical cold-start envelope."""

    return isinstance(payload, dict) and payload.get("error_code") == "KAIRIX_COLD_START"


def require_ready(
    tool_name: str,
    readiness_check: Callable[[], bool] | None,
    *,
    config: ColdStartConfig | None = None,
    warm_progress_source: Callable[[], WarmProgress | None] | None = None,
) -> dict[str, Any] | None:
    """Return a cold-start envelope when readiness_check says not-ready.

    ``None`` means the caller may proceed with the real tool implementation.

    ``warm_progress_source`` is the public DI seam: tests inject a fake
    returning a fixed :class:`WarmProgress` so they can assert the live
    remaining-seconds path without touching module globals. Production
    callers leave it ``None`` and the default lazy-import of
    :func:`kairix.platform.warm.state.get_warm_progress` fires.
    """

    if readiness_check is None or readiness_check():
        return None
    cfg = config or ColdStartConfig()
    progress = _resolve_warm_progress(warm_progress_source)
    return cold_start_envelope(
        tool_name=tool_name,
        retry_after_ms=cfg.retry_after_ms,
        estimated_seconds_remaining=cfg.estimated_seconds_remaining,
        warm_progress=progress,
    )


def _resolve_warm_progress(source: Callable[[], WarmProgress | None] | None) -> WarmProgress | None:
    """Return the live WarmProgress snapshot, or ``None`` when warm hasn't started.

    Default path lazy-imports ``get_warm_progress`` so the cold_start module
    stays importable without the platform.warm package being loaded (the
    transport layer imports cold_start at module top-level).
    """
    if source is not None:
        return source()
    from kairix.platform.warm.state import get_warm_progress

    return get_warm_progress()


def _default_build_search_pipeline() -> Any:
    """Production-default pipeline factory — lazy-imports the real builder.

    Lazy so the cold_start module stays importable without the heavy
    ``kairix.core.factory`` graph being loaded at transport-layer import
    time (the transport imports cold_start at module top-level).
    """
    from kairix.core.factory import build_search_pipeline

    return build_search_pipeline()


@dataclass
class WarmStackDeps:
    """Injectable seam for :func:`warm_retrieval_stack`.

    Canonical kairix Deps shape (F6-exempt — fields live on a ClassDef
    with ``field(default=...)`` per CLAUDE.md's Deps-pattern rule; the
    same shape as :class:`kairix.core.curator.drain.Neo4jDrainTickDeps`).
    Production callers leave ``deps`` as ``None`` and the function binds
    :func:`_default_build_search_pipeline`, which constructs the real
    :class:`~kairix.core.search.pipeline.SearchPipeline`. Unit tests pass
    a ``WarmStackDeps(pipeline_factory=lambda: FakeSearchPipeline(...))``
    so the warm-up orchestration (build step record, read-only probe,
    success / probe-failure / build-failure envelope assembly, and the
    elapsed-time accounting) is exercised without provider secrets, a KV
    mount, or a live retrieval backend — none of which exist in unit
    scope. Replaces the rejected ``# pragma`` / E2E-only grandfather:
    the orchestration is now F1-clean unit-reachable, not integration-
    or memory-asserted.

    Attributes:
        pipeline_factory: zero-arg callable returning an object with a
            ``search(query=, budget=, collections=)`` method. Defaults to
            :func:`_default_build_search_pipeline`. A factory that raises
            drives the build-failure branch; a returned pipeline whose
            ``search`` raises drives the probe-failure branch.
    """

    pipeline_factory: Callable[[], Any] = field(default=_default_build_search_pipeline)


def warm_retrieval_stack(deps: WarmStackDeps | None = None) -> dict[str, Any]:
    """Pay the expensive retrieval initialisation cost once.

    This function deliberately constructs the production search pipeline and runs
    a tiny read-only probe. It should be called by long-running HTTP deployments
    before advertising readiness, and can also be exposed as a manual ``warm``
    MCP tool.

    ``deps`` is the public DI seam (default ``None`` → production
    factories bind). Tests inject a :class:`WarmStackDeps` with a fake
    ``pipeline_factory`` to exercise the success and probe-failure
    orchestration branches at unit scope without provider config.
    """

    import time

    resolved = deps or WarmStackDeps()
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []

    try:
        step_started = time.perf_counter()
        pipeline = resolved.pipeline_factory()
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
