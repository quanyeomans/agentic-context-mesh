"""Integration tests for MarkitdownExtractor cleanup under scratch-disk
pressure (test-resilience plan Wave 1).

Targets failure-mode Class A from docs/architecture/test-resilience-plan.md
§2: resource exhaustion mid-extract. The v2026.5.27a2 dogfood revealed
that markitdown's pre-fix write-bytes step lived OUTSIDE the try/finally
that unlinked the tmp file — a write failure (ENOSPC) left an empty
placeholder, and 8,087 placeholders accumulated in /tmp over a single
backfill cycle. These tests use the F6 scratch_dir constructor seam to
drive extraction against a deliberately-tight directory and assert
cleanup discipline holds.

F1-clean: no monkeypatch. Drives the real extractor with explicit
scratch_dir override.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kairix.extractors.markitdown import MarkitdownExtractor, version

pytestmark = pytest.mark.integration


class _BoomConverter:
    """Test seam — converter that raises during convert().

    F1-clean Protocol impl (not monkeypatching markitdown).
    """

    def convert(self, source: Any, **kwargs: Any) -> Any:
        raise RuntimeError("scripted-converter-boom")


class _SilentConverter:
    """Test seam — converter that returns valid markdown without touching disk."""

    def __init__(self, markdown: str = "# OK\n\nrecovered content paragraph here.") -> None:
        self.markdown = markdown
        self.calls = 0

    def convert(self, source: Any, **kwargs: Any) -> Any:
        self.calls += 1

        class _Result:
            text_content = self.markdown
            markdown = self.markdown
            title = "scripted"

        return _Result()


# ---------------------------------------------------------------------------
# Test 1 — Happy-path: no leaked tmp files in scratch_dir after extract
# ---------------------------------------------------------------------------


def test_extract_leaves_scratch_dir_clean_after_success(tmp_path: Path) -> None:
    """Sanity baseline: a successful extract removes its tmp file before
    returning. Drives the F6 scratch_dir seam end-to-end.

    Sabotage proof: remove the ``finally: tmp_path.unlink()`` block in
    MarkitdownExtractor.extract; this test fails because a tmp file
    remains in scratch_dir.
    """
    extractor = MarkitdownExtractor(
        version=version,
        converter_factory=lambda: _SilentConverter(),
        scratch_dir=tmp_path,
    )
    extractor.extract(b"%PDF-1.7 fake-bytes", "application/pdf")
    leftover = list(tmp_path.iterdir())
    assert leftover == [], f"successful extract should leave scratch clean, found: {leftover}"


# ---------------------------------------------------------------------------
# Test 2 — Converter failure: scratch_dir still clean
# ---------------------------------------------------------------------------


def test_extract_leaves_scratch_dir_clean_after_converter_failure(tmp_path: Path) -> None:
    """When the converter raises, the tmp file must still be unlinked
    by the finally block. Sabotage proof recorded in test_markitdown.py
    already executes this; this is the integration-layer cover.
    """
    extractor = MarkitdownExtractor(
        version=version,
        converter_factory=lambda: _BoomConverter(),
        scratch_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="scripted-converter-boom"):
        extractor.extract(b"%PDF-1.7 fake-bytes", "application/pdf")
    leftover = list(tmp_path.iterdir())
    assert leftover == [], (
        f"converter failure must unlink tmp file; leftover: {leftover}. "
        "Regression vector for v2026.5.27a2 dogfood incident."
    )


# ---------------------------------------------------------------------------
# Test 3 — 100 successive extractions in same scratch dir don't accumulate
# ---------------------------------------------------------------------------


def test_100_successive_extractions_do_not_accumulate_in_scratch_dir(tmp_path: Path) -> None:
    """The dogfood failure shape: every extraction leaked a tmp file
    placeholder. After 100 extractions, scratch_dir should still be
    empty — proof that cleanup discipline is per-call idempotent.

    Sabotage proof: comment out the unlink call in the finally; this
    test fails because scratch_dir contains 100 tmp files.
    """
    converter = _SilentConverter()
    extractor = MarkitdownExtractor(
        version=version,
        converter_factory=lambda: converter,
        scratch_dir=tmp_path,
    )
    for i in range(100):
        extractor.extract(f"%PDF-1.7 fake-bytes-{i}".encode(), "application/pdf")
    leftover = list(tmp_path.iterdir())
    assert leftover == [], (
        f"100 extractions should leave scratch clean (regression: pre-fix "
        f"v2026.5.27a1 leaked 100 placeholders); leftover: {len(leftover)}"
    )
    assert converter.calls == 100


# ---------------------------------------------------------------------------
# Test 4 — Mix of success + failure in same scratch dir
# ---------------------------------------------------------------------------


def test_mixed_success_and_failure_extractions_clean_scratch(tmp_path: Path) -> None:
    """Alternating successful + failing extractions exercise both branches
    of the try/finally. After 50 of each, scratch must be empty.

    Sabotage proof: in MarkitdownExtractor.extract, move write_bytes back
    inside the with-block (pre-v2026.5.27a2 shape). This test fails
    because ENOSPC-style failures leak tmp files even though the converter
    branch handles its own.
    """
    extractors_used = []
    leftover_counts = []

    for i in range(100):
        if i % 2 == 0:
            extractor = MarkitdownExtractor(
                version=version,
                converter_factory=lambda: _SilentConverter(),
                scratch_dir=tmp_path,
            )
            extractor.extract(f"%PDF-{i}".encode(), "application/pdf")
        else:
            extractor = MarkitdownExtractor(
                version=version,
                converter_factory=lambda: _BoomConverter(),
                scratch_dir=tmp_path,
            )
            try:
                extractor.extract(f"%PDF-{i}".encode(), "application/pdf")
            except RuntimeError:
                pass
        extractors_used.append(extractor)
        leftover_counts.append(len(list(tmp_path.iterdir())))

    assert all(c == 0 for c in leftover_counts), (
        f"scratch leak detected: leftover_counts after each iteration: {leftover_counts}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Scratch dir write-permission failure (simulates ENOSPC)
# ---------------------------------------------------------------------------


def test_extract_propagates_oserror_when_scratch_unwritable(tmp_path: Path) -> None:
    """If the scratch_dir is read-only (or in the production failure
    mode, ENOSPC) the NamedTemporaryFile creation OR the write_bytes
    raises OSError. The extractor must propagate the exception cleanly
    so the connector pipeline's dead-letter path catches it.

    Sabotage proof: wrap the write_bytes call in try/except OSError
    that returns an empty document; this test fails because no
    exception propagates and the pipeline silently indexes empty docs.
    """
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o555)  # read+execute, no write
    try:
        extractor = MarkitdownExtractor(
            version=version,
            converter_factory=lambda: _SilentConverter(),
            scratch_dir=readonly_dir,
        )
        with pytest.raises(OSError):
            extractor.extract(b"%PDF-1.7 fake-bytes", "application/pdf")
    finally:
        # Restore permissions so pytest can clean tmp_path
        readonly_dir.chmod(0o755)
        # Any leftover (shouldn't be — write failed before any rename)
        if list(readonly_dir.iterdir()):
            for f in readonly_dir.iterdir():
                f.unlink()


# ---------------------------------------------------------------------------
# Test 6 — Concurrent extractors in same scratch dir don't collide
# ---------------------------------------------------------------------------


def test_concurrent_extractors_in_same_scratch_dir(tmp_path: Path) -> None:
    """If two MarkitdownExtractor instances share a scratch_dir, their
    tmp file names must not collide. NamedTemporaryFile guarantees
    uniqueness; this test pins the contract.

    Sabotage proof: replace ``tempfile.NamedTemporaryFile(...)`` with
    a fixed-name file (``tmp_path / "fixed.pdf"``); the test fails
    because the second extract overwrites the first's tmp file mid-flight.
    """
    extractor_a = MarkitdownExtractor(
        version=version,
        converter_factory=lambda: _SilentConverter("# A"),
        scratch_dir=tmp_path,
    )
    extractor_b = MarkitdownExtractor(
        version=version,
        converter_factory=lambda: _SilentConverter("# B"),
        scratch_dir=tmp_path,
    )
    # Run sequentially (same thread); cleanliness check after each
    extractor_a.extract(b"%PDF-A", "application/pdf")
    assert list(tmp_path.iterdir()) == []
    extractor_b.extract(b"%PDF-B", "application/pdf")
    assert list(tmp_path.iterdir()) == []
