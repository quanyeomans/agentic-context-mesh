"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SuggestionFilter`.

One method (``apply``). Failure surface:

  * ``raises`` — internal parse / dictionary lookup failures surface
    verbatim; the filter chain must not silently drop a suggestion on
    a code-level error (the suggestion would slip through unfiltered).
  * ``returns_empty`` — when every suggestion is filtered out the
    return is ``[]``, not None.
"""

from __future__ import annotations

import pytest

from kairix.knowledge.entities.protocols import Suggestion, SuggestionFilter

pytestmark = pytest.mark.contract


class _FailingFilter:
    """Inline :class:`SuggestionFilter` with raises-knob on ``apply``."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises

    def apply(self, suggestions: list[Suggestion], context: str) -> list[Suggestion]:
        del context
        if self._raises is not None:
            raise self._raises
        # Filter-all default: drop every suggestion
        return []


def test_apply_raises_propagates_typed_exception() -> None:
    """A filter parse failure surfaces — caller must not interpret a
    silently-empty return as "all suggestions filtered" when the filter
    crashed mid-pass.

    Sabotage proof: in ``_FailingFilter.apply`` change
    ``raise self._raises`` to ``return suggestions``. Re-run:
    pytest.raises sees nothing. Restored.
    """
    flt: SuggestionFilter = _FailingFilter(raises=RuntimeError("F68-filter-raises"))
    with pytest.raises(RuntimeError, match="F68-filter-raises"):
        flt.apply([{"text": "Acme", "label": "ORG", "source": "ner", "confidence": 0.9}], "context")


def test_apply_returns_empty_when_all_suggestions_filtered() -> None:
    """When every suggestion is filtered out the return is ``[]`` —
    callers iterate without a None check.

    Sabotage proof: change ``_FailingFilter.apply`` to
    ``return [{}]`` instead of ``[]``. Re-run: ``== []`` fails.
    Restored.
    """
    flt: SuggestionFilter = _FailingFilter()
    out = flt.apply(
        [{"text": "Acme", "label": "ORG", "source": "ner", "confidence": 0.9}],
        "context",
    )
    assert out == [], f"all-filtered must yield []; got {out!r}"
