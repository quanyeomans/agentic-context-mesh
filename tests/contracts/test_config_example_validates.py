"""Contract: the repo's own ``kairix.config.example.yaml`` validates clean,
and the validator's sensitivity vocabulary is single-sourced from the
``Sensitivity`` / ``F39Tier`` Literals in ``kairix.core.protocols`` (GH #480).

#480 was the two-vocabularies bug: the example config tags sources with the
chunk-tag ``Sensitivity`` literal (``client-confidential``), but the topology
validator carried a *second*, divergent hardcoded F39 tier list
(``public/internal/confidential/restricted``) and rejected it. The two sources
of truth disagreed.

This module is the F50-style drift guard:

* **Drift guard** — the production ``kairix config validate`` binary, run against
  the repo's own example config, exits 0. If anyone re-introduces a hardcoded
  tier list that diverges from the Literal, this goes red.
* **Single source of truth** — the accepted F39 set is exactly ``get_args(F39Tier)``
  and every value in ``get_args(Sensitivity)`` is accepted (mapping onto its F39
  equivalent). Adding a tier to either Literal can't silently drift the validator.

Drives the production CLI surface via subprocess (the canonical F30 outcome
shape) and the public ``parse_topology_v2`` surface — no private-fn behaviour
test, no monkeypatch (F1/F2).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

from kairix.config import parse_topology_v2
from kairix.config.topology_v2 import TopologyV2ParseError
from kairix.core.protocols import F39Tier, Sensitivity

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_CONFIG = _REPO_ROOT / "kairix.config.example.yaml"


def test_example_config_validates_clean_through_the_cli() -> None:
    """``kairix config validate kairix.config.example.yaml`` exits 0.

    The example config tags a source ``default_sensitivity: client-confidential``
    (the chunk-tag ``Sensitivity`` vocabulary). Before #480 the topology
    validator's divergent hardcoded F39 list rejected it; this is the regression
    lock that the two vocabularies now resolve to one.
    """
    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "config", "validate", str(_EXAMPLE_CONFIG)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"validate failed (exit {result.returncode}):\n{combined}"
    assert "is not a valid F39 tier" not in combined, combined
    assert "OK" in result.stdout, result.stdout


def test_example_config_uses_the_chunk_tag_vocabulary() -> None:
    """Guard the premise: the example config really does use ``client-confidential``.

    If the example config is ever rewritten to only use F39 tiers, the drift
    guard above would pass vacuously. This pins that the example actually
    exercises the cross-vocabulary path.
    """
    text = _EXAMPLE_CONFIG.read_text(encoding="utf-8")
    assert "client-confidential" in text, "example config no longer exercises the chunk-tag vocabulary"


@pytest.mark.parametrize("tier", get_args(Sensitivity))
def test_every_sensitivity_literal_value_is_accepted(tier: str) -> None:
    """Every value in the ``Sensitivity`` Literal parses through topology_v2.

    Single-source-of-truth: the validator's chunk-tag map is derived from
    ``get_args(Sensitivity)``, so any value the type system permits is accepted.
    A hardcoded map that omitted a future-added value would fail here.
    """
    cfg = parse_topology_v2(
        {"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1", "default_sensitivity": tier}]}}
    )
    accepted = cfg.connectors[0].default_sensitivity
    assert accepted in get_args(F39Tier), f"{tier!r} normalised to {accepted!r}, not an F39 tier"


@pytest.mark.parametrize("tier", get_args(F39Tier))
def test_every_f39_tier_literal_value_passes_through(tier: str) -> None:
    """Every value in the ``F39Tier`` Literal passes through unchanged.

    The accepted F39 set is derived from ``get_args(F39Tier)`` — not a second
    hardcoded tuple. Adding a tier to the Literal extends what the validator
    accepts automatically.
    """
    cfg = parse_topology_v2(
        {"topology_v2": {"connectors": [{"id": "c1", "kind": "obsidian", "name": "c1", "default_sensitivity": tier}]}}
    )
    assert cfg.connectors[0].default_sensitivity == tier


def test_value_outside_both_vocabularies_is_rejected() -> None:
    """A genuinely-invalid tier is still rejected (the validator isn't a no-op)."""
    with pytest.raises(TopologyV2ParseError) as excinfo:
        parse_topology_v2(
            {
                "topology_v2": {
                    "connectors": [{"id": "c1", "kind": "obsidian", "name": "c1", "default_sensitivity": "bogus-tier"}]
                }
            }
        )
    message = str(excinfo.value)
    assert "bogus-tier" in message
    # The diagnostic lists the derived F39 vocabulary, not a stale hardcoded one.
    for tier in get_args(F39Tier):
        assert tier in message, f"diagnostic omits derived tier {tier!r}: {message}"
