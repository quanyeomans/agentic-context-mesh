"""
One-time migration from QMD's SQLite index to kairix's own database.

Usage::

    kairix db migrate-from-qmd [--qmd-db PATH]

Migrates all documents, content, content_vectors, and vectors from QMD's
``index.sqlite`` into kairix's database using SQLite ``ATTACH DATABASE``.
Non-destructive — the QMD database is never modified.
"""

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import load_extensions
from .fts import rebuild_fts
from .schema import create_schema

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    """Summary of a QMD-to-kairix migration."""

    documents: int = 0
    content_rows: int = 0
    content_vectors: int = 0
    vectors: int = 0
    fts_indexed: int = 0

    def __str__(self) -> str:
        return (
            f"Migration: {self.documents} documents, {self.content_rows} content rows, "
            f"{self.content_vectors} content_vectors, {self.vectors} vectors, "
            f"{self.fts_indexed} FTS indexed"
        )


def migrate_from_qmd(
    kairix_db_path: Path,
    qmd_db_path: Path,
) -> MigrationReport:
    """
    Migrate all data from QMD's index.sqlite to a kairix-owned database.

    Creates the kairix schema if it does not exist, then copies data
    using ``ATTACH DATABASE`` + ``INSERT OR IGNORE`` for atomic, non-destructive
    migration.

    Args:
        kairix_db_path: Path to the target kairix database (created if missing).
        qmd_db_path:    Path to the source QMD database (read-only).

    Returns:
        MigrationReport with row counts.

    Raises:
        FileNotFoundError: If qmd_db_path does not exist.
    """
    if not qmd_db_path.exists():
        raise FileNotFoundError(f"QMD database not found at {qmd_db_path}")

    report = MigrationReport()

    # Ensure target directory exists
    kairix_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Open kairix DB with sqlite-vec
    db = sqlite3.connect(str(kairix_db_path), timeout=30.0)
    db.execute("PRAGMA journal_mode=WAL")
    load_extensions(db)
    create_schema(db)

    # Attach QMD database
    db.execute("ATTACH DATABASE ? AS qmd", (str(qmd_db_path),))

    try:
        # Copy documents
        db.execute("""
            INSERT OR IGNORE INTO documents (id, collection, path, title, hash, created_at, modified_at, active)
            SELECT id, collection, path, title, hash, created_at, modified_at, active
            FROM qmd.documents
        """)
        report.documents = db.execute("SELECT changes()").fetchone()[0]

        # Copy content
        db.execute("""
            INSERT OR IGNORE INTO content (hash, doc, created_at)
            SELECT hash, doc, created_at
            FROM qmd.content
        """)
        report.content_rows = db.execute("SELECT changes()").fetchone()[0]

        # Copy content_vectors — handle chunk_date column which may or may not exist in QMD
        qmd_cv_cols = {row[1] for row in db.execute("PRAGMA qmd.table_info(content_vectors)")}
        if "chunk_date" in qmd_cv_cols:
            db.execute("""
                INSERT OR IGNORE INTO content_vectors (hash, seq, pos, model, embedded_at, chunk_date)
                SELECT hash, seq, pos, model, embedded_at, chunk_date
                FROM qmd.content_vectors
            """)
        else:
            db.execute("""
                INSERT OR IGNORE INTO content_vectors (hash, seq, pos, model, embedded_at)
                SELECT hash, seq, pos, model, embedded_at
                FROM qmd.content_vectors
            """)
        report.content_vectors = db.execute("SELECT changes()").fetchone()[0]

        # Copy vectors — requires sqlite-vec loaded on both connections
        # Check if QMD has vectors_vec table
        qmd_has_vec = db.execute("SELECT 1 FROM qmd.sqlite_master WHERE name='vectors_vec'").fetchone()

        if qmd_has_vec:
            db.execute("""
                INSERT OR IGNORE INTO vectors_vec (hash_seq, embedding)
                SELECT hash_seq, embedding
                FROM qmd.vectors_vec
            """)
            report.vectors = db.execute("SELECT COUNT(*) FROM vectors_vec").fetchone()[0]

        # Rebuild FTS index
        report.fts_indexed = rebuild_fts(db)

        db.commit()

    finally:
        db.execute("DETACH DATABASE qmd")

    db.close()

    logger.info("db.migrate: %s", report)
    return report
