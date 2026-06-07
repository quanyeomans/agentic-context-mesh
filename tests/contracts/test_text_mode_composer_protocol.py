"""Contract tests for :class:`kairix.agents.mcp.text_mode_composers.TextModeComposer`.

PR 2.8 / #421 introduces the composer registry that maps CLI
subcommands to ``(from_envelope, format_text)`` pairs. The dispatcher
uses the registry to render text mode from a warm-MCP envelope without
re-running the in-process pipeline.

Contract surface:

* ``TextModeComposer`` is a frozen dataclass with three fields:
  ``from_envelope`` (callable taking a dict, returning a result object),
  ``format_text`` (callable taking ``(result, argv)`` and returning a
  string), and ``name`` (str for diagnostics).
* ``register_composer(subcommand, composer)`` adds an entry; calling it
  twice with the same subcommand replaces the prior entry (last-write
  wins so import-order regressions don't cause silent stale renders).
* ``get_composer(subcommand)`` returns the entry or ``None`` for
  unknown subcommands.
* ``list_registered()`` returns the registered subcommand names sorted
  so diagnostic output is stable.

F1/F2-clean: tests construct the dataclass directly + drive the public
register/get/list surface; no monkeypatch on kairix internals.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.text_mode_composers import (
    TextModeComposer,
    get_composer,
    list_registered,
    register_composer,
)

pytestmark = pytest.mark.contract


# Sabotage-proof (executed): mutated ``TextModeComposer`` to add a
# second mandatory field; this test failed with TypeError missing
# argument. Restored.
def test_text_mode_composer_is_frozen_dataclass_with_three_fields() -> None:
    """The dataclass shape: from_envelope + format_text + name."""
    composer = TextModeComposer(
        from_envelope=lambda env: dict(env),
        format_text=lambda result, argv: f"rendered:{result}:{argv}",
        name="example-subcmd",
    )
    assert composer.name == "example-subcmd"
    # Frozen — assignment must raise FrozenInstanceError (stdlib dataclasses)
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        composer.name = "mutated"  # type: ignore[misc] — frozen-write probe


# Sabotage-proof (executed): mutated ``register_composer`` to ignore
# the second call so the same subcommand re-register would not replace
# the prior entry; this test failed because ``get_composer`` returned
# the original. Restored.
def test_register_composer_replaces_prior_entry_for_same_subcommand() -> None:
    """Re-registering the same subcommand wins last-write — no silent stale composer."""
    first = TextModeComposer(
        from_envelope=lambda env: {"first": True, **env},
        format_text=lambda result, argv: "first-renderer",
        name="replace-probe",
    )
    second = TextModeComposer(
        from_envelope=lambda env: {"second": True, **env},
        format_text=lambda result, argv: "second-renderer",
        name="replace-probe",
    )
    register_composer("replace-probe", first)
    register_composer("replace-probe", second)
    entry = get_composer("replace-probe")
    assert entry is not None
    assert entry.format_text({}, []) == "second-renderer"


# Sabotage-proof (executed): made ``get_composer`` raise KeyError for
# unknown names; this test failed because the unknown lookup raised
# instead of returning None. Restored.
def test_get_composer_returns_none_for_unknown_subcommand() -> None:
    """Unknown subcommands return None so the dispatcher can fall through."""
    assert get_composer("definitely-no-such-subcommand-xyz-987") is None


# Sabotage-proof (executed): replaced ``list_registered`` with the
# unsorted dict order; this test failed reporting the entries out of
# order. Restored sorted() call.
def test_list_registered_returns_sorted_tuple() -> None:
    """Diagnostic output is sorted so operators see stable lists."""
    register_composer(
        "zzz-probe-list",
        TextModeComposer(from_envelope=lambda env: env, format_text=lambda r, a: "z", name="zzz-probe-list"),
    )
    register_composer(
        "aaa-probe-list",
        TextModeComposer(from_envelope=lambda env: env, format_text=lambda r, a: "a", name="aaa-probe-list"),
    )
    entries = list_registered()
    assert isinstance(entries, tuple)
    # The two probe entries must appear sorted relative to each other
    assert entries.index("aaa-probe-list") < entries.index("zzz-probe-list")


# Sabotage-proof (executed): changed the registry module to import
# kairix.use_cases.brief at top of file (the would-be circular import
# the brief says to avoid); the import raised ImportError on first
# load. Removed the top-level import.
def test_registry_module_does_not_import_cli_or_use_case_modules() -> None:
    """The registry module is import-leaf — it never imports the CLI/use_case modules.

    The intended import direction is CLI/use_case → registry (registers at import).
    A reverse import would create a circular dependency the moment a CLI module
    is touched at startup.
    """
    import kairix.agents.mcp.text_mode_composers as registry_mod

    src = registry_mod.__file__
    assert src is not None
    with open(src) as fh:
        text = fh.read()
    # The registry must not import any CLI/use_case modules directly
    forbidden = (
        "from kairix.use_cases",
        "from kairix.agents.briefing",
        "from kairix.agents.prep",
        "from kairix.agents.research",
        "from kairix.core.search.cli",
        "from kairix.core.temporal.cli",
        "from kairix.knowledge.contradict",
        "from kairix.bootstrap_cli",
    )
    for f in forbidden:
        assert f not in text, (
            f"text_mode_composers.py must not import {f} (would create a circular import). "
            f"fix: keep registration on the CLI/use_case side; the registry is import-leaf."
        )
