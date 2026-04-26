"""Unit test: verify embed progress logging is present in the embed pipeline."""

import inspect

import pytest

from kairix.embed import embed as embed_mod


@pytest.mark.unit
def test_embed_progress_logging_present():
    """run_embed must contain per-batch progress logging."""
    src = inspect.getsource(embed_mod.run_embed)
    assert "Embed progress:" in src, "Expected 'Embed progress:' log line in run_embed()"
    # Ensure it references batch index and counts
    assert "batch_idx" in src
    assert "embedded" in src and "total" in src
