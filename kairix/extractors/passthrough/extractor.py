"""Passthrough extractor — markdown / plain-text bytes-to-:class:`ExtractedDocument`.

The simplest possible :class:`kairix.extractors.Extractor` impl. Used
for sources whose native format is already markdown (Obsidian vaults,
``.txt`` notes, hand-authored ``.md`` files). No conversion is
performed; ``extract`` decodes the raw UTF-8 bytes and wraps them in
an :class:`ExtractedDocument` with empty ``pages`` / ``images`` and
minimal :class:`DocMetadata`.

Quality is determined by a single check: ``markdown.strip()`` is non-
empty. An empty file is not a useful chunk source; ``quality_ok`` is
``False`` and the orchestrator skips the document.

See ``docs/architecture/connector-ingestion-architecture.md`` §3 for
the :class:`Extractor` Protocol this plugin satisfies, and §10 for
the IM-4 wave entry that ships this extractor alongside markitdown.
"""

from __future__ import annotations

from kairix.extractors import (
    DocMetadata,
    ExtractedDocument,
    MimeType,
)

#: Canonical name surfaced by the entry-point registry.
PLUGIN_NAME = "passthrough"

#: Mime types the passthrough plugin claims. The ``text/*`` prefix
#: covers ``text/markdown`` and ``text/plain`` per the spec, plus any
#: variant a source labels its plain-text content with
#: (``text/x-markdown``, ``text/x-rst``, …). Non-``text/*`` mime types
#: (PDF, DOCX, HTML) fall through to other extractors in the chain.
_TEXT_PREFIX = "text/"


class PassthroughExtractor:
    """:class:`Extractor` impl for markdown / plain-text content.

    No upstream library is wrapped — this plugin's whole job is to
    decode UTF-8 bytes and surface them as :class:`ExtractedDocument`.
    Per F40, the plugin's ``version`` is declared at the package
    ``__init__`` module level; this class accepts it via the
    constructor so the same instance carries the version through to
    ``documents_media.extractor_version`` on every produced document.
    """

    def __init__(self, *, version: str) -> None:
        """Construct the extractor with an explicit ``version`` string.

        ``version`` is the F40-mandated module-level constant defined
        in :mod:`kairix.extractors.passthrough` — passed through the
        constructor so the value flows from one canonical declaration
        site, not duplicated inside the class body.
        """
        self.name: str = PLUGIN_NAME
        self.version: str = version

    def can_extract(self, mime: MimeType, _magic_bytes: bytes) -> bool:
        """``True`` for any ``text/*`` mime type.

        ``_magic_bytes`` is ignored — text is too unstructured to
        identify by leading bytes (UTF-8 BOM is optional, plain
        markdown has no header). The mime hint from the connector
        (``text/markdown`` for ``.md``, ``text/plain`` for ``.txt``)
        is the authoritative signal here. The ``_``-prefix on the
        parameter name is the F19 signal that the position is held
        for Protocol compatibility while the implementation does not
        consult the value.
        """
        return mime.startswith(_TEXT_PREFIX)

    def extract(self, raw: bytes, _mime: MimeType) -> ExtractedDocument:
        """Decode ``raw`` as UTF-8 and wrap in :class:`ExtractedDocument`.

        Decoding errors are replaced (``errors='replace'``) so a stray
        non-UTF-8 byte in an otherwise-text file doesn't blow up the
        whole pipeline. ``confidence`` is ``1.0`` because there is no
        format detection or OCR step that could be uncertain — the
        bytes are the markdown.

        ``_mime`` is ``_``-prefixed (F19) because the caller already
        filtered via :meth:`can_extract`; post-decode the mime hint
        carries no extra information.

        ``pages`` and ``images`` are empty: text files have no native
        page / slide / sheet structure and no embedded images. The
        :class:`DocMetadata` carries no title / author / language —
        callers wanting those derive them from the connector-side
        front-matter parser, not this extractor.
        """
        text = raw.decode("utf-8", errors="replace")
        return ExtractedDocument(
            markdown=text,
            pages=(),
            images=(),
            metadata=DocMetadata(
                title=None,
                author=None,
                created_date=None,
                language=None,
                page_count=None,
            ),
            confidence=1.0,
        )

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """``True`` if the document's markdown has any non-whitespace content.

        Empty / whitespace-only files are skipped by the orchestrator
        — they produce no useful chunks and would only pollute the
        retrieval index with zero-vector embeddings. Per the
        escalation-chain semantics (spec §4 "Three failures map to
        three behaviours"), a passthrough ``quality_ok = False`` lands
        the item in ``connector_deadletter`` after retries since text
        has no alternate extractor to escalate to.
        """
        return bool(doc.markdown.strip())
