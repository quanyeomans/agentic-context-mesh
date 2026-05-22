"""Unit tests for F35 (``scripts/checks/check_f35_no_cross_connector.py``).

F35 forbids ``kairix/connectors/<plugin>/**`` from importing another
connector under ``kairix/connectors/`` or any extractor under
``kairix/extractors/``. Connectors must stay independently shippable;
cross-plugin work goes through ``kairix/core/connectors/`` and extraction
goes through the Extractor Protocol via the registry.

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
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f35_no_cross_connector.py"


def _load_detector():
    """Load the F35 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f35_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f35_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_real_repo_gate_is_green() -> None:
    """At landing the real F35 detector run against the full repo emits no
    violations — ``kairix/connectors/`` does not exist yet, so the check
    is vacuously green.
    """
    detector = _load_detector()
    assert detector.main() == 0


def test_connector_with_no_cross_imports_passes(tmp_path: Path) -> None:
    """A connector importing only its own siblings and the shared base is
    not flagged.

    Sabotage-proof inline: add a sibling-connector import; the detector
    fires.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "sync.py"
    _write(
        target,
        "from kairix.connectors._base import SourceConnector\n"
        "from kairix.connectors.obsidian.client import build_client\n",
    )
    _write(tmp_path / "kairix" / "connectors" / "obsidian" / "__init__.py", "")
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage.
    _write(target, "from kairix.connectors.sharepoint.client import build\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/connectors/obsidian/sync.py") in violations


def test_connector_importing_sibling_via_import_form_is_flagged(tmp_path: Path) -> None:
    """``import kairix.connectors.<other>`` form is also detected.

    Sabotage-proof inline: rewrite as a core/connectors import; the flag
    clears.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "auth.py"
    _write(target, "import kairix.connectors.sharepoint.auth\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/connectors/obsidian/auth.py") in violations

    # Sabotage: import from core/connectors instead.
    _write(target, "from kairix.core.connectors.cursor_store import CursorStore\n")
    assert detector.collect_violations(tmp_path) == set()


def test_connector_importing_extractor_is_flagged(tmp_path: Path) -> None:
    """``from kairix.extractors.X import Y`` from a connector is rejected —
    extraction goes through the Extractor Protocol via the registry.

    Sabotage-proof inline: rewrite to a core/protocols import; the flag
    clears.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "process.py"
    _write(target, "from kairix.extractors.markitdown import MarkitdownExtractor\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/connectors/obsidian/process.py") in violations

    # Sabotage: replace with a legal Protocol import.
    _write(target, "from kairix.core.protocols import Extractor\n")
    assert detector.collect_violations(tmp_path) == set()


def test_connector_importing_extractor_via_import_form_is_flagged(
    tmp_path: Path,
) -> None:
    """``import kairix.extractors.X`` from a connector is rejected."""
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "boot.py"
    _write(target, "import kairix.extractors.ocr\n")
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/connectors/obsidian/boot.py") in violations


def test_connector_can_import_shared_base(tmp_path: Path) -> None:
    """``kairix.connectors._base`` is shared scaffolding, NOT a peer
    connector — never flagged.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "register.py"
    _write(
        target,
        "from kairix.connectors._base import SourceConnector, ConnectorRegistry\n",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_connector_can_import_core_protocols(tmp_path: Path) -> None:
    """A connector importing the core Protocol surface is fine — that's the
    contract it implements.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "sync.py"
    _write(
        target,
        "from kairix.core.protocols import SourceConnector, ChangeEvent, RawArtefact\n",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_connector_can_import_transport(tmp_path: Path) -> None:
    """``kairix.transport.*`` is the legitimate cross-plugin seam —
    connectors use it freely.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "sharepoint" / "graph.py"
    _write(target, "from kairix.transport.http import http_client\n")
    assert detector.collect_violations(tmp_path) == set()


def test_connector_can_import_core_connectors(tmp_path: Path) -> None:
    """``kairix.core.connectors.*`` is the orchestration layer — connectors
    delegate Bronze/Silver/cursor work to it through value types.
    """
    detector = _load_detector()
    target = tmp_path / "kairix" / "connectors" / "obsidian" / "sync.py"
    _write(target, "from kairix.core.connectors.cursor_store import Cursor\n")
    assert detector.collect_violations(tmp_path) == set()


def test_root_level_connector_files_are_not_plugins(tmp_path: Path) -> None:
    """Files directly under ``kairix/connectors/`` (``__init__.py``,
    ``_base.py``) are scaffolding, not plugins — F35 doesn't apply.
    """
    detector = _load_detector()
    _write(tmp_path / "kairix" / "connectors" / "__init__.py", "")
    _write(
        tmp_path / "kairix" / "connectors" / "_base.py",
        "# SourceConnector Protocol lives here.\n",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_missing_connectors_directory_passes(tmp_path: Path) -> None:
    """Fresh checkout where ``kairix/connectors/`` doesn't exist yet —
    F35 is a no-op until plugins appear.
    """
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()


def test_empty_connectors_directory_passes(tmp_path: Path) -> None:
    """``kairix/connectors/`` exists but holds no plugin subdirectories —
    gate green.
    """
    detector = _load_detector()
    (tmp_path / "kairix" / "connectors").mkdir(parents=True)
    assert detector.collect_violations(tmp_path) == set()


def test_remediation_carries_action_markers() -> None:
    """F35's REMEDIATION must satisfy F21."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem
