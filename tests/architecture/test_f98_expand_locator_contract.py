"""Unit tests for F98 (``scripts/checks/check_f98_expand_locator_contract.py``).

F98 has two limbs (PLA-297, the anti-dead-end lock):

  * Limb A — every registered agent result-row surface (search / timeline /
    entity / prep / research / contradict / expand) exposes an
    expand-acceptable locator: a ``source_ref`` accessor, a ``SourceRef``
    field, OR a ``source_uri`` field.
  * Limb B — ``run_expand`` keeps its ``seq`` parameter optional so a
    source_uri-only locator is expandable.

Each test scaffolds synthetic surfaces / an expand module in tmpdir and
confirms the detector's verdict, with inline sabotage-proofs flipping each
limb of the contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f98_expand_locator_contract.py"


def _load_detector():
    """Load the F98 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f98_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f98_detector"] = module
    spec.loader.exec_module(module)
    return module


_SOURCE_REF_BODY = """
from dataclasses import dataclass
from kairix.core.protocols import SourceRef

@dataclass(frozen=True)
class {cls}:
    path: str
    source_uri: str = ""
    def source_ref(self) -> SourceRef:
        return SourceRef.of(path=self.path, source_uri=self.source_uri)
"""

_SOURCE_URI_FIELD_BODY = """
from dataclasses import dataclass

@dataclass(frozen=True)
class {cls}:
    path: str
    source_uri: str = ""
"""

_SOURCE_REF_FIELD_BODY = """
from dataclasses import dataclass
from kairix.core.protocols import SourceRef

@dataclass(frozen=True)
class {cls}:
    ref: SourceRef
    snippet: str = ""
"""

_NONCONFORMING_BODY = """
from dataclasses import dataclass

@dataclass(frozen=True)
class {cls}:
    path: str
    title: str
"""

_EXPAND_OPTIONAL_SEQ = """
def run_expand(source_uri: str, seq: int | None = None, *, token_budget: int = 2000):
    return None
"""

_EXPAND_REQUIRED_SEQ = """
def run_expand(source_uri: str, seq: int, *, token_budget: int = 2000):
    return None
"""


def _write_surfaces(detector, tmp_path: Path, bodies: dict[str, str], *, expand_body: str) -> None:
    """Write a synthetic module per registered surface + the expand module.

    ``bodies`` maps a class name to its body template; surfaces absent get the
    conforming source_ref body. ``expand_body`` is the ``run_expand``-carrying
    body for the expand module (whose ExpandedChunk surface is also written).
    """
    for rel_module, class_name in detector._EXPAND_LOCATOR_SURFACES:
        template = bodies.get(class_name, _SOURCE_REF_BODY)
        target = tmp_path / rel_module
        target.parent.mkdir(parents=True, exist_ok=True)
        text = template.format(cls=class_name)
        if rel_module == detector._EXPAND_MODULE:
            # The expand module carries BOTH ExpandedChunk and run_expand.
            text = text + "\n" + expand_body
        target.write_text(text, encoding="utf-8")


def test_all_surfaces_conforming_and_optional_seq_is_clean(tmp_path: Path) -> None:
    """Every surface exposing a locator + optional seq → no violations.

    Sabotage-proof inline: strip the locator from one surface; it's flagged.
    """
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {}, expand_body=_EXPAND_OPTIONAL_SEQ)
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage: rewrite TimelineHit to a bare path-only row.
    _write_surfaces(detector, tmp_path, {"TimelineHit": _NONCONFORMING_BODY}, expand_body=_EXPAND_OPTIONAL_SEQ)
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/timeline.py::TimelineHit") in violations


def test_source_uri_field_satisfies_limb_a(tmp_path: Path) -> None:
    """A bare ``source_uri`` field (no source_ref method) is expand-acceptable."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {"ContradictionHit": _SOURCE_URI_FIELD_BODY}, expand_body=_EXPAND_OPTIONAL_SEQ)
    assert detector.collect_violations(tmp_path) == set()


def test_sourceref_field_satisfies_limb_a(tmp_path: Path) -> None:
    """A ``ref: SourceRef`` field satisfies the locator contract."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {"ResearchChunk": _SOURCE_REF_FIELD_BODY}, expand_body=_EXPAND_OPTIONAL_SEQ)
    assert detector.collect_violations(tmp_path) == set()


def test_required_seq_is_flagged_as_dead_end(tmp_path: Path) -> None:
    """Limb B sabotage-proof: run_expand with a REQUIRED seq re-introduces the
    dead-end and is flagged."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {}, expand_body=_EXPAND_REQUIRED_SEQ)
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/expand.py::run_expand") in violations


def test_missing_surface_module_is_flagged(tmp_path: Path) -> None:
    """A registered surface whose module is gone (renamed away) is a violation."""
    detector = _load_detector()
    _write_surfaces(detector, tmp_path, {}, expand_body=_EXPAND_OPTIONAL_SEQ)
    (tmp_path / "kairix/use_cases/research.py").unlink()
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/use_cases/research.py::ResearchChunk") in violations


def test_param_optionality_helper_recognises_both_shapes() -> None:
    """``_param_is_optional`` accepts a default OR a ``| None`` annotation and
    rejects a bare required param."""
    import ast

    detector = _load_detector()

    def _func(src: str) -> ast.FunctionDef:
        node = ast.parse(src).body[0]
        assert isinstance(node, ast.FunctionDef)
        return node

    assert detector._param_is_optional(_func("def f(source_uri, seq=None): ..."), "seq") is True
    assert detector._param_is_optional(_func("def f(source_uri, seq: int | None): ..."), "seq") is True
    assert detector._param_is_optional(_func("def f(source_uri, seq: int): ..."), "seq") is False


def test_real_repo_gate_is_green() -> None:
    """The real F98 detector against the full repo emits no violations — the
    PLA-297 change makes every surface expand-acceptable and expand
    source_uri-only capable."""
    detector = _load_detector()
    assert detector.main() == 0


def test_remediation_carries_action_markers() -> None:
    """F98's REMEDIATION must satisfy F21 (fix:/next:/run: action markers)."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem
