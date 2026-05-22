"""F40 detector tests -- every Extractor plugin declares version: str.

The F40 detector (``scripts/checks/check_f40_extractor_version.py``)
walks ``kairix/extractors/<name>/__init__.py`` files and verifies each
plugin declares a non-empty module-level ``version: str`` attribute
AND a top-level ``make_extractor`` factory function. Schema drift
between extractor versions (ADR §5.6) is the failure mode the rule
closes — the version writes through to
``documents_media.extractor_version`` so re-extract sweeps can target
stale derivatives.

Sabotage proof for the version check: temporarily mutate
``_has_version_declaration`` to always return ``True``; the
"missing version" test below stops firing. Restoring the helper
makes it red again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from check_f40_extractor_version import init_has_violation  # noqa: E402

pytestmark = pytest.mark.unit


def _write_init(tmp_path: Path, plugin_name: str, content: str) -> Path:
    plugin_dir = tmp_path / "kairix" / "extractors" / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    init = plugin_dir / "__init__.py"
    init.write_text(content, encoding="utf-8")
    return init


def test_plugin_missing_version_is_violation(tmp_path: Path) -> None:
    """An ``__init__.py`` with a make_extractor factory but no ``version:
    str = "..."`` declaration is flagged."""
    init = _write_init(
        tmp_path,
        "foo",
        ("def make_extractor():\n    return object()\n"),
    )

    assert init_has_violation(init) is True


def test_plugin_with_version_and_factory_is_not_flagged(tmp_path: Path) -> None:
    """A canonical plugin (annotated version + make_extractor) passes."""
    init = _write_init(
        tmp_path,
        "markitdown",
        ('version: str = "0.1.4"\n\ndef make_extractor():\n    return object()\n'),
    )

    assert init_has_violation(init) is False


def test_plugin_with_bare_assign_version_is_accepted(tmp_path: Path) -> None:
    """The rule's intent is 'version is declared', not 'annotation is
    spelled' — a bare ``version = "0.1"`` also counts."""
    init = _write_init(
        tmp_path,
        "pdf_fallback",
        ('version = "0.1.0"\n\ndef make_extractor():\n    return object()\n'),
    )

    assert init_has_violation(init) is False


def test_plugin_with_empty_version_string_is_violation(tmp_path: Path) -> None:
    """An empty string literal is not a valid version — F40 flags it."""
    init = _write_init(
        tmp_path,
        "ocr",
        ('version: str = ""\n\ndef make_extractor():\n    return object()\n'),
    )

    assert init_has_violation(init) is True


def test_plugin_with_version_but_no_factory_is_violation(tmp_path: Path) -> None:
    """A version declaration alone is not enough — the make_extractor
    factory must also exist (§8 entry-point shape)."""
    init = _write_init(
        tmp_path,
        "vision",
        'version: str = "0.0.1"\n',
    )

    assert init_has_violation(init) is True


def test_unparseable_init_is_violation(tmp_path: Path) -> None:
    """A syntactically broken ``__init__.py`` is treated as missing both
    requirements — the plugin can't be loaded, so it has no version."""
    init = _write_init(tmp_path, "broken", "def make_extractor(:\n")

    assert init_has_violation(init) is True
