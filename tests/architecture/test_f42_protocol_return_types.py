"""Unit tests for F42 (``scripts/checks/check_f42_protocol_return_types.py``).

F42 requires every public method on a connector-surface Protocol
(``SourceConnector``, ``Extractor``, ``BronzeStore``,
``SilverProcessor``, ``EntityGraphSink``) to return a frozen
dataclass, a tuple/iterator/list of one, or a primitive / Optional
shape. Bare ``dict[str, Any]``, ``list[dict]``, ``Any``, and
``Mapping[..., Any]`` returns are rejected.

Each test scaffolds a synthetic ``protocols.py`` in tmpdir and
confirms the detector's verdict, with an inline sabotage-proof
flipping the violating shape.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f42_protocol_return_types.py"


def _load_detector():
    """Load the F42 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f42_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f42_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write_protocols(tmp_path: Path, body: str) -> None:
    """Write a synthetic ``kairix/core/protocols.py`` containing
    ``body`` (the imports and Protocol class definitions).
    """
    target = tmp_path / "kairix" / "core" / "protocols.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def test_conforming_surface_protocol_is_not_flagged(tmp_path: Path) -> None:
    """A surface Protocol whose methods return frozen-dc / Iterator /
    Optional / scalar is not flagged.

    Sabotage-proof inline: swap one method's return to ``dict[str, Any]``;
    the detector fires.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Iterator, Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class ChangeEvent:
    op: str
    item_id: str

class SourceConnector(Protocol):
    def list_changes(self, cursor: str | None) -> Iterator[ChangeEvent]: ...
    def fetch(self, item_id: str) -> ChangeEvent: ...
    def source_link(self, item_id: str) -> str: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()

    # Sabotage: rewrite source_link to return dict[str, Any].
    _write_protocols(
        tmp_path,
        """
from typing import Any, Iterator, Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class ChangeEvent:
    op: str
    item_id: str

class SourceConnector(Protocol):
    def list_changes(self, cursor: str | None) -> Iterator[ChangeEvent]: ...
    def fetch(self, item_id: str) -> ChangeEvent: ...
    def source_link(self, item_id: str) -> dict[str, Any]: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::SourceConnector.source_link") in violations


def test_dict_return_is_flagged(tmp_path: Path) -> None:
    """``-> dict[str, Any]`` on a surface Protocol method fails.

    Sabotage-proof inline: swap to a frozen dataclass; flag clears.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Protocol
from dataclasses import dataclass

class Extractor(Protocol):
    def extract(self, raw: bytes) -> dict[str, Any]: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::Extractor.extract") in violations

    # Sabotage: switch to a typed shape.
    _write_protocols(
        tmp_path,
        """
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str

class Extractor(Protocol):
    def extract(self, raw: bytes) -> ExtractedDocument: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_list_dict_return_is_flagged(tmp_path: Path) -> None:
    """``-> list[dict]`` is rejected — container of forbidden inner."""
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Protocol

class BronzeStore(Protocol):
    def replay(self, source_name: str) -> list[dict]: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::BronzeStore.replay") in violations


def test_iterator_of_dict_is_flagged(tmp_path: Path) -> None:
    """``-> Iterator[dict[str, Any]]`` is rejected — Iterator carries
    a forbidden element type.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Iterator, Protocol

class SourceConnector(Protocol):
    def list_changes(self, cursor: str | None) -> Iterator[dict[str, Any]]: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::SourceConnector.list_changes") in violations


def test_bare_any_return_is_flagged(tmp_path: Path) -> None:
    """``-> Any`` is rejected outright."""
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Protocol

class EntityGraphSink(Protocol):
    def stage(self, signals: list[str]) -> Any: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::EntityGraphSink.stage") in violations


def test_mapping_any_return_is_flagged(tmp_path: Path) -> None:
    """``-> Mapping[str, Any]`` is rejected — Mapping is in the
    forbidden name set, and the inner Any compounds it.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Mapping, Protocol

class SilverProcessor(Protocol):
    def process(self, raw: bytes) -> Mapping[str, Any]: ...
""",
    )
    violations = detector.collect_violations(tmp_path)
    assert Path("kairix/core/protocols.py::SilverProcessor.process") in violations


def test_optional_of_frozen_dc_is_allowed(tmp_path: Path) -> None:
    """``-> ChangeEvent | None`` (PEP 604 union with None) is allowed."""
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class ChangeEvent:
    op: str

class SourceConnector(Protocol):
    def fetch(self, item_id: str) -> ChangeEvent | None: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_tuple_of_frozen_dc_is_allowed(tmp_path: Path) -> None:
    """``-> tuple[Chunk, ...]`` is the canonical homogeneous-tuple shape."""
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class Chunk:
    text: str

class SilverProcessor(Protocol):
    def process(self, raw: bytes) -> tuple[Chunk, ...]: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_non_surface_protocol_is_not_scanned(tmp_path: Path) -> None:
    """Protocols not in the connector-surface set (e.g.,
    ``DocumentRepository``) are intentionally skipped — F42 is the
    typed-boundary discipline for the new connector framework, not
    a retroactive sweep over the legacy repository Protocols.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Protocol

class DocumentRepository(Protocol):
    def search_fts(self, query: str) -> list[dict[str, Any]]: ...
""",
    )
    # DocumentRepository is NOT in _SURFACE_PROTOCOLS.
    assert detector.collect_violations(tmp_path) == set()


def test_private_method_is_skipped(tmp_path: Path) -> None:
    """``_``-prefixed methods are skipped — only the public surface
    is subject to the discipline.
    """
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Any, Protocol

class SourceConnector(Protocol):
    def _internal(self) -> dict[str, Any]: ...
    def source_link(self, item_id: str) -> str: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_missing_protocols_file_passes(tmp_path: Path) -> None:
    """Fresh checkout: no ``protocols.py`` — detector is a no-op."""
    detector = _load_detector()
    assert detector.collect_violations(tmp_path) == set()


def test_scalar_returns_are_allowed(tmp_path: Path) -> None:
    """Primitive scalars and None are accepted everywhere."""
    detector = _load_detector()
    _write_protocols(
        tmp_path,
        """
from typing import Protocol

class SourceConnector(Protocol):
    def source_link(self, item_id: str) -> str: ...
    def sensitivity_for(self, item_id: str) -> bool: ...
""",
    )
    assert detector.collect_violations(tmp_path) == set()


def test_real_repo_gate_is_green() -> None:
    """The real F42 detector run against the full repo emits no
    net-new violations — the connector-surface Protocols don't
    exist yet, so the rule is vacuously clean.
    """
    detector = _load_detector()
    assert detector.main() == 0


def test_remediation_carries_action_markers() -> None:
    """F42's REMEDIATION must satisfy F21."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem
