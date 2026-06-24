"""File-compatibility classifier — a pre-extract triage gate.

Some source connectors (SharePoint via Microsoft Graph, most notably)
hand the pipeline files whose declared MIME is unreliable: Graph
frequently omits the ``Content-Type`` so the connector falls back to
``application/octet-stream`` (see
``kairix.connectors.sharepoint.connector.DEFAULT_FETCH_MIME``). Two
failure modes follow:

* **Genuinely-unprocessable binaries** (``.exe``, ``.vsdx``, true
  ``.zip`` archives, legacy binary Office) reach ``extractor.extract``,
  raise, and land in the dead-letter queue. After ``failure_count >=
  threshold`` they poison and the operator sees a dead-letter backlog
  that no retry can ever clear.
* **Office documents with a stripped Content-Type** arrive as
  ``application/octet-stream`` even though they are perfectly-valid
  OOXML (a ``.docx`` is a ZIP container with a ``word/`` member). The
  generic MIME means the docx/markitdown extractors never claim them.

:func:`classify_compat` resolves both by inspecting the strongest
available signal — magic bytes, then MIME, then filename extension as a
tiebreak — and returning a :class:`CompatResult` the pipeline uses to
(a) *skip* known-unsupported formats BEFORE extraction (no extract
attempt, no dead-letter), or (b) *correct* the MIME for an
OOXML-document-that-looks-like-a-ZIP so it routes to the right
extractor.

Design constraints:

* **stdlib only** — ``zipfile`` + ``io``; no new dependencies. The
  module is pure and side-effect-free.
* **Conservative by construction** — the extension signal is a
  *tiebreak* that can only push toward ``KNOWN_UNSUPPORTED``; it never
  *upgrades* an item to ``SUPPORTED``. Anything we cannot positively
  classify returns ``UNKNOWN`` (NOT ``KNOWN_UNSUPPORTED``) so the
  existing extract path still runs — today's behaviour for
  mislabeled-but-valid files is preserved.
"""

from __future__ import annotations

import enum
import io
import zipfile
from dataclasses import dataclass

# --- OOXML (Office Open XML) MIME types -----------------------------------
# The three modern Office container MIMEs. A docx/pptx/xlsx is a ZIP whose
# namelist carries a tell-tale top-level directory (word/ ppt/ xl/).
_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# --- magic byte signatures -------------------------------------------------
_MAGIC_PDF = b"%PDF"
_MAGIC_ZIP = b"PK\x03\x04"
_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_GIF87 = b"GIF87a"
_MAGIC_GIF89 = b"GIF89a"

# --- supported MIME universe (when magic gives nothing decisive) ----------
# Union of every wired extractor's ``can_extract`` MIME set
# (passthrough text/*, pdf, the OOXML trio, html, images). ``text/*`` is
# handled by prefix below; the rest are exact matches.
_SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        _MIME_DOCX,
        _MIME_PPTX,
        _MIME_XLSX,
        "text/html",
        "application/xhtml+xml",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/tiff",
        "image/bmp",
    }
)

# --- known-unsupported MIME prefixes / exacts -----------------------------
# Formats with no extractor and no prospect of one in this wave: legacy
# binary Office (.doc/.ppt/.xls via msword etc.), Visio, executables,
# ODF, MS Publisher. Skipping these pre-extract keeps them out of the
# dead-letter queue.
_KNOWN_UNSUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
        "application/x-msdownload",
        "application/vnd.ms-publisher",
    }
)
_KNOWN_UNSUPPORTED_MIME_PREFIXES: tuple[str, ...] = (
    "application/vnd.ms-visio",
    "application/vnd.oasis.opendocument.",  # ODF: odt / ods / odp
)

# --- known-unsupported extensions (TIEBREAK only — never upgrades) --------
_KNOWN_UNSUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".dll",
        ".vsd",
        ".vsdx",
        ".msg",
        ".odt",
        ".ods",
        ".odp",
        ".pub",
    }
)


class Compat(enum.Enum):
    """Pre-extract compatibility verdict for one item.

    * ``SUPPORTED`` — a wired extractor can handle this format; route to
      extraction as normal (with :attr:`CompatResult.effective_mime`).
    * ``KNOWN_UNSUPPORTED`` — positively identified as a format with no
      extractor (true archive, legacy binary Office, executable, ODF,
      corrupt ZIP). The pipeline SKIPS extraction and records the item
      so it is consumed without dead-lettering.
    * ``UNKNOWN`` — could not positively classify (generic
      octet-stream, unrecognised MIME, no decisive magic/extension).
      The pipeline falls through to the existing extract path so a
      mislabeled-but-valid file still gets a chance.
    """

    SUPPORTED = "supported"
    KNOWN_UNSUPPORTED = "known_unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompatResult:
    """Outcome of :func:`classify_compat`.

    ``effective_mime`` is the MIME the pipeline should route extraction
    with. It equals the input ``mime`` in every case EXCEPT the
    OOXML-from-ZIP disambiguation branch, where a ZIP container whose
    namelist proves it is a docx/pptx/xlsx is re-labelled with the
    correct OOXML MIME so the format-specific extractor claims it.
    """

    compat: Compat
    effective_mime: str


def _classify_zip(data: bytes, mime: str) -> CompatResult:
    """Disambiguate a ``PK\\x03\\x04``-prefixed payload.

    OOXML Office documents are ZIP containers; a true archive is also a
    ZIP. We open the central directory and look for the tell-tale
    top-level member (``word/`` / ``ppt/`` / ``xl/``). A document maps
    to ``SUPPORTED`` with the corrected OOXML ``effective_mime``; any
    other (real archive / unknown ZIP) is ``KNOWN_UNSUPPORTED`` — we
    skip true archives per the product decision. A ``PK`` header that
    cannot be opened as a ZIP at all — corrupt, truncated, or a
    malformed central directory (which raises ``BadZipFile`` /
    ``struct.error`` / ``OSError`` / ``EOFError``) — is likewise
    ``KNOWN_UNSUPPORTED``: this classifier is total and must never
    raise, so any failure to read the container is the safe default.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except Exception:  # classifier must be total — any zip read failure ⇒ unsupported
        # PK magic but the ZIP could not be opened/read: not just
        # ``BadZipFile`` but also a ``struct.error`` from a malformed /
        # truncated central directory (common for partially-downloaded
        # SharePoint files), ``OSError``/``EOFError`` on a short read,
        # etc. This function is a pre-extract gate whose contract is
        # totality — it must NEVER raise (an escape would propagate
        # through ``_process_item`` and crash the whole batch). Any
        # failure to open the container ⇒ treat as KNOWN_UNSUPPORTED,
        # the safe default.
        return CompatResult(Compat.KNOWN_UNSUPPORTED, mime)
    if any(n.startswith("word/") for n in names):
        return CompatResult(Compat.SUPPORTED, _MIME_DOCX)
    if any(n.startswith("ppt/") for n in names):
        return CompatResult(Compat.SUPPORTED, _MIME_PPTX)
    if any(n.startswith("xl/") for n in names):
        return CompatResult(Compat.SUPPORTED, _MIME_XLSX)
    # A valid ZIP that is not an OOXML Office document → true archive.
    return CompatResult(Compat.KNOWN_UNSUPPORTED, mime)


def _extension(name: str) -> str:
    """Lower-cased final extension of ``name`` (``""`` when none)."""
    dot = name.rfind(".")
    if dot < 0:
        return ""
    return name[dot:].lower()


def classify_compat(mime: str, name: str, data: bytes) -> CompatResult:
    """Classify an item's processability BEFORE extraction.

    Args:
        mime: the connector-declared MIME hint (may be the generic
            ``application/octet-stream`` when the source stripped the
            Content-Type).
        name: the item filename WITH extension (e.g. from
            ``ChangeEvent.metadata["name"]``). Used only as a tiebreak;
            ``""`` when unavailable.
        data: the full file bytes already fetched by the connector. Only
            the leading bytes (magic) and — for ZIP payloads — the
            central directory are inspected.

    Returns:
        A :class:`CompatResult` whose ``compat`` drives the skip gate
        and whose ``effective_mime`` drives extraction routing.

    Resolution order (strongest signal first):

    1. **Magic bytes** (``data[:8]``) — PDF / image / ZIP-family. ZIP
       payloads are disambiguated into OOXML-document (SUPPORTED, MIME
       corrected) vs. true-archive/corrupt (KNOWN_UNSUPPORTED).
    2. **MIME** — when magic was indecisive: supported universe →
       SUPPORTED; known-unsupported set/prefixes → KNOWN_UNSUPPORTED.
    3. **Extension** (tiebreak, KNOWN_UNSUPPORTED-only) — a
       known-unsupported extension demotes an otherwise-unknown item.
    4. **Fall-through** — generic / unrecognised → UNKNOWN (the extract
       path still runs; today's behaviour is preserved).
    """
    head = data[:8]

    # 1. Magic bytes — the most trustworthy signal.
    if head.startswith(_MAGIC_PDF):
        return CompatResult(Compat.SUPPORTED, mime)
    if (
        head.startswith(_MAGIC_PNG)
        or head.startswith(_MAGIC_JPEG)
        or head.startswith(_MAGIC_GIF87)
        or head.startswith(_MAGIC_GIF89)
    ):
        return CompatResult(Compat.SUPPORTED, mime)
    if head.startswith(_MAGIC_ZIP):
        return _classify_zip(data, mime)

    # 2. MIME — when magic gave nothing decisive.
    normalised_mime = (mime or "").strip().lower()
    if normalised_mime.startswith("text/"):
        return CompatResult(Compat.SUPPORTED, mime)
    if normalised_mime in _SUPPORTED_MIMES:
        return CompatResult(Compat.SUPPORTED, mime)
    if normalised_mime in _KNOWN_UNSUPPORTED_MIMES:
        return CompatResult(Compat.KNOWN_UNSUPPORTED, mime)
    if any(normalised_mime.startswith(prefix) for prefix in _KNOWN_UNSUPPORTED_MIME_PREFIXES):
        return CompatResult(Compat.KNOWN_UNSUPPORTED, mime)

    # 3. Extension — TIEBREAK only; can demote to KNOWN_UNSUPPORTED but
    # never upgrade to SUPPORTED.
    if _extension(name) in _KNOWN_UNSUPPORTED_EXTENSIONS:
        return CompatResult(Compat.KNOWN_UNSUPPORTED, mime)

    # 4. Generic / unrecognised (e.g. application/octet-stream with no
    # decisive magic/extension) — do NOT auto-skip. Let extraction try.
    return CompatResult(Compat.UNKNOWN, mime)
