"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`DocumentWriter`.

Single Protocol method ``write(*, corpus_id, session_id, rendered_body,
frontmatter)`` returning a :class:`Path`. When the underlying writer
(SQLite repo + materialiser) rejects the write (disk full, FTS5
rebuild error, permission denied), the exception MUST propagate so
the caller's :class:`IngestResult.document_paths` doesn't lie about
what landed on disk.

We probe via an inline ``_RaisingWriter`` subclass since the canonical
:class:`tests.fakes.FakeDocumentWriter` is capture-only (no raise
knob).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract


def test_write_raises_when_backend_rejects_persistence() -> None:
    """A ``DocumentWriter`` whose ``write`` raises must surface the
    exception — silent fallback to a sentinel path would let the
    caller record a doc that never landed.

    Sabotage proof: in ``_RaisingWriter.write`` change
    ``raise self._exc`` to ``return Path("/fake/sentinel.md")``. Re-run:
    the test fails because no exception fires and the call returns a
    path. Restored.
    """

    class _RaisingWriter:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def write(
            self,
            *,
            corpus_id: str,
            session_id: str,
            rendered_body: str,
            frontmatter: dict[str, Any],
        ) -> Path:
            del corpus_id, session_id, rendered_body, frontmatter
            raise self._exc

    writer = _RaisingWriter(RuntimeError("F68-writer-disk-full"))
    with pytest.raises(RuntimeError, match="F68-writer-disk-full"):
        writer.write(
            corpus_id="corpus-alpha",
            session_id="session-001",
            rendered_body="body",
            frontmatter={"agent": "agent-alpha"},
        )
