"""#475 — ``reference_library.index`` config surface (eager | lazy | skip).

Drives the public ``parse_reference_library`` / ``load_reference_library``
boundary in ``kairix.core.search.config_loader``. Contract:

  * absent block → eager (default-safe: existing deployments unchanged)
  * each of the three valid modes parses verbatim
  * an invalid mode raises ``ConfigValidationError`` with an F21-shaped
    message naming all three valid options — silent fallback would mask
    the exact misconfiguration class this flag exists to prevent
  * ``load_reference_library`` mirrors ``load_collections``: explicit
    ``config_path`` kwarg (F2-clean), missing file → default
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from kairix.core.search.config_loader import (
    VALID_REFLIB_INDEX_MODES,
    ConfigValidationError,
    ReferenceLibraryConfig,
    load_reference_library,
    parse_reference_library,
)

pytestmark = pytest.mark.unit


def test_absent_block_defaults_to_eager() -> None:
    """No ``reference_library:`` block → eager (today's behaviour).

    Sabotage proof: changing the dataclass default to "lazy" fails this.
    """
    assert parse_reference_library({}).index == "eager"
    assert ReferenceLibraryConfig().index == "eager"


@pytest.mark.parametrize("mode", VALID_REFLIB_INDEX_MODES)
def test_each_valid_mode_parses(mode: str) -> None:
    """eager / lazy / skip all round-trip through the parser."""
    cfg = parse_reference_library({"reference_library": {"index": mode}})
    assert cfg.index == mode


def test_block_present_without_index_defaults_to_eager() -> None:
    """``reference_library: {}`` (block declared, no index key) → eager."""
    assert parse_reference_library({"reference_library": {}}).index == "eager"


def test_invalid_mode_raises_with_f21_affordance() -> None:
    """A typo'd mode raises loudly, naming all three valid options.

    Sabotage proof: removing the ``mode not in VALID_REFLIB_INDEX_MODES``
    raise in ``parse_reference_library`` makes this fail (no exception).
    """
    with pytest.raises(ConfigValidationError) as info:
        parse_reference_library({"reference_library": {"index": "skpi"}})
    msg = str(info.value)
    assert "'skpi'" in msg
    for mode in ("eager", "lazy", "skip"):
        assert mode in msg
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg


def test_non_mapping_block_raises() -> None:
    """``reference_library: skip`` (scalar, not a mapping) raises with remediation."""
    with pytest.raises(ConfigValidationError) as info:
        parse_reference_library({"reference_library": "skip"})
    assert "must be a mapping" in str(info.value)
    assert "fix:" in str(info.value)


def test_load_returns_default_when_no_config_file(tmp_path: Path) -> None:
    """Explicit path to a nonexistent file → eager default, no raise."""
    cfg = load_reference_library(config_path=tmp_path / "missing.yaml")
    assert cfg.index == "eager"


def test_load_reads_mode_from_yaml_file(tmp_path: Path) -> None:
    """A real YAML file with ``index: lazy`` loads as lazy.

    Sabotage proof: dropping the ``parse_reference_library(data)`` call
    in ``load_reference_library`` (returning the bare default) fails this.
    """
    config = tmp_path / "kairix.config.yaml"
    config.write_text(
        dedent(
            """
            reference_library:
              index: lazy
            """
        ),
        encoding="utf-8",
    )
    assert load_reference_library(config_path=config).index == "lazy"


def test_load_propagates_invalid_mode_error(tmp_path: Path) -> None:
    """An invalid declared value raises at load time — not silently eager."""
    config = tmp_path / "kairix.config.yaml"
    config.write_text("reference_library:\n  index: nope\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        load_reference_library(config_path=config)


def test_load_returns_default_on_unparseable_yaml(tmp_path: Path) -> None:
    """Broken YAML mirrors ``load_collections``: fall back to the default."""
    config = tmp_path / "kairix.config.yaml"
    config.write_text("reference_library: [unclosed\n  - ::bad", encoding="utf-8")
    assert load_reference_library(config_path=config).index == "eager"
