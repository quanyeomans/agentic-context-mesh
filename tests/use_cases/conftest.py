"""Per-directory fixtures for kairix use-case tests.

Resets the prep-summary cache between tests so a previous case's
cached LLM summary doesn't short-circuit the chat fn in the next
case (#396 W-B C3). The cache is a process-shared state added in
``kairix.use_cases.prep``; without this reset, two tests that pass
the same query / tier / context block see different chat-fn call
counts depending on order.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_prep_summary_cache() -> None:
    """Each test in tests/use_cases/ starts with a clean prep summary cache."""
    from kairix.use_cases.prep import reset_prep_summary_cache

    reset_prep_summary_cache()
    yield
    reset_prep_summary_cache()
