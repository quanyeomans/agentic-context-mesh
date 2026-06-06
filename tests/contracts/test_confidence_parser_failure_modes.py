"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ConfidenceParser`.

Single Protocol method ``parse(response)``. The Protocol docstring is
explicit: implementations MUST raise :class:`ConfidenceParseError` for
unparseable input, NOT silently return ``0.0`` (which is the bug this
Protocol was created to fix).

We probe the shipped :class:`JsonModeConfidenceParser` and
:class:`RegexExtractConfidenceParser` since their failure shape is
the canonical Protocol contract proof.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from kairix.agents.research.confidence import JsonModeConfidenceParser, RegexExtractConfidenceParser
from kairix.agents.research.protocols import ConfidenceParseError

pytestmark = pytest.mark.contract


def test_parse_raises_on_invalid_json_for_json_mode_parser() -> None:
    """:class:`JsonModeConfidenceParser` must raise on non-JSON input —
    silently returning 0.0 was the original bug.

    Sabotage proof: in :meth:`JsonModeConfidenceParser.parse` change
    the ``raise ConfidenceParseError(...)`` in the JSONDecodeError
    branch to ``return 0.0``. Re-run: the test fails because no
    exception is raised. Restored.
    """
    parser = JsonModeConfidenceParser()
    with pytest.raises(ConfidenceParseError, match="not valid JSON"):
        parser.parse("this is not JSON at all")


def test_parse_raises_on_missing_value_for_regex_parser() -> None:
    """:class:`RegexExtractConfidenceParser` must raise when no
    confidence-shaped substring appears — silent fallback to 0.0
    would mask LLM non-compliance.

    Sabotage proof: in :meth:`RegexExtractConfidenceParser.parse`
    change ``raise ConfidenceParseError(...)`` to ``return 0.0``.
    Re-run: the test fails because no exception is raised. Restored.
    """
    parser = RegexExtractConfidenceParser()
    with pytest.raises(ConfidenceParseError, match="no confidence-shaped value"):
        parser.parse("The agent responded with prose but no number.")
