"""Source-string contract: the config loader must not hardcode ``reference-library``.

Reference-library treatment is an operator-config policy decision driven
by the topology collection's ``retrieval:`` block (a per-collection
override), not by a source branch. The config loader is a policy-neutral
surface that must not branch on the literal collection name; that
asymmetry has previously regressed into hidden behaviour where one corpus
was treated specially.

Justified callers that still legitimately reference the literal:
  - ``kairix/core/embed/cli.py`` — embed harness auto-injects a
    ``CollectionConfig(name="reference-library", ...)``. This is
    structural (the harness *is* the source of the name) and lives
    outside the config-loader policy surface.
  - ``kairix/core/search/registry.py`` — ``RESERVED_AGENT_COLLECTION_NAMES``
    structurally defends against agent-collection name collisions with
    that auto-injected name. Single-element constant, name-collision
    only, no policy intent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.search import config_loader as config_loader_module


@pytest.mark.contract
def test_config_loader_resolve_retrieval_does_not_reference_reflib_literal() -> None:
    """``resolve_retrieval_config`` must not branch on the literal collection name.

    The reference-library retrieval baseline lives in operator yaml as a
    per-collection ``retrieval:`` block, not in source. The example yaml
    ships the historical baseline values so new operators get them by
    default; operators who deviate are taking deliberate ownership.
    """
    source = Path(config_loader_module.__file__).read_text(encoding="utf-8")
    # The string may legitimately appear in the explanatory docstring of
    # ``resolve_retrieval_config``; what we forbid is a policy branch that
    # tests against the literal at runtime.
    forbidden_patterns = [
        '== "reference-library"',
        "== 'reference-library'",
        '!= "reference-library"',
        "!= 'reference-library'",
        '"reference-library":',
    ]
    offenders = [pat for pat in forbidden_patterns if pat in source]
    assert not offenders, (
        f"kairix/core/search/config_loader.py contains forbidden reflib policy patterns: "
        f"{offenders}. Use per-collection retrieval overrides via yaml, not source branches."
    )
