"""Unit tests for F34 (``scripts/checks/check_f34_core_connector_layer_imports.py``).

F34 forbids ``kairix/core/connectors/**`` from importing
``kairix/connectors/**`` or ``kairix/extractors/**`` — orchestration code
talks to the per-source connector and per-format extractor layers through
Protocols only.

Each test has an inline sabotage-proof: introduce a violation, confirm the
detector flags it; remove the violation, confirm the detector clears.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f34_core_connector_layer_imports.py"


def _load_detector():
    """Load the F34 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f34_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f34_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_real_repo_gate_is_green() -> None:
    """At landing the real F34 detector run against the full repo emits no
    violations — ``kairix/core/connectors/`` does not exist yet, so the
    check is vacuously green.
    """
    detector = _load_detector()
    assert detector.main() == 0


def test_core_connector_file_with_no_imports_passes(tmp_path: Path) -> None:
    """A core/connectors file that imports nothing forbidden is not flagged.

    Sabotage-proof inline: adding ``from kairix.connectors.obsidian import x``
    causes the detector to flag the file.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "pipeline.py"
    _write(target, '"""Orchestration module — pure."""\n')
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage.
    _write(target, "from kairix.connectors.obsidian import make_connector\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/connectors/pipeline.py") in violations


def test_core_connector_imports_protocol_passes(tmp_path: Path) -> None:
    """``from kairix.core.protocols import X`` is the seam — always allowed.

    Sabotage-proof inline: swap the import for ``kairix.extractors`` and the
    detector fires.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "silver.py"
    _write(target, "from kairix.core.protocols import SilverProcessor\n")
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage.
    _write(target, "from kairix.extractors.markitdown import MarkitdownExtractor\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/connectors/silver.py") in violations


def test_core_connector_imports_connectors_is_flagged(tmp_path: Path) -> None:
    """``from kairix.connectors.X import Y`` from inside core/connectors is
    rejected.

    Sabotage-proof inline: changing the import to a sibling
    ``kairix.core.protocols`` import clears the flag.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "registry.py"
    _write(target, "from kairix.connectors.sharepoint import make_connector\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/connectors/registry.py") in violations

    # Sabotage: replace with a legal core sibling import.
    _write(target, "from kairix.core.protocols import SourceConnector\n")
    assert detector.collect_violations(tmp_path) == set()


def test_core_connector_imports_extractors_via_plain_import_is_flagged(
    tmp_path: Path,
) -> None:
    """``import kairix.extractors.X`` form is also detected (not just
    ``from ... import ...``).

    Sabotage-proof inline: replace with a non-kairix import; the flag
    clears.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "boot.py"
    _write(target, "import kairix.extractors.markitdown\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/connectors/boot.py") in violations

    # Sabotage.
    _write(target, "import logging\n")
    assert detector.collect_violations(tmp_path) == set()


def test_missing_core_connectors_directory_passes(tmp_path: Path) -> None:
    """A fresh checkout where ``kairix/core/connectors/`` doesn't exist yet
    must not false-positive — F34 is a no-op until orchestration code
    appears.
    """
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()


def test_empty_core_connectors_directory_passes(tmp_path: Path) -> None:
    """``kairix/core/connectors/`` exists but contains no .py files —
    gate green.
    """
    detector = _load_detector()
    (tmp_path / "kairix" / "core" / "connectors").mkdir(parents=True)
    assert detector.collect_violations(tmp_path) == set()


def test_sibling_connectors_helpers_does_not_match_prefix(tmp_path: Path) -> None:
    """``kairix.connectors_helpers`` (hypothetical sibling, NOT under
    kairix/connectors/) must not trip the prefix match — the rule is
    anchored on the dotted boundary.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "core" / "connectors" / "x.py"
    _write(target, "from kairix.connectors_helpers import noop\n")
    assert detector.collect_violations(tmp_path) == set()


def test_remediation_carries_action_markers() -> None:
    """F34's own REMEDIATION must satisfy F21 — the agent reading a failure
    must get the correction action inline.
    """
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem
