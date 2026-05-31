"""ADR-029 agent-facing query queue + carry-along delivery.

Public surface:

* :func:`dispatch_or_queue` — decorator that wraps an MCP tool handler so
  that fast calls (<= budget_seconds) return synchronously and slow calls
  return plain text ``"Processing your request (id: q_<hash>)..."`` while
  the handler runs on a background worker thread.
* :func:`carry_along_prefix` — middleware that reads completed
  pending_queries rows for an agent, marks them ``delivered``, and
  returns a formatted text prefix to prepend to the current tool
  response.
* :func:`reset_for_tests` — drop the cached executor + connection so
  tests can swap dependencies cleanly between cases.

This module implements G.1 of ADR-029. Wired only into ``tool_search``
in this wave; G.2 rolls the same pattern across the remaining MCP tools.

See ``docs/architecture/ADR-029-agent-query-queue-and-carry-along-delivery.md``
for the full design.
"""

from kairix.core.queue.carry_along import carry_along_prefix
from kairix.core.queue.dispatch import (
    PROCESSING_TEMPLATE,
    QUEUE_WORKER_MAX_WORKERS,
    dispatch_or_queue,
    reset_for_tests,
)

__all__ = [
    "PROCESSING_TEMPLATE",
    "QUEUE_WORKER_MAX_WORKERS",
    "carry_along_prefix",
    "dispatch_or_queue",
    "reset_for_tests",
]
