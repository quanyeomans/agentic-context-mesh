"""Unit tests for ``harmonise_reference_library`` (issue #361).

The reference library ships inside the kairix container image at
``$KAIRIX_REFLIB_ROOT``. Operators who declare the collection in
``kairix.config.yaml`` for custom retrieval params often write
``path: reference-library`` (relative), which the scanner resolves
under ``$KAIRIX_DOCUMENT_ROOT`` and silently fails to find. This
helper reconciles the operator-declared entry with the bundled path
so the scan always lands on a path that exists.

Four cases covered:

  - Operator declared with broken relative path → auto-correct to the
    bundled path + emit actionable INFO line.
  - Operator declared with absolute correct path → preserved verbatim
    (operator's customisation wins).
  - No operator declaration → auto-append default entry (historic
    behaviour preserved).
  - No bundled reflib on disk → no-op (skip everything).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kairix.core.db.scanner import CollectionConfig
from kairix.core.embed.use_cases import (
    REFERENCE_LIBRARY_NAME,
    harmonise_reference_library,
)

pytestmark = pytest.mark.unit


def test_relative_bad_path_auto_corrected_to_reflib_root(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    document_root = tmp_path / "docs"
    document_root.mkdir()
    reflib_root = tmp_path / "reflib"
    reflib_root.mkdir()

    declared = [
        CollectionConfig(name=REFERENCE_LIBRARY_NAME, path="reference-library", glob="**/*.md"),
    ]

    with caplog.at_level(logging.INFO, logger="kairix.core.embed.use_cases"):
        result = harmonise_reference_library(declared, reflib_root, document_root)

    assert len(result) == 1
    assert result[0].name == REFERENCE_LIBRARY_NAME
    assert result[0].path == str(reflib_root)
    assert any("auto-correcting" in r.getMessage() and "fix:" in r.getMessage() for r in caplog.records), (
        "expected an F21-actionable INFO message with 'fix:' affordance"
    )


def test_absolute_good_path_preserved_no_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    document_root = tmp_path / "docs"
    document_root.mkdir()
    reflib_root = tmp_path / "reflib"
    reflib_root.mkdir()

    declared = [
        CollectionConfig(name=REFERENCE_LIBRARY_NAME, path=str(reflib_root), glob="**/*.md"),
    ]

    with caplog.at_level(logging.INFO, logger="kairix.core.embed.use_cases"):
        result = harmonise_reference_library(declared, reflib_root, document_root)

    assert len(result) == 1
    assert result[0].path == str(reflib_root)
    assert not any("auto-correcting" in r.getMessage() for r in caplog.records), (
        "expected no auto-correct log when operator path is valid"
    )


def test_no_declaration_appends_default(tmp_path: Path) -> None:
    document_root = tmp_path / "docs"
    document_root.mkdir()
    reflib_root = tmp_path / "reflib"
    reflib_root.mkdir()

    declared: list[CollectionConfig] = []

    result = harmonise_reference_library(declared, reflib_root, document_root)

    assert len(result) == 1
    assert result[0].name == REFERENCE_LIBRARY_NAME
    assert result[0].path == str(reflib_root)
    assert result[0].glob == "**/*.md"


def test_no_bundled_reflib_is_noop(tmp_path: Path) -> None:
    document_root = tmp_path / "docs"
    document_root.mkdir()
    missing_reflib = tmp_path / "nope-not-here"

    declared = [
        CollectionConfig(name=REFERENCE_LIBRARY_NAME, path="reference-library", glob="**/*.md"),
    ]

    result = harmonise_reference_library(declared, missing_reflib, document_root)

    assert len(result) == 1
    assert result[0].path == "reference-library", "should leave declaration untouched when no bundled reflib exists"


def test_only_reference_library_name_is_special_cased(tmp_path: Path) -> None:
    document_root = tmp_path / "docs"
    document_root.mkdir()
    reflib_root = tmp_path / "reflib"
    reflib_root.mkdir()

    declared = [
        CollectionConfig(name="my-other-collection", path="my-other-collection", glob="**/*.md"),
    ]

    result = harmonise_reference_library(declared, reflib_root, document_root)

    # Other collections are NOT auto-corrected — they fall through to the
    # scanner's existing path-missing handling.
    assert len(result) == 2, "expected reference-library auto-appended alongside the other collection"
    names = sorted(c.name for c in result)
    assert names == ["my-other-collection", REFERENCE_LIBRARY_NAME]
