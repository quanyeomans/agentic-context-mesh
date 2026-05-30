"""EmailThreadChunker plugin — thread-as-document / message-as-chunk splitter.

Used by Gmail and M365 email-headers connectors (ADR-028 §"Email —
`EmailThreadChunker`"). Treats one email thread as one document and
one message as one chunk; quoted-reply chains stripped so the same
text doesn't appear N times across N replies; subject / sender /
thread-id / date carried as a metadata prefix per message.

Targets:
  * 1024 token cap per chunk (proxy: 4096 characters; 1 token ~ 4
    chars). Sub-split only when a single message exceeds the cap.
  * 0-50 tokens overlap (proxy: 0 chars at default). Boundary IS the
    message — no need to bleed.

F55: declares module-level ``version: str``; the
:class:`EmailThreadChunker` instance passes
``chunker_version=self.version`` through to every emitted Chunk.
"""

from __future__ import annotations

from kairix.chunkers.email_thread.chunker import (
    PLUGIN_NAME,
    EmailThreadChunker,
)

#: F55-mandated module-level version. Bump on behaviour changes
#: (e.g. swapping the quoted-reply stripper, raising the per-message
#: cap, including signature blocks).
version: str = "0.1.0"


def make_chunker() -> EmailThreadChunker:
    """Construct the thread-aware :class:`EmailThreadChunker`.

    The constructor receives ``version=`` from this module's
    :data:`version` so the F55 declaration site stays canonical.
    """
    return EmailThreadChunker(version=version)


__all__ = [
    "PLUGIN_NAME",
    "EmailThreadChunker",
    "make_chunker",
    "version",
]
