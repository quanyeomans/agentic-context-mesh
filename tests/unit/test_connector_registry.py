"""IM-2 unit tests for the connector + extractor registry resolvers.

Exercises the module-level :func:`iter_connectors` /
:func:`iter_extractors` / :func:`resolve_connector` /
:func:`resolve_extractor` helpers plus the :class:`ConnectorRegistry` /
:class:`ExtractorRegistry` wrapper classes against the entry-points
registered in kairix's own ``pyproject.toml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.connectors.registry import (
    ConnectorRegistry,
    ExtractorRegistry,
    iter_connectors,
    iter_extractors,
    resolve_connector,
    resolve_extractor,
)

pytestmark = pytest.mark.unit


def test_iter_connectors_includes_obsidian() -> None:
    """The first-party Obsidian connector is registered in pyproject."""
    names = {name for name, _ in iter_connectors()}
    assert "obsidian" in names


def test_iter_extractors_includes_passthrough_and_markitdown() -> None:
    """The first-party extractors are registered in pyproject."""
    names = {name for name, _ in iter_extractors()}
    assert "passthrough" in names
    assert "markitdown" in names


def test_resolve_connector_returns_factory() -> None:
    factory = resolve_connector("obsidian")
    assert callable(factory)


def test_resolve_extractor_returns_factory() -> None:
    factory = resolve_extractor("passthrough")
    assert callable(factory)


def test_resolve_connector_raises_keyerror_for_unknown() -> None:
    with pytest.raises(KeyError) as exc_info:
        resolve_connector("does-not-exist-connector-9000")
    msg = str(exc_info.value)
    # Action markers required by F21.
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg


def test_resolve_extractor_raises_keyerror_for_unknown() -> None:
    with pytest.raises(KeyError) as exc_info:
        resolve_extractor("does-not-exist-extractor-9000")
    msg = str(exc_info.value)
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg


def test_connector_registry_resolves_obsidian(tmp_path: Path) -> None:
    """``ConnectorRegistry.resolve`` returns a real SourceConnector instance."""
    from kairix.core.protocols import SourceConnector

    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ConnectorRegistry().resolve("obsidian", config={"vault_root": str(vault)})
    assert isinstance(connector, SourceConnector)


def test_extractor_registry_resolves_markdown_via_passthrough() -> None:
    """``ExtractorRegistry.resolve`` picks passthrough for ``text/markdown``."""
    from kairix.core.protocols import Extractor

    extractor = ExtractorRegistry().resolve("text/markdown", b"# heading\n")
    assert isinstance(extractor, Extractor)


def test_extractor_registry_raises_keyerror_with_actions() -> None:
    with pytest.raises(KeyError) as exc_info:
        ExtractorRegistry().resolve("application/x-no-such-format-9000", b"\x00\x00\x00\x00")
    msg = str(exc_info.value)
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg
