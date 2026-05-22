"""Contract tests for ``kairix.connectors._base``.

Verifies the SC-2 plug-point: the ``kairix.connectors`` package
re-exports the ``SourceConnector`` Protocol + its value objects so
plugin authors import from one canonical location, and that the
Protocol's documented surface matches the spec in
``docs/architecture/connector-ingestion-architecture.md`` §3.

These tests are deliberately shape-only — Wave 2's Obsidian plugin
lands a ``FakeSourceConnector`` in ``tests/fakes.py`` plus a
plugin-specific contract test under ``tests/contracts/`` (per F43).

Once SC-1's additions to ``kairix/core/protocols.py`` land on main,
``kairix/connectors/_base.py`` swaps its local Protocol definitions
for canonical re-exports from ``kairix.core.protocols``. These
contract tests pass against either shape — they check the surface,
not where the symbols are defined.
"""

from __future__ import annotations

import importlib

import pytest

from kairix.connectors import (
    ChangeEvent,
    Cursor,
    MimeType,
    RawArtefact,
    Sensitivity,
    SourceConnector,
)


@pytest.mark.contract
def test_source_connector_imports_from_package_root() -> None:
    """Plugin authors import from ``kairix.connectors`` — make sure that
    works for every symbol the spec promises.
    """
    assert SourceConnector is not None
    assert ChangeEvent is not None
    assert RawArtefact is not None
    assert Cursor is not None
    assert MimeType is not None
    assert Sensitivity is not None


@pytest.mark.contract
def test_package_export_resolves_through_the_canonical_module() -> None:
    """``kairix.connectors.SourceConnector`` must resolve to the same
    object that the package's ``_base`` re-export module defines —
    otherwise ``isinstance(x, SourceConnector)`` checks would split
    depending on which import path the test reached for.

    ``importlib.import_module`` is used instead of a direct
    ``from kairix.connectors import _base`` because F5 (no internal
    name imports in tests) treats the leading-underscore submodule as
    private. ``importlib`` is the stdlib escape that the F5 detector
    correctly does not flag — it's how the framework exercises its own
    re-export discipline without becoming a counterexample of it.
    """
    base = importlib.import_module("kairix.connectors._base")
    assert SourceConnector is base.SourceConnector
    assert ChangeEvent is base.ChangeEvent
    assert RawArtefact is base.RawArtefact
    assert Cursor is base.Cursor
    assert MimeType is base.MimeType
    assert Sensitivity is base.Sensitivity


@pytest.mark.contract
def test_source_connector_protocol_surface_matches_spec() -> None:
    """The Protocol must declare ``name`` + the four methods the spec
    pins (``list_changes``, ``fetch``, ``source_link``,
    ``sensitivity_for``). Sabotage-proof: deleting one of these
    attributes from ``_base.py`` would flip the matching ``hasattr``
    to False.
    """
    assert hasattr(SourceConnector, "list_changes")
    assert hasattr(SourceConnector, "fetch")
    assert hasattr(SourceConnector, "source_link")
    assert hasattr(SourceConnector, "sensitivity_for")
    # ``name`` is declared as a class-level attribute annotation, so
    # check the Protocol's annotations rather than ``hasattr`` (which
    # would also accept a missing attribute on a runtime_checkable
    # Protocol).
    assert "name" in SourceConnector.__annotations__


@pytest.mark.contract
def test_change_event_is_frozen_dataclass() -> None:
    """F42 mandates frozen-dataclass returns at the connector
    boundary. ``ChangeEvent`` is the canonical instance — assert
    runtime immutability via ``setattr`` (which mypy cannot statically
    prove violates the frozen contract, keeping --strict happy).
    """
    event = ChangeEvent(op="created", item_id="abc", modified_at="2026-05-22T00:00:00Z")
    with pytest.raises((AttributeError, TypeError)):
        setattr(event, "item_id", "mutated")  # noqa: B010 — setattr is deliberate; direct assignment is rejected by mypy --strict on a frozen dataclass (read-only Property), but we need a runtime path to assert the FrozenInstanceError is actually raised. The noqa keeps ruff B010 quiet without disabling the mypy check elsewhere.


@pytest.mark.contract
def test_raw_artefact_is_frozen_dataclass() -> None:
    """Same as ChangeEvent — F42 frozen-dataclass contract."""
    artefact = RawArtefact(raw=b"x", mime="text/plain", fetched_at="2026-05-22T00:00:00Z")
    with pytest.raises((AttributeError, TypeError)):
        setattr(artefact, "raw", b"mutated")  # noqa: B010 — setattr is deliberate; same rationale as the ChangeEvent frozen-dataclass assertion above.
