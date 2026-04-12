"""
Mnemosyne entity extraction pipeline — orchestration layer.

Coordinates file scanning, NER extraction, ontology reconciliation, and
mention tracking. Supports incremental processing via mtime filtering.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from kairix.entities.extract import extract_file
from kairix.entities.reconcile import reconcile_extracted, update_mentions

logger = logging.getLogger(__name__)

VAULT_ROOT = "/data/obsidian-vault"
AGENT_ROOTS: list[str] = [
    "/data/workspaces/builder/memory",
    "/data/workspaces/shape/memory",
]

# Files and directories to skip (mirrors wikilinks eligibility rules)
_SKIP_DIRS = {
    ".git",
    ".obsidian",
    ".trash",
    "node_modules",
    "__pycache__",
}
_SKIP_PATTERNS = {
    "STYLE.md",
    "README.md",
    "AGENTS.md",
    "SOUL.md",
}

# Metadata key for last pipeline run
_META_KEY_LAST_RUN = "entity_pipeline_last_run"

# Only vault-structure matches (≥0.85) and known entity lookups (≥0.90) auto-write
# to entities.db. Title Case heuristic (0.4) generates too many false positives.
_HIGH_CONFIDENCE_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _ensure_metadata_table(db: sqlite3.Connection) -> None:
    """Create the metadata table if it doesn't exist (idempotent)."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.commit()


def get_last_run_mtime(db: sqlite3.Connection) -> float | None:
    """
    Return the mtime timestamp of the last successful pipeline run.

    Reads from the metadata table. Returns None if no prior run recorded.
    """
    try:
        _ensure_metadata_table(db)
        row = db.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (_META_KEY_LAST_RUN,),
        ).fetchone()
        if row is None:
            return None
        return float(row[0])
    except (sqlite3.OperationalError, ValueError):
        return None


