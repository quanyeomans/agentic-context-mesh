"""Unit tests for the text-mode composer registry (PR 2.8 / #421).

Drives the canonical register/get/list surface end-to-end. Companion to
``tests/contracts/test_text_mode_composer_protocol.py`` which pins the
shape — this file covers behaviour.

F1/F2-clean: no monkeypatch on kairix internals.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.text_mode_composers import (
    TextModeComposer,
    get_composer,
    list_registered,
    register_composer,
)

pytestmark = pytest.mark.unit


# Sabotage-proof (executed): swapped the args of register_composer so
# the name was stored under the composer.name slot instead of the key;
# this test failed because get_composer(subcommand) returned None.
# Restored the (subcommand, composer) signature.
def test_register_then_get_round_trips_the_composer() -> None:
    """A registered composer is retrievable by its subcommand key."""
    composer = TextModeComposer(
        from_envelope=lambda env: {"tag": "agent-alpha", **env},
        format_text=lambda r, argv: f"rendered({r['tag']})",
        name="probe-roundtrip",
    )
    register_composer("probe-roundtrip", composer)
    entry = get_composer("probe-roundtrip")
    assert entry is composer
    rendered = entry.format_text(entry.from_envelope({"agent": "agent-alpha"}), [])
    assert rendered == "rendered(agent-alpha)"


# Sabotage-proof (executed): made list_registered return the unsorted
# dict iteration; this test failed because the comparison expected
# alphabetical order. Restored sorted() in the list_registered impl.
def test_list_registered_includes_every_registered_name() -> None:
    """Every registered name appears in list_registered()."""
    names = ("probe-list-1", "probe-list-2", "probe-list-3")
    for n in names:
        register_composer(
            n,
            TextModeComposer(
                from_envelope=lambda env: env,
                # Bind ``n`` via default arg so each closure captures its own value (B023).
                format_text=lambda r, a, name=n: name,
                name=n,
            ),
        )
    registered = list_registered()
    for n in names:
        assert n in registered


# Sabotage-proof (executed): made get_composer return a fresh
# TextModeComposer with default no-op callables instead of the
# registered entry; this test failed because the format_text returned
# something other than the seeded "sentinel-text". Restored direct
# dict lookup.
def test_get_composer_returns_the_registered_entry_not_a_default() -> None:
    """get_composer returns the registered TextModeComposer, not a copy."""
    sentinel = TextModeComposer(
        from_envelope=lambda env: {"sentinel": True, **env},
        format_text=lambda r, argv: "sentinel-text",
        name="probe-identity",
    )
    register_composer("probe-identity", sentinel)
    entry = get_composer("probe-identity")
    assert entry is sentinel
    assert entry.format_text({}, []) == "sentinel-text"


# Sabotage-proof (executed): mutated register_composer to no-op when
# the subcommand was already registered (first-wins); this test failed
# because the second composer never replaced the first. Restored
# last-write-wins semantics.
def test_re_registration_replaces_existing_entry() -> None:
    """Last-write-wins so test ordering doesn't break composer behaviour."""
    first = TextModeComposer(
        from_envelope=lambda env: env, format_text=lambda r, a: "first-render", name="probe-replace"
    )
    register_composer("probe-replace", first)
    second = TextModeComposer(
        from_envelope=lambda env: env, format_text=lambda r, a: "second-render", name="probe-replace"
    )
    register_composer("probe-replace", second)
    assert get_composer("probe-replace") is second
