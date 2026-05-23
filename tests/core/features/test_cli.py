"""Unit tests for the kairix-features CLI adapter.

Tests the format helpers and the ``main`` dispatch in isolation —
subprocess-level coverage lives in
``tests/integration/test_outcome_features_cli.py`` (F30 outcome test).
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from kairix.core.features.cli import (
    build_parser,
    format_json_envelope,
    format_table,
    main,
)
from kairix.core.features.resolver import FlagStatus

pytestmark = pytest.mark.unit


def _make_status(name: str = "canary", default: bool = False, effective: bool = False) -> FlagStatus:
    return FlagStatus(
        name=name,
        default=default,
        effective=effective,
        source="default",
        stage="introduce",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
        related_spec=None,
    )


def test_build_parser_requires_action() -> None:
    """The action positional is required — running ``kairix features``
    with no subcommand exits with the argparse usage error.
    """
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_accepts_status() -> None:
    """``status`` is the supported action; ``--json`` is its modifier."""
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.action == "status"
    assert args.emit_json is False

    args = parser.parse_args(["status", "--json"])
    assert args.emit_json is True


def test_format_table_handles_empty_entries() -> None:
    """Empty registry → friendly "no flags" line, not a bare header."""
    out = format_table(())
    assert "No feature flags registered" in out
    assert "NAME" not in out


def test_format_table_renders_header_and_row() -> None:
    """Populated registry → header line + one row per entry, columns
    aligned. The exact widths aren't load-bearing, but every column
    label has to be present so operators can scan the table.
    """
    out = format_table((_make_status("canary", default=False, effective=True),))
    assert "NAME" in out
    assert "DEFAULT" in out
    assert "EFFECTIVE" in out
    assert "STAGE" in out
    assert "RETIRE-BY" in out
    assert "canary" in out
    assert "true" in out  # effective rendered as lowercase
    assert "false" in out  # default rendered as lowercase


def test_format_json_envelope_emits_flags_key() -> None:
    """The JSON envelope shape is the canonical operator surface — the
    top-level object carries a ``flags`` list, each element a flat
    asdict projection of the FlagStatus dataclass.
    """
    out = format_json_envelope(())
    parsed = json.loads(out)
    assert parsed == {"flags": []}

    out = format_json_envelope((_make_status("alpha"),))
    parsed = json.loads(out)
    assert parsed["flags"][0]["name"] == "alpha"
    assert parsed["flags"][0]["stage"] == "introduce"


def test_main_status_text_mode_prints_table_and_returns_zero() -> None:
    """``main(['status'])`` prints the table and returns 0."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = main(["status"], status_provider=lambda: ())
    assert result == 0
    assert "No feature flags registered" in buf.getvalue()


def test_main_status_json_mode_prints_envelope_and_returns_zero() -> None:
    """``main(['status', '--json'])`` prints the JSON envelope."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = main(["status", "--json"], status_provider=lambda: ())
    assert result == 0
    parsed = json.loads(buf.getvalue())
    assert parsed == {"flags": []}


def test_main_uses_injected_status_provider() -> None:
    """The ``status_provider`` kwarg is the DI seam — tests pass a fake
    provider and the CLI renders its output without touching the real
    resolver.
    """
    sentinel = (_make_status("alpha", default=False, effective=True),)
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["status", "--json"], status_provider=lambda: sentinel)
    parsed = json.loads(buf.getvalue())
    assert parsed["flags"][0]["name"] == "alpha"
    assert parsed["flags"][0]["effective"] is True
