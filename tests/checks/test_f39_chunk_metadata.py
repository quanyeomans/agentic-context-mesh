"""F39 detector tests -- every Chunk write carries the three required kwargs.

The F39 detector (``scripts/checks/check_f39_chunk_metadata.py``)
shape-matches ``Chunk(...)`` constructor callsites under ``kairix/`` and
verifies that ``source_uri``, ``source_modified_at``, and ``sensitivity``
are all passed as keyword arguments at the callsite. A missing kwarg
silently demotes confidential content to the schema default ('public'),
which is the failure mode the connector-ingestion ADR §5.7 calls out.

Sabotage proof for the kwarg check: drop ``source_uri`` from
``_REQUIRED_KWARGS`` in the detector → ``test_chunk_missing_all_kwargs_is_violation``
still red (the other two kwargs are absent) but
``test_chunk_missing_only_source_uri_is_violation`` flips green; restoring
the constant makes it red again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from check_f39_chunk_metadata import file_has_violation  # noqa: E402

pytestmark = pytest.mark.unit


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def test_chunk_missing_all_kwargs_is_violation(tmp_path: Path) -> None:
    """A synthetic Chunk(text='x') call with none of the three required
    metadata kwargs is flagged."""
    f = _write(
        tmp_path,
        "bad_chunk.py",
        'def emit():\n    return Chunk(text="x")\n',
    )

    assert file_has_violation(f) is True


def test_chunk_with_all_three_kwargs_is_not_flagged(tmp_path: Path) -> None:
    """All three required kwargs present at the callsite — no violation."""
    f = _write(
        tmp_path,
        "good_chunk.py",
        (
            "def emit():\n"
            "    return Chunk(\n"
            '        text="x",\n'
            '        source_uri="obsidian://note/1",\n'
            '        source_modified_at="2026-05-22T10:00:00Z",\n'
            '        sensitivity="internal",\n'
            "    )\n"
        ),
    )

    assert file_has_violation(f) is False


def test_chunk_missing_only_source_uri_is_violation(tmp_path: Path) -> None:
    """Even with two of the three kwargs, omitting one is a violation."""
    f = _write(
        tmp_path,
        "partial_chunk.py",
        (
            "def emit():\n"
            "    return Chunk(\n"
            '        text="x",\n'
            '        source_modified_at="2026-05-22T10:00:00Z",\n'
            '        sensitivity="internal",\n'
            "    )\n"
        ),
    )

    assert file_has_violation(f) is True


def test_kwargs_splat_is_conservatively_accepted(tmp_path: Path) -> None:
    """A ``**kwargs`` splat is treated as supplying every required kwarg —
    F39 only catches the obvious omission shape, not dynamic splats."""
    f = _write(
        tmp_path,
        "splat_chunk.py",
        'def emit(opts):\n    return Chunk(text="x", **opts)\n',
    )

    assert file_has_violation(f) is False


def test_unrelated_call_is_not_matched(tmp_path: Path) -> None:
    """``ChunkDateBoost(...)`` and other prefixes do NOT match — only the
    exact class name ``Chunk``."""
    f = _write(
        tmp_path,
        "other.py",
        "def emit():\n    return ChunkDateBoost(weight=1.0)\n",
    )

    assert file_has_violation(f) is False


def test_attribute_access_is_not_matched(tmp_path: Path) -> None:
    """``mod.Chunk(...)`` (Attribute access) is intentionally not matched
    — F26/F27 already block cross-layer construction by attribute access,
    so F39 protects only the in-package bare-name construction surface."""
    f = _write(
        tmp_path,
        "attr.py",
        'def emit():\n    return mod.Chunk(text="x")\n',
    )

    assert file_has_violation(f) is False
