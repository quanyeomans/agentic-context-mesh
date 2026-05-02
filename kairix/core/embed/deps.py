"""Dependency container for the embedding pipeline.

Production code calls run_embed() without deps — defaults are wired to real services.
Tests construct EmbedDependencies with fakes — no monkey-patching needed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class EmbedDependencies:
    """Injectable dependencies for run_embed().

    Each field defaults to the production implementation.
    Tests override specific fields with fakes.
    """

    get_azure_config: Callable[[], tuple[str, str, str]] | None = None
    preflight_check: Callable[[str, str, str], int] | None = None
    embed_batch: Callable[..., list[list[float]]] | None = None
    open_usearch_index: Callable[[], object | None] | None = None
    migrate_content_vectors: Callable[[sqlite3.Connection], None] | None = None
    get_document_root: Callable[[], str | None] | None = None

    def __post_init__(self) -> None:
        """Wire production defaults for any unset dependencies."""
        if self.get_azure_config is None:
            from kairix.core.embed.embed import _get_azure_config

            self.get_azure_config = _get_azure_config
        if self.preflight_check is None:
            from kairix.core.embed.embed import preflight_check

            self.preflight_check = preflight_check
        if self.embed_batch is None:
            from kairix.core.embed.embed import embed_batch

            self.embed_batch = embed_batch
        if self.open_usearch_index is None:
            from kairix.core.embed.embed import _open_usearch_index

            self.open_usearch_index = _open_usearch_index
        if self.migrate_content_vectors is None:
            from kairix.core.embed.schema import migrate_content_vectors

            self.migrate_content_vectors = migrate_content_vectors
        if self.get_document_root is None:

            def _default_doc_root() -> str | None:
                try:
                    from kairix.paths import document_root

                    return str(document_root())
                except Exception:
                    return None

            self.get_document_root = _default_doc_root
