"""Unit tests for the collection-path resolution check in
``kairix.core.search.config_validator.validate_config``.

Regression coverage for the bug class hit on 2026-05-31: an operator's
``kairix.config.yaml`` declared ``path: reference-library`` (relative),
which the scanner resolved as ``$KAIRIX_DOCUMENT_ROOT/reference-library``
and silently warned about at every scan tick. The validator now turns
this into a hard validate-time error with F21-actionable remediation
text — operators see the misconfiguration before deploy, not in worker
logs hours later.

F2-clean: tests pass ``document_root`` / ``reflib_root`` explicitly via
the validate_config kwargs instead of mutating process env.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.search.config_validator import validate_config

pytestmark = pytest.mark.unit


def test_skip_when_document_root_kwarg_points_at_nonexistent_path(tmp_path: Path) -> None:
    """document_root is set but the path doesn't exist → silent skip (test shell)."""
    cfg = {
        "collections": {
            "shared": [
                {"name": "reference-library", "path": "reference-library"},
            ],
        },
    }
    errors = validate_config(cfg, document_root=tmp_path / "not-real", reflib_root=tmp_path / "not-real-reflib")
    assert errors == [], f"expected no errors when document_root doesn't exist; got {errors}"


def test_relative_path_that_doesnt_resolve_is_an_error(tmp_path: Path) -> None:
    docroot = tmp_path / "docs"
    docroot.mkdir()
    cfg = {
        "collections": {
            "shared": [
                {"name": "my-notes", "path": "my-notes"},
            ],
        },
    }
    errors = validate_config(cfg, document_root=docroot, reflib_root=tmp_path / "not-real-reflib")
    assert any("my-notes" in e and "does not exist" in e for e in errors), (
        f"expected a path-doesnt-exist error for 'my-notes'; got {errors}"
    )
    assert any("fix:" in e for e in errors), f"error must carry F21 'fix:' affordance; got {errors}"


def test_reference_library_gets_auto_correct_hint(tmp_path: Path) -> None:
    docroot = tmp_path / "docs"
    docroot.mkdir()
    reflib = tmp_path / "reflib"
    reflib.mkdir()
    cfg = {
        "collections": {
            "shared": [
                {"name": "reference-library", "path": "reference-library"},
            ],
        },
    }
    errors = validate_config(cfg, document_root=docroot, reflib_root=reflib)
    assert any("reference-library" in e and "auto-corrects" in e and str(reflib) in e for e in errors), (
        f"expected reference-library to get the auto-correct hint pointing at {reflib}; got {errors}"
    )


def test_absolute_path_that_exists_passes(tmp_path: Path) -> None:
    docroot = tmp_path / "docs"
    docroot.mkdir()
    real_collection = tmp_path / "my-corpus"
    real_collection.mkdir()
    cfg = {
        "collections": {
            "shared": [
                {"name": "my-corpus", "path": str(real_collection)},
            ],
        },
    }
    errors = validate_config(cfg, document_root=docroot, reflib_root=tmp_path / "not-real-reflib")
    assert errors == [], f"expected no errors for absolute path that exists; got {errors}"


def test_relative_path_under_document_root_passes(tmp_path: Path) -> None:
    docroot = tmp_path / "docs"
    docroot.mkdir()
    (docroot / "my-corpus").mkdir()
    cfg = {
        "collections": {
            "shared": [
                {"name": "my-corpus", "path": "my-corpus"},
            ],
        },
    }
    errors = validate_config(cfg, document_root=docroot, reflib_root=tmp_path / "not-real-reflib")
    assert errors == [], f"expected no errors for valid relative path; got {errors}"
