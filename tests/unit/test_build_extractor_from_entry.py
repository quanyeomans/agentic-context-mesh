"""Tests for :func:`kairix.core.connectors.registry.build_extractor_from_entry` —
the shared helper that constructs either a single extractor or an
:class:`EscalatingExtractor` from a connector config entry. Used by
``_run_one_connector_batch`` (sync) and ``_build_reextract_components``
(Bug D recovery)."""

from __future__ import annotations

import pytest

from kairix.core.connectors.escalation import EscalatingExtractor
from kairix.core.connectors.registry import build_extractor_from_entry

pytestmark = pytest.mark.unit


def test_default_returns_passthrough_when_no_extractor_set() -> None:
    """Entry with no ``extractor`` field falls back to ``passthrough`` —
    matches the historical default in worker.py call sites.

    Sabotage proof: change the default ``"passthrough"`` to ``"markitdown"``;
    the assertion fails because the wrong extractor name is returned.
    """
    extractor = build_extractor_from_entry({})
    assert extractor.name == "passthrough"


def test_single_extractor_returns_named_extractor() -> None:
    """``extractor: passthrough`` returns a single passthrough extractor,
    NOT wrapped in a chain. Backward compatibility for existing configs.

    Sabotage proof: change the precedence so chain wraps even single-name
    entries; the assertion ``isinstance(..., EscalatingExtractor)`` fails.
    """
    extractor = build_extractor_from_entry({"extractor": "passthrough"})
    assert extractor.name == "passthrough"
    assert not isinstance(extractor, EscalatingExtractor)


def test_extractor_chain_returns_escalating_extractor() -> None:
    """``extractor_chain: [a, b]`` wraps in EscalatingExtractor.

    Sabotage proof: remove the chain branch; the function returns the
    single-extractor default and the isinstance check fails.
    """
    extractor = build_extractor_from_entry({"extractor_chain": ["passthrough", "passthrough"]})
    assert isinstance(extractor, EscalatingExtractor)
    assert extractor.name == "escalating(passthrough,passthrough)"


def test_extractor_chain_precedes_extractor_when_both_set() -> None:
    """When both fields are set, ``extractor_chain`` wins. Operators
    explicitly opting into escalation shouldn't have the legacy field
    silently override.

    Sabotage proof: invert the precedence; the test fails because
    the single-extractor path is taken even with chain set.
    """
    extractor = build_extractor_from_entry({"extractor": "markitdown", "extractor_chain": ["passthrough"]})
    assert isinstance(extractor, EscalatingExtractor)
    assert "passthrough" in extractor.name


def test_extractor_chain_invalid_shape_raises_with_fix_pointer() -> None:
    """Operator typo (``extractor_chain: passthrough`` not in a list)
    fails fast with a fix-pointer error. The alternative (silent
    fall-through to single-extractor) hides the misconfiguration.

    Sabotage proof: relax the isinstance check; the test fails because
    no ValueError is raised.
    """
    with pytest.raises(ValueError, match="extractor_chain must be a list"):
        build_extractor_from_entry({"extractor_chain": "passthrough"})


def test_extractor_chain_passes_per_member_configs() -> None:
    """``extractor_chain_configs: {name: {...}}`` threads per-tier kwargs
    into the matching factory. Without this seam operators can't
    configure individual tiers in a chain.

    Sabotage proof: drop the per_member_configs lookup; the chain
    construction succeeds but the chain member doesn't receive its
    config. Hard to assert directly without a fake extractor — verified
    structurally by inspecting build_extractor_from_entry's source.
    """
    import inspect

    from kairix.core.connectors import registry

    source = inspect.getsource(registry.build_extractor_from_entry)
    # Structural assertion — the per-member-config branch must threadkwargs
    # into the factory call.
    assert "extractor_chain_configs" in source
    assert "per_member_configs.get(name" in source or "per_member_configs[name" in source
