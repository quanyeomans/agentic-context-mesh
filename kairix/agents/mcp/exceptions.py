"""Typed exception envelopes for the kairix MCP transport surface.

Downstream agent wrappers (kairix-context-bridge, kairix-memory-provider,
openclaw-plugin, third-party MCP clients) need a typed way to recognise
transport-level failures so they can compose a structured retryable
envelope back to the calling agent — instead of letting an opaque
``fetch failed`` string surface as a hard error.

The MCP server itself doesn't raise these exceptions on the request
path (its handlers return :func:`kairix.agents.mcp.cold_start.cold_start_envelope`
when the readiness gate is False, and the
:class:`~kairix.agents.mcp.transport.ColdStartMiddleware` returns HTTP
503 + ``Retry-After`` during the warm-up window). These exception
classes exist for wrapper-side recovery: catch the wrapper's transport
exception, map it onto :class:`TransportFetchFailedError`, then call
:func:`transport_fetch_failed_envelope` to compose the structured
response.

Issue tracking: https://github.com/three-cubes/kairix/issues/320
"""

from __future__ import annotations

from typing import Any


class TransportFetchFailedError(Exception):
    """Raised by wrappers when the MCP transport surface fails to return a structured response.

    Caught by agent-facing wrappers to convert into a retryable envelope
    via :func:`transport_fetch_failed_envelope`. The exception carries
    the originally-requested tool name so the envelope's ``tool`` field
    accurately reflects what the agent was reaching for.
    """

    def __init__(self, tool_name: str, *, cause: BaseException | None = None) -> None:
        super().__init__(f"transport fetch failed for tool {tool_name!r}")
        self.tool_name = tool_name
        self.cause = cause


def transport_fetch_failed_envelope(
    *,
    tool_name: str,
    retry_after_seconds: int = 8,
) -> dict[str, Any]:
    """Return the canonical TransportFetchFailed envelope.

    Use this from agent-facing wrappers when the underlying MCP transport
    returns an opaque error (HTTP fetch failed, socket reset, premature
    cancellation) before kairix's handler could emit a structured response.
    The shape mirrors :func:`kairix.agents.mcp.cold_start.cold_start_envelope`
    so an agent that already knows how to parse ColdStart sees a familiar
    structure here.

    Agents seeing this envelope should retry the same tool call after
    the suggested wait. If the second attempt still produces an opaque
    transport failure, escalate to the operator — the issue is no longer
    a transient cold-start window.
    """

    return {
        "status": "retryable_transport_failure",
        "error": "TransportFetchFailed",
        "error_code": "KAIRIX_TRANSPORT_FETCH_FAILED",
        "tool": tool_name,
        "retry_after_ms": retry_after_seconds * 1000,
        "estimated_seconds_remaining": float(retry_after_seconds),
        "guidance": (
            f"kairix transport returned an opaque error before the handler could respond. "
            f"next: wait ~{retry_after_seconds}s and retry — usually a cold-start race or "
            f"transient timeout that resolves on the second attempt."
        ),
        "agent_instruction": (
            f"next: pause {retry_after_seconds}s, retry the same tool. "
            f"fix: if the second attempt also returns TransportFetchFailed, surface "
            f'"kairix transport failing repeatedly" to the operator and ask whether to '
            f"proceed without retrieval."
        ),
        "see_also": ["https://github.com/three-cubes/kairix/issues/320"],
    }


__all__ = ["TransportFetchFailedError", "transport_fetch_failed_envelope"]
