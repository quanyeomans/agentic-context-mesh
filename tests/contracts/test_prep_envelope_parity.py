"""Contract: ``PrepOutput`` <-> envelope round-trip preserves rendered text.

PR 2.4 / #421 — warm-MCP text-mode routing for ``kairix prep``.

After this PR the CLI dispatcher can route ``kairix prep <query>`` to a
warm MCP worker even when ``--json`` is not on argv. The dispatcher
receives a JSON envelope (the same dict ``tool_prep`` returns); to
render the operator-facing text it converts envelope -> ``PrepOutput``
via ``PrepOutput.from_envelope`` and calls the existing ``format_text``.
That seam MUST produce byte-identical text to the in-process path —
otherwise warm-MCP routing silently changes operator output.

This contract pins that round-trip at the byte level for every relevant
shape (empty / with-sources / with-error / numeric edges). Production
callers never construct ``PrepOutput`` from a dict directly; the test
goes through the public surface (``prep_output_to_envelope`` +
``PrepOutput.from_envelope``) so the contract documents the supported
shape and breaks loudly when either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.agents.prep.cli import format_text
from kairix.core.protocols import SourceRef
from kairix.use_cases.prep import PrepOutput, prep_output_to_envelope

pytestmark = pytest.mark.contract


def _src(name: str) -> SourceRef:
    """A SourceRef whose source_uri == path == ``name`` (PLA-274 — prep
    sources are now resolvable breadcrumbs, not bare title strings). The
    label the renderer shows is ``name`` so the ordering anchors below
    still locate it by substring."""
    return SourceRef.of(path=name)


def _roundtrip(out: PrepOutput) -> PrepOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = prep_output_to_envelope(out)
    return PrepOutput.from_envelope(envelope)


# Sabotage-proof (executed): dropped the ``summary`` key from
# ``from_envelope`` (defaulted to ""), test failed on the byte-equality
# assertion because the rebuilt format_text had empty body; restored.
def test_roundtrip_preserves_text_with_summary_and_sources() -> None:
    original = PrepOutput(
        query="alpha topic",
        tier="l0",
        summary="Alpha is a sample document discussing the topic in detail.",
        tokens=12,
        sources=[_src("doc-alpha"), _src("doc-beta")],
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): mutated ``from_envelope`` to coerce
# ``sources`` to a fixed ``["wrong"]``; the rendered Sources block
# carried wrong entries and equality fired; restored.
def test_roundtrip_preserves_text_with_sources_list_ordering() -> None:
    original = PrepOutput(
        query="ordering matters",
        tier="l1",
        summary="A structured overview of the ordering invariant.",
        tokens=20,
        sources=[_src("doc-z"), _src("doc-a"), _src("doc-m")],
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # Anchor that ordering survives the JSON-shaped round-trip — the
    # Sources block is rendered in iteration order, not alphabetic.
    assert rendered_rebuilt.index("doc-z") < rendered_rebuilt.index("doc-a")
    assert rendered_rebuilt.index("doc-a") < rendered_rebuilt.index("doc-m")


# Sabotage-proof (executed): made ``from_envelope`` always default
# sources to ``["accidental"]`` even when the envelope was empty; the
# empty-sources branch then rendered a "Sources:" header and equality
# fired; restored.
def test_roundtrip_preserves_text_with_empty_sources_no_section() -> None:
    original = PrepOutput(
        query="lonely query",
        tier="l0",
        summary="No relevant documents found for this topic.",
        tokens=8,
        sources=[],
    )
    rebuilt = _roundtrip(original)
    rendered = format_text(rebuilt)
    assert format_text(original) == rendered
    # When sources is empty the Sources block must not appear — anchor
    # that the round-trip doesn't accidentally synthesise one.
    assert "Sources:" not in rendered


# Sabotage-proof (executed): mutated ``from_envelope`` to ignore the
# ``error`` key (hard-coded to ""); the error branch in format_text
# returned the summary instead of the ``"error: ..."`` line and
# equality fired; restored.
def test_roundtrip_preserves_text_with_error_short_circuit() -> None:
    original = PrepOutput(
        query="failed query",
        tier="l0",
        summary="",
        tokens=0,
        sources=[],
        error="RuntimeError: backend unavailable",
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # The error short-circuit must dominate — even though summary is
    # empty, the rendered text is the error line, not "".
    assert rendered_rebuilt.startswith("error:")
    assert "RuntimeError" in rendered_rebuilt


# Sabotage-proof (executed): removed every key extraction except
# ``query`` from ``from_envelope``; the structural-field assertions
# fired on ``rebuilt.tier`` first; restored.
def test_roundtrip_preserves_structural_fields() -> None:
    original = PrepOutput(
        query="structural",
        tier="l1",
        summary="body",
        tokens=99,
        sources=[_src("s1"), _src("s2")],
        error="",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.query == original.query
    assert rebuilt.tier == original.tier
    assert rebuilt.summary == original.summary
    assert rebuilt.tokens == original.tokens
    assert rebuilt.sources == original.sources
    assert rebuilt.error == original.error


# Sabotage-proof (executed): changed ``int(envelope.get("tokens", 0))``
# to ``int(envelope.get("tokens", 0)) + 1``; the equality assertion on
# tokens fired with 43 != 42; restored.
def test_roundtrip_preserves_tokens_field_as_int() -> None:
    """Tokens round-trips through JSON as int (not str / not float)."""
    original = PrepOutput(query="q", tier="l0", summary="s", tokens=42, sources=[])
    rebuilt = _roundtrip(original)
    assert rebuilt.tokens == 42
    assert isinstance(rebuilt.tokens, int)