def set_last_run_mtime(db: sqlite3.Connection) -> None:
    """
    Record the current time as the last successful pipeline run mtime.

    Upserts into the metadata table.
    """
    _ensure_metadata_table(db)
    now = datetime.now(timezone.utc).timestamp()
    db.execute(
        """
        INSERT INTO metadata (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (_META_KEY_LAST_RUN, str(now)),
    )
    db.commit()


# ---------------------------------------------------------------------------
# File eligibility
# ---------------------------------------------------------------------------


def _should_process(path: str) -> bool:
    """
    Return True if a file is eligible for entity extraction.

    Mirrors wikilinks eligibility rules:
    - Must be a .md file
    - Must not be inside a skip directory
    - Must not match skip patterns
    """
    p = Path(path)
    if p.suffix != ".md":
        return False
    if p.name in _SKIP_PATTERNS:
        return False
    for part in p.parts:
        if part in _SKIP_DIRS:
            return False
    return True


def get_changed_files(
    vault_root: str = VAULT_ROOT,
    agent_roots: list[str] | None = None,
    since_mtime: float | None = None,
) -> list[str]:
    """
    Return eligible .md files modified since since_mtime.

    Scans vault_root and any agent_roots. Applies eligibility filtering
    (same rules as wikilinks pipeline). If since_mtime is None, returns
    all eligible files.

    Args:
        vault_root:   Root of the Obsidian vault.
        agent_roots:  Additional directories to scan (e.g. agent memory dirs).
        since_mtime:  Unix timestamp; only return files newer than this.

    Returns:
        Sorted list of absolute file paths.
    """
    roots = [vault_root]
    if agent_roots is not None:
        roots.extend(agent_roots)
    else:
        roots.extend(AGENT_ROOTS)

    files: list[str] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        try:
            for path in root_path.rglob("*.md"):
                if not _should_process(str(path)):
                    continue
                if since_mtime is not None:
                    try:
                        mtime = path.stat().st_mtime
                    except (PermissionError, OSError):
                        continue
                    if mtime <= since_mtime:
                        continue
                files.append(str(path))
        except (PermissionError, OSError) as exc:
            logger.warning("get_changed_files: cannot scan %s: %s", root, exc)

    return sorted(files)


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def _file_hash(path: str) -> str:
    """Return a short MD5 hash of a file's contents."""
    try:
        content = Path(path).read_bytes()
        return hashlib.md5(content).hexdigest()[:12]  # noqa: S324  # nosec B324
    except (PermissionError, OSError):
        return "unknown"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_extraction_pipeline(
    paths: list[str],
    db: sqlite3.Connection,
    use_llm: bool = False,
    batch_size: int = 50,
    since_mtime: float | None = None,
    api_key: str | None = None,
    endpoint: str | None = None,
) -> dict:
    """
    Full extraction pipeline over a list of files.

    Steps per file:
    1. Skip if mtime <= since_mtime (already processed).
    2. extract_file() → ExtractedEntity list.
    3. reconcile_extracted() → entity_id mapping.
    4. update_mentions() → entity_mentions rows.
    5. Log result per file.

    Args:
        paths:        List of absolute file paths to process.
        db:           Open entities DB connection.
        use_llm:      Enable LLM extraction fallback.
        batch_size:   Number of files per batch (for progress logging).
        since_mtime:  Skip files with mtime <= this timestamp.
        api_key:      Azure OpenAI API key (required if use_llm=True).
        endpoint:     Azure OpenAI endpoint (required if use_llm=True).

    Returns:
        Summary dict: {processed, skipped, new_entities, merged, mentions_written, errors}
    """
    summary: dict[str, int] = {
        "processed": 0,
        "skipped": 0,
        "new_entities": 0,
        "merged": 0,
        "mentions_written": 0,
        "errors": 0,
    }

    total = len(paths)
    for i, path in enumerate(paths):
        # Progress logging per batch
        if i > 0 and i % batch_size == 0:
            logger.info(
                "pipeline: %d/%d files processed (%d new entities, %d merged)",
                i,
                total,
                summary["new_entities"],
                summary["merged"],
            )

        # Step 1: mtime filter
        if since_mtime is not None:
            try:
                mtime = Path(path).stat().st_mtime
            except (PermissionError, OSError):
                summary["errors"] += 1
                continue
            if mtime <= since_mtime:
                summary["skipped"] += 1
                continue

        # Step 2: Extract
        try:
            extracted = extract_file(
                path,
                db,
                use_llm=use_llm,
                api_key=api_key,
                endpoint=endpoint,
            )
        except Exception as exc:
            logger.error("pipeline: extraction failed for %s: %s", path, exc)
            summary["errors"] += 1
            continue

        if not extracted:
            summary["skipped"] += 1
            continue

        # Step 3: Reconcile
        # Only pass high-confidence entities (vault structure match ≥ 0.85,
        # known entity lookup ≥ 0.90) to reconciler. Title Case heuristic
        # (confidence=0.4) generates too many false positives from body text —
        # it is useful for LLM refinement passes but must not auto-write to DB.

        high_conf = [e for e in extracted if e.confidence >= _HIGH_CONFIDENCE_THRESHOLD]
        if not high_conf:
            summary["skipped"] += 1
            continue
        extracted = high_conf

        try:
            # Count entities before reconciliation to detect new ones
            before_count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            entity_map = reconcile_extracted(extracted, db)
            after_count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

            new_count = after_count - before_count
            merged_count = len(entity_map) - new_count
            summary["new_entities"] += max(0, new_count)
            summary["merged"] += max(0, merged_count)
        except Exception as exc:
            logger.error("pipeline: reconciliation failed for %s: %s", path, exc)
            summary["errors"] += 1
            continue

        # Step 4: Update mentions
        try:
            doc_hash = _file_hash(path)
            entity_ids = list(entity_map.values())
            update_mentions(entity_ids, doc_hash, path, db)
            summary["mentions_written"] += len(entity_ids)
        except Exception as exc:
            logger.error("pipeline: mention update failed for %s: %s", path, exc)
            summary["errors"] += 1
            continue

        summary["processed"] += 1
        logger.debug(
            "pipeline: %s → %d entities (%d new, %d mentions)",
            path,
            len(entity_map),
            new_count,
            len(entity_ids),
        )

    logger.info(
        "pipeline: complete — %d processed, %d skipped, %d new entities, %d merged, %d mentions, %d errors",
        summary["processed"],
        summary["skipped"],
        summary["new_entities"],
        summary["merged"],
        summary["mentions_written"],
        summary["errors"],
    )
    return summary
