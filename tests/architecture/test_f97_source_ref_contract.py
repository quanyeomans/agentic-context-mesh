"""Unit tests for F97 (``scripts/checks/check_f97_source_ref_contract.py``).

F97 requires every registered agent-facing result-row dataclass (search /
timeline / entity / prep / research / contradict) to EITHER declare a
``SourceRef``-typed field OR define a ``source_ref`` accessor, so the
canonical resolvable breadcrumb is surfaced uniformly (PLA-274).

Each test scaffolds synthetic surface modules in tmpdir and confirms the
detector's verdict, with inline sabotage-proofs flipping the contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f97_source_ref_contract.py"


def _load_detector():
    """Load the F97 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f97_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f97_detector"] = module
    spec.loader.exec_module(module)
    return module


_METHOD_BODY = """
from dataclasses import dataclass
from kairix.core.protocols import SourceRef

@dataclass(frozen=True)
class {cls}:
    path: str
    source_uri: str = ""
    def source_ref(self) -> SourceRef:
        return SourceRef.of(path=self.path, source_uri=self.source_uri)
"""

_FIELD_BODY = """
from dataclasses import dataclass
from kairix.core.protocols import SourceRef

@dataclass(frozen=True)
class {cls}:
    ref: SourceRef
    snippet: str = ""
"""

_LIST_FIELD_BODY = """
from dataclasses import dataclass, field
from kairix.core.protocols import SourceRef

@dataclass(frozen=True)
class {cls}:
    query: str
    sources: list[SourceRef] = field(default_factory=list)
"""

_NONCONFORMING_BODY = """
from dataclasses import dataclass

@dataclass(frozen=True)
class {cls}:
    path: str
    title: str
"""


def _write_surfaces(detector, tmp_path: Path, bodies: dict[str, str]) -> None:
    """Write a synthetic module per registered surface.

    ``bodies`` maps a class name to the body template to use for it;
    surfaces absent from ``bodies`` get the conforming method body so only
    the surface under test diverges.
    """
    for rel_module, class_name in detector._REGISTERED_SURFACES:
        template = bodies.get(class_name, _METHOD_BODY)
        target = tmp_path / rel_module
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.format(cls=class_name), encoding="utf-8")


def test_all_surfaces_conforming_is_clean(tmp_path: Path) -> None:
    """Every registered surface carrying a source_ref method → no violations.

    Sabotage-proof inline: strip the contract from one surface; it's flagged.
    """
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {})
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage: rewrite TimelineHit to a bare path-only row.
    _write_surfaces(detector, tmp_path, {"TimelineHit": _NONCONFORMING_BODY})
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/timeline.py::TimelineHit") in violations


def test_embed_field_satisfies_contract(tmp_path: Path) -> None:
    """A ``ref: SourceRef`` field (the EMBED option) passes — no method needed."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {"ResearchChunk": _FIELD_BODY})
    assert detector.collect_violations(tmp_path) == set()


def test_list_of_sourceref_field_satisfies_contract(tmp_path: Path) -> None:
    """``sources: list[SourceRef]`` (prep's shape) satisfies the embed option."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {"PrepOutput": _LIST_FIELD_BODY})
    assert detector.collect_violations(tmp_path) == set()


def test_bare_path_row_is_flagged(tmp_path: Path) -> None:
    """A row with neither a SourceRef field nor a source_ref() method fails."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {"ContradictionHit": _NONCONFORMING_BODY})
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/contradict.py::ContradictionHit") in violations


def test_missing_surface_module_is_flagged(tmp_path: Path) -> None:
    """A registered surface whose module/class is gone (renamed away) is a
    violation — the contract names a surface that must exist."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {})
    # Delete one surface module entirely.
    (tmp_path / "kairix/use_cases/research.py").unlink()
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/research.py::ResearchChunk") in violations


def test_annotation_mentions_sourceref_helper() -> None:
    """The annotation walker recognises every SourceRef container shape and
    rejects unrelated annotations."""
    import ast

    detector = _load_detector()

    def _ann(src: str) -> ast.expr:
        return ast.parse(src, mode="eval").body

    assert detector._annotation_mentions_sourceref(_ann("SourceRef")) is True
    assert detector._annotation_mentions_sourceref(_ann("SourceRef | None")) is True
    assert detector._annotation_mentions_sourceref(_ann("list[SourceRef]")) is True
    assert detector._annotation_mentions_sourceref(_ann("tuple[SourceRef, ...]")) is True
    assert detector._annotation_mentions_sourceref(_ann("str")) is False
    assert detector._annotation_mentions_sourceref(_ann("dict[str, int]")) is False
    assert detector._annotation_mentions_sourceref(None) is False


def test_real_repo_gate_is_green() -> None:
    """The real F97 detector against the full repo emits no violations — the
    foundational PLA-274 change wires every registered surface."""
    detector = _load_detector()
    assert detector.main() == 0


def test_remediation_carries_action_markers() -> None:
    """F97's REMEDIATION must satisfy F21 (fix:/next:/run: action markers)."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem
