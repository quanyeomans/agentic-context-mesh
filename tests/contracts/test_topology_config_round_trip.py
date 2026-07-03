"""F96 contract: nested connector config round-trips losslessly (parse -> materialize).

Guards the topology ``str()``-coercion bug class (kairix#621): any value in
``connector_specific_config`` / ``extractor_config`` — including deeply nested
lists/dicts, mixed scalars, unicode, None, large ints — must survive
``parse_topology`` -> ``config_pairs_to_mapping`` unchanged. A regression here
silently strands any connector with nested config (SharePoint's ``drives:`` list
was the production instance: it became a Python repr-string the connector
rejected every sync tick).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.config import config_pairs_to_mapping, parse_topology

pytestmark = pytest.mark.contract

_ADVERSARIAL_CONFIGS: list[dict[str, Any]] = [
    # SharePoint-shape: a list of dicts (the exact production bug).
    {"drives": [{"site_id": "tc.sharepoint.com,a,b", "exclude_paths": ["/Archive", "/Personal"]}]},
    # Deeply nested: dict -> list -> dict -> list.
    {"filters": {"include": [{"path": "/a", "tags": ["x", "y"]}], "exclude": [{"path": "/b", "tags": []}]}},
    # Mixed scalar types (the str()-coercion silently flattens these).
    {"max_items": 50, "recursive": True, "ratio": 1.5, "name": "x", "nothing": None},
    # Unicode + symbols.
    {"label": "café — déjà vu — 日本語", "marker": "checkmark-and-warn"},
    # Empty nested containers.
    {"drives": [], "settings": {}},
    # Large int beyond 2^53 (JSON-float precision edge; preserved as a Python int).
    {"big": 9007199254740993},
]


def _materialise(field: str, value: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_topology({"topology": {"connectors": [{"id": "c", "kind": "obsidian", "name": "c", field: value}]}})
    return config_pairs_to_mapping(getattr(parsed.connectors[0], field))


@pytest.mark.parametrize("value", _ADVERSARIAL_CONFIGS)
def test_connector_specific_config_round_trips(value: dict[str, Any]) -> None:
    assert _materialise("connector_specific_config", value) == value


@pytest.mark.parametrize("value", _ADVERSARIAL_CONFIGS)
def test_extractor_config_round_trips(value: dict[str, Any]) -> None:
    assert _materialise("extractor_config", value) == value
