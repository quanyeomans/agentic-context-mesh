"""EmbedPipeline — orchestrator for the embedding pipeline.

Wraps the procedural run_embed() function in a composable dataclass.
Each dependency is injectable at construction time for testing.

Production code uses build_embed_pipeline() from kairix.core.factory;
tests construct EmbedPipeline directly with fakes.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from kairix.core.embed.deps import EmbedDependencies

logger = logging.getLogger(__name__)


@dataclass
class EmbedPipeline:
    """Composes embedding dependencies into a runnable pipeline.

    Constructed once via the factory (or directly in tests with fakes).
    Call run() to embed pending document chunks.
    """

    db: sqlite3.Connection
    deps: EmbedDependencies

    def run(
        self,
        force: bool = False,
        batch_size: int = 250,
        limit: int | None = None,
        parallel: int = 1,
        wal_checkpoint_every_n_batches: int = 10,
    ) -> dict[str, Any]:
        """Embed pending chunks and return a summary dict.

        Args:
            force:      Re-embed all chunks, not just pending.
            batch_size: Chunks per API call.
            limit:      Cap total chunks (for validation/testing).
            parallel:   Number of batches to embed concurrently (1..10).
                        Default 1 = serial. See
                        docs/operations/runbooks/worker-memory-and-swap.md.
            wal_checkpoint_every_n_batches:
                        Issue ``PRAGMA wal_checkpoint(PASSIVE)`` after
                        every Nth committed batch (GH #394). Default 10.
                        0 disables. Tests inject smaller values for
                        deterministic per-batch assertions.

        Returns:
            Dict with keys: embedded, skipped, failed, duration_s,
            estimated_cost_usd, total_chunks, chunk_date_count, failed_paths.
        """
        from kairix.core.embed.embed import run_embed

        return run_embed(
            db=self.db,
            force=force,
            batch_size=batch_size,
            limit=limit,
            deps=self.deps,
            parallel=parallel,
            wal_checkpoint_every_n_batches=wal_checkpoint_every_n_batches,
        )
