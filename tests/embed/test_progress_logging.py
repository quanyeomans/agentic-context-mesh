"""Unit test: verify embed progress logging is present in the embed pipeline."""

import inspect

import pytest

from kairix.embed import embed as embed_mod


@pytest.mark.unit
def test_embed_progress_logging_present():
    """run_embed must contain per-batch progress logging."""
    src = inspect.getsource(embed_mod.run_embed)
    assert "Embed progress:" in src, "Expected 'Embed progress:' log line in run_embed()"
    # Ensure it logs batch index, counts, and percentage
    assert "batch_idx + 1" in src
    assert "embedded, total" in src
