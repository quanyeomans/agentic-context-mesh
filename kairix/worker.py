"""Background worker for scheduled tasks.

Runs inside the kairix-worker Docker container. Handles:
- Incremental document indexing (every hour)
- Entity relationship seeding (once a day at 3am)
- Health check logging (every 6 hours)

Usage:
    python -m kairix.worker
    # Or via Docker: docker compose exec kairix-worker worker
"""

from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Task schedule (seconds between runs)
EMBED_INTERVAL = 3600  # 1 hour
ENTITY_SEED_INTERVAL = 86400  # 24 hours
HEALTH_CHECK_INTERVAL = 21600  # 6 hours


def _run_embed() -> None:
    """Run incremental embed — indexes new and changed documents."""
    try:
        from kairix.embed.cli import main as embed_main

        logger.info("worker: starting incremental embed")
        embed_main()
        logger.info("worker: embed complete")
    except Exception as exc:
        logger.warning("worker: embed failed — %s", exc)


def _run_entity_seed() -> None:
    """Run entity relationship seeding from vault structure."""
    try:
        from kairix.store.cli import main as store_main

        logger.info("worker: starting entity seed")
        store_main(["crawl", "--document-root", os.environ.get("KAIRIX_DOCUMENT_ROOT", str(Path.home() / "Documents"))])
        logger.info("worker: entity seed complete")
    except Exception as exc:
        logger.warning("worker: entity seed failed — %s", exc)


def _run_health_check() -> None:
    """Log a health check."""
    try:
        from kairix.onboard.check import run_all_checks

        results = run_all_checks()
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        logger.info("worker: health check %d/%d passed", passed, total)
    except Exception as exc:
        logger.warning("worker: health check failed — %s", exc)


def main() -> None:
    """Run the worker loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("kairix worker starting — embed every %ds, entity seed every %ds", EMBED_INTERVAL, ENTITY_SEED_INTERVAL)

    # Track when each task last ran
    last_embed = 0.0
    last_entity = 0.0
    last_health = 0.0

    # Graceful shutdown
    running = True

    def _shutdown(signum: int, frame: object) -> None:
        nonlocal running
        logger.info("worker: shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Run embed immediately on startup
    _run_embed()
    last_embed = time.monotonic()

    while running:
        now = time.monotonic()

        if now - last_embed >= EMBED_INTERVAL:
            _run_embed()
            last_embed = now

        if now - last_entity >= ENTITY_SEED_INTERVAL:
            _run_entity_seed()
            last_entity = now

        if now - last_health >= HEALTH_CHECK_INTERVAL:
            _run_health_check()
            last_health = now

        # Sleep 60 seconds between checks
        for _ in range(60):
            if not running:
                break
            time.sleep(1)

    logger.info("kairix worker stopped")


if __name__ == "__main__":
    main()
