"""
Document scanner — discovers, hashes, and ingests markdown files into the kairix database.

Handles scanning document directories, computing
content hashes, and upserting into the documents + content tables.

Usage::

    from kairix.db import open_db
    from kairix.db.scanner import DocumentScanner, CollectionConfig

    db = open_db(extensions=False)
    scanner = DocumentScanner(db, document_root=Path("~/kairix-vault").expanduser())
    report = scanner.scan([
        CollectionConfig(name="doc-areas", path="02-Areas"),
        CollectionConfig(name="doc-knowledge", path="05-Knowledge"),
    ])
    print(report)
"""

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kairix.reflib.frontmatter import extract_title

logger = logging.getLogger(__name__)


@dataclass
class CollectionConfig:
    """Configuration for a single collection to scan."""

    name: str
    path: str  # relative to document_root
    glob: str = "**/*.md"
    exclude: list[str] = field(default_factory=list)


@dataclass
class ScanReport:
    """Summary of a document scan operation."""

    new: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: int = 0
    collections_scanned: int = 0

    @property
    def total_processed(self) -> int:
        return self.new + self.updated + self.unchanged

    def __str__(self) -> str:
        return (
            f"Scan: {self.new} new, {self.updated} updated, "
            f"{self.removed} removed, {self.unchanged} unchanged, "
            f"{self.errors} errors ({self.collections_scanned} collections)"
        )


def _hash_content(text: str) -> str:
    """SHA-256 hex digest of document content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentScanner:
    """
    Scans document directories and ingests documents into the kairix database.

    The scanner is incremental: it compares content hashes to detect changes
    and only updates modified documents.
    """

    def __init__(
        self, db: sqlite3.Connection, document_root: Path | None = None, vault_root: Path | None = None
    ) -> None:
        self._db = db
        self._document_root = document_root or vault_root or Path.home() / "kairix-vault"

    def scan(self, collections: list[CollectionConfig]) -> ScanReport:
        """
        Scan all configured collections and update the database.

        Args:
            collections: List of collection configs defining what to scan.

        Returns:
            ScanReport with counts of new, updated, removed, unchanged documents.
        """
        report = ScanReport()

        for config in collections:
            col_report = self._scan_collection(config)
            report.new += col_report.new
            report.updated += col_report.updated
            report.removed += col_report.removed
            report.unchanged += col_report.unchanged
            report.errors += col_report.errors
            report.collections_scanned += 1

        self._db.commit()
        logger.info("db.scanner: %s", report)
        return report

    def _scan_collection(self, config: CollectionConfig) -> ScanReport:
        """Scan a single collection."""
        report = ScanReport()
        collection_path = self._document_root / config.path

        if not collection_path.exists():
            logger.warning("db.scanner: collection path does not exist: %s", collection_path)
            return report

        # Build exclude set
        exclude_patterns = set(config.exclude)

        # Get existing documents for this collection
        existing = {}
        for row in self._db.execute(
            "SELECT path, hash FROM documents WHERE collection = ? AND active = 1",
            (config.name,),
        ):
            existing[row[0]] = row[1]

        seen_paths: set[str] = set()
        now = datetime.now(tz=timezone.utc).isoformat()

        # Scan files
        for file_path in sorted(collection_path.glob(config.glob)):
            if not file_path.is_file():
                continue

            # Check excludes
            rel_to_vault = str(file_path.relative_to(self._document_root))
            if any(pattern in rel_to_vault for pattern in exclude_patterns):
                continue

            seen_paths.add(rel_to_vault)

            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("db.scanner: cannot read %s — %s", file_path, e)
                report.errors += 1
                continue

            content_hash = _hash_content(text)
            title = extract_title(text, file_path)

            old_hash = existing.get(rel_to_vault)

            if old_hash == content_hash:
                report.unchanged += 1
                continue

            # Upsert document
            self._db.execute(
                """
                INSERT INTO documents (collection, path, title, hash, created_at, modified_at, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(collection, path) DO UPDATE SET
                    title = excluded.title,
                    hash = excluded.hash,
                    modified_at = excluded.modified_at,
                    active = 1
                """,
                (config.name, rel_to_vault, title, content_hash, now, now),
            )

            # Upsert content
            self._db.execute(
                "INSERT OR REPLACE INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
                (content_hash, text, now),
            )

            if old_hash is None:
                report.new += 1
            else:
                report.updated += 1

        # Mark removed documents as inactive
        for path in existing:
            if path not in seen_paths:
                self._db.execute(
                    "UPDATE documents SET active = 0, modified_at = ? WHERE collection = ? AND path = ?",
                    (now, config.name, path),
                )
                report.removed += 1

        return report


# Backwards-compat alias
VaultScanner = DocumentScanner
