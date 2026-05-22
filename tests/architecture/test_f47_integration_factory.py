"""Unit tests for F47 (``scripts/checks/check_f47_integration_factory.py``).

F47 flags ``tests/integration/test_*.py`` files that construct a
``*Pipeline`` class imported from ``kairix.*`` directly. The allowed
shape is ``kairix.core.factory.build_<pipeline>(paths=FakePaths(...))``.
Two exemptions: files under ``tests/contracts/`` (not scanned) and files
matching ``test_*_contract.py`` (single-layer boundary proofs).

Each test has an inline sabotage-proof recorded in its docstring — the
detector must fire on the violating shape AND go quiet on the allowed
shape.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f47_integration_factory.py"


def _load_detector():
    """Load the F47 detector module by file path (avoids package import)."""
    spec = importlib.util.spec_from_file_location("_f47_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f47_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


_VIOLATING_BODY = """\
from kairix.core.search.pipeline import SearchPipeline


def test_search_pipeline_direct():
    pipeline = SearchPipeline(retriever=None, vector_repo=None)
    assert pipeline is not None
"""

_FACTORY_BODY = """\
from kairix.core.factory import build_search_pipeline
from tests.fakes import FakePaths


def test_search_pipeline_via_factory(tmp_path):
    paths = FakePaths(root=tmp_path)
    pipeline = build_search_pipeline(paths=paths)
    assert pipeline is not None
"""


def test_direct_pipeline_construction_is_flagged(tmp_path: Path) -> None:
    """A ``test_*.py`` file that imports SearchPipeline from kairix.* and
    calls ``SearchPipeline(...)`` directly is flagged.

    Sabotage-proof: remove the ``SearchPipeline(...)`` Call from the body
    (leaving the import) — the detector goes quiet.
    Inline confirmation (executed): see the assertion below the unlink.
    """
    detector = _load_detector()
    target = tmp_path / "tests" / "integration" / "test_violator.py"
    _write(target, _VIOLATING_BODY)
    violations = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_violator.py") in violations

    # Sabotage-proof: remove the call (keep the import); detector goes quiet.
    target.write_text(
        "from kairix.core.search.pipeline import SearchPipeline\n"
        "\n"
        "def test_noop():\n"
        "    assert SearchPipeline is not None\n",
        encoding="utf-8",
    )
    violations_after = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_violator.py") not in violations_after


def test_contract_file_is_exempt(tmp_path: Path) -> None:
    """A file under ``tests/integration/`` ending in ``_contract.py`` with
    identical direct construction is NOT flagged — it's a single-layer
    boundary proof, intentionally bypassing composition.

    Sabotage-proof: rename the same file to ``test_violator.py``; the
    detector fires. (Executed inline below.)
    """
    detector = _load_detector()
    target = tmp_path / "tests" / "integration" / "test_search_contract.py"
    _write(target, _VIOLATING_BODY)
    violations = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_search_contract.py") not in violations

    # Sabotage: rename so the exemption no longer applies.
    renamed = tmp_path / "tests" / "integration" / "test_violator.py"
    target.rename(renamed)
    violations_after = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_violator.py") in violations_after


def test_factory_construction_is_clean(tmp_path: Path) -> None:
    """A test using ``kairix.core.factory.build_search_pipeline(...)`` is
    NOT flagged: it imports the factory builder, not a ``*Pipeline``
    class, so the import filter never adds anything to flag.

    Sabotage-proof: change the import to
    ``from kairix.core.search.pipeline import SearchPipeline`` and call
    ``SearchPipeline(...)``; the detector fires. (Executed inline.)
    """
    detector = _load_detector()
    target = tmp_path / "tests" / "integration" / "test_clean.py"
    _write(target, _FACTORY_BODY)
    violations = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_clean.py") not in violations

    # Sabotage: rewrite as a direct-construction violator under the same name.
    target.write_text(_VIOLATING_BODY, encoding="utf-8")
    violations_after = detector.collect_violations(tmp_path)
    assert Path("tests/integration/test_clean.py") in violations_after
