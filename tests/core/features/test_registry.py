"""Unit tests for the feature-flag registry.

Covers the frozen-dataclass shape, the empty-registry invariant at
PR-2 landing, and the defensive key/name validation that fires when a
future entry's dict key disagrees with its ``FeatureFlag.name``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kairix.core.features.registry import REGISTRY, FeatureFlag, validate_registry

pytestmark = pytest.mark.unit


def test_registry_has_obsidian_connector_primary_at_pr6_landing() -> None:
    """PR-6 lands the first flag — ``obsidian_connector_primary`` at
    introduce stage. Every future PR that adds a flag appends to this
    assertion.

    Sabotage: rename the registry entry's key → this test fails
    because ``"obsidian_connector_primary" in REGISTRY`` is False.
    Verified during PR-6 development.
    """
    assert "obsidian_connector_primary" in REGISTRY, (
        f"expected the obsidian_connector_primary entry at PR-6 landing; got: {sorted(REGISTRY)}"
    )
    entry = REGISTRY["obsidian_connector_primary"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec == "docs/architecture/connector-ingestion-architecture.md"


def test_feature_flag_is_frozen() -> None:
    """FeatureFlag must be immutable — F42 frozen-dc discipline at the
    boundary. Mutating any field on an instance raises FrozenInstanceError.
    """
    entry = FeatureFlag(
        name="canary",
        default=False,
        description="canary",
        stage="introduce",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
    )

    with pytest.raises(FrozenInstanceError):
        entry.default = True  # type: ignore[misc] — testing immutability


def test_feature_flag_default_related_spec_is_none() -> None:
    """``related_spec`` is optional — it defaults to None so flags
    without a spec doc can still be declared.
    """
    entry = FeatureFlag(
        name="canary",
        default=False,
        description="canary",
        stage="introduce",
        introduced_in="v2026.5.22",
        target_retire_in="v2026.7.22",
        owner="test",
    )

    assert entry.related_spec is None


def test_validate_registry_rejects_mismatched_key_and_name() -> None:
    """The cheap defensive check must catch a typo where the dict key
    disagrees with the ``FeatureFlag.name`` field. F52 (PR-3) will
    catch dead call sites; this validator catches the registry-side
    typo at import time.
    """
    bad_registry = {
        "the_dict_key": FeatureFlag(
            name="the_dc_name",
            default=False,
            description="canary",
            stage="introduce",
            introduced_in="v2026.5.22",
            target_retire_in="v2026.7.22",
            owner="test",
        ),
    }

    with pytest.raises(ValueError, match=r"does not match FeatureFlag\.name"):
        validate_registry(bad_registry)


def test_validate_registry_accepts_matched_entry() -> None:
    """The validator must accept the well-formed shape — the production
    registry passes this validator on import, so the empty-good path
    has to be exercised explicitly.
    """
    good_registry = {
        "canary": FeatureFlag(
            name="canary",
            default=False,
            description="canary",
            stage="introduce",
            introduced_in="v2026.5.22",
            target_retire_in="v2026.7.22",
            owner="test",
        ),
    }

    # Must not raise — empty assertion is intentional: the success signal
    # IS the absence of an exception.
    validate_registry(good_registry)


def test_validate_registry_accepts_empty_dict() -> None:
    """The PR-2 landing state is the empty registry — the validator
    must accept it.
    """
    validate_registry({})
