"""Unit tests for the pre-extract compatibility classifier.

Covers every branch of
:func:`kairix.core.connectors.compat.classify_compat`:

* magic-byte signals (PDF / images / ZIP-family);
* OOXML-from-ZIP disambiguation (docx/pptx/xlsx → SUPPORTED + corrected
  ``effective_mime``);
* true-archive ZIP and corrupt ZIP → KNOWN_UNSUPPORTED;
* MIME-driven SUPPORTED / KNOWN_UNSUPPORTED;
* extension tiebreak (KNOWN_UNSUPPORTED-only, never an upgrade);
* the generic octet-stream → UNKNOWN fall-through that preserves
  today's extract-anyway behaviour.

F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from kairix.core.connectors.compat import (
    Compat,
    CompatResult,
    classify_compat,
)

pytestmark = pytest.mark.unit

_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ooxml_zip(member_dir: str) -> bytes:
    """Build a tiny in-memory ZIP carrying one OOXML tell-tale member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{member_dir}/main.xml", "<xml/>")
        zf.writestr("[Content_Types].xml", "<xml/>")
    return buf.getvalue()


def _loose_zip() -> bytes:
    """A valid ZIP of loose files — a true archive, no OOXML member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
        zf.writestr("data/values.csv", "a,b,c")
    return buf.getvalue()


# --- magic bytes -----------------------------------------------------------


def test_pdf_magic_on_octet_stream_is_supported() -> None:
    """``%PDF`` magic beats a generic octet-stream MIME → SUPPORTED."""
    result = classify_compat("application/octet-stream", "report", b"%PDF-1.7\n%trailer junk")
    assert result == CompatResult(Compat.SUPPORTED, "application/octet-stream")


@pytest.mark.parametrize(
    "head",
    [
        b"\x89PNG\r\n\x1a\n",  # PNG
        b"\xff\xd8\xff\xe0",  # JPEG (JFIF)
        b"GIF87a",  # GIF (legacy)
        b"GIF89a",  # GIF (modern)
    ],
)
def test_image_magic_is_supported(head: bytes) -> None:
    """Common raster-image magics → SUPPORTED (the OCR extractor claims them)."""
    result = classify_compat("application/octet-stream", "image", head + b"\x00\x01\x02payload")
    assert result.compat is Compat.SUPPORTED


def test_docx_zip_labeled_application_zip_is_supported_with_corrected_mime() -> None:
    """A docx ZIP mislabeled application/zip → SUPPORTED + corrected effective_mime."""
    data = _ooxml_zip("word")
    result = classify_compat("application/zip", "monthly-report", data)
    assert result.compat is Compat.SUPPORTED
    assert result.effective_mime == _MIME_DOCX


def test_pptx_zip_is_supported_with_corrected_mime() -> None:
    data = _ooxml_zip("ppt")
    result = classify_compat("application/octet-stream", "deck", data)
    assert result.compat is Compat.SUPPORTED
    assert result.effective_mime == _MIME_PPTX


def test_xlsx_zip_is_supported_with_corrected_mime() -> None:
    data = _ooxml_zip("xl")
    result = classify_compat("application/octet-stream", "sheet", data)
    assert result.compat is Compat.SUPPORTED
    assert result.effective_mime == _MIME_XLSX


def test_loose_zip_is_known_unsupported() -> None:
    """A true archive (valid ZIP, no OOXML member) → KNOWN_UNSUPPORTED (skip archives)."""
    data = _loose_zip()
    result = classify_compat("application/zip", "bundle.zip", data)
    assert result.compat is Compat.KNOWN_UNSUPPORTED
    # MIME is NOT corrected for a non-OOXML ZIP.
    assert result.effective_mime == "application/zip"


def test_corrupt_zip_magic_is_known_unsupported() -> None:
    """PK magic that is NOT a parseable ZIP (corrupt) → KNOWN_UNSUPPORTED."""
    result = classify_compat("application/octet-stream", "x", b"PK\x03\x04" + b"not-a-real-zip" * 4)
    assert result.compat is Compat.KNOWN_UNSUPPORTED
    assert result.effective_mime == "application/octet-stream"


def test_truncated_zip_magic_does_not_raise() -> None:
    """A truncated ZIP (malformed central directory) must NOT raise.

    ``zipfile.ZipFile`` can raise ``struct.error`` (not just
    ``BadZipFile``) on a truncated / malformed central directory — a
    common shape for partially-downloaded SharePoint files. The
    classifier is a pre-extract gate whose contract is totality: any
    failure to read the container ⇒ KNOWN_UNSUPPORTED, never an escape.
    """
    truncated = b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 100
    result = classify_compat("application/octet-stream", "x.zip", truncated)
    assert result.compat is Compat.KNOWN_UNSUPPORTED


def test_bare_pk_magic_only_does_not_raise() -> None:
    """Just the 4-byte PK signature with no body ⇒ KNOWN_UNSUPPORTED (no raise)."""
    result = classify_compat("application/octet-stream", "x.zip", b"PK\x03\x04")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


def test_empty_bytes_does_not_raise() -> None:
    """Zero-length payload (no magic at all) ⇒ UNKNOWN, never an escape."""
    result = classify_compat("application/octet-stream", "x.zip", b"")
    assert result.compat is Compat.UNKNOWN


# --- MIME-driven -----------------------------------------------------------


def test_text_markdown_mime_is_supported() -> None:
    result = classify_compat("text/markdown", "note.md", b"# heading\n\nbody")
    assert result == CompatResult(Compat.SUPPORTED, "text/markdown")


def test_text_prefix_any_subtype_is_supported() -> None:
    """The ``text/*`` prefix mirrors the passthrough extractor's rule."""
    result = classify_compat("text/x-rst", "doc.rst", b"some restructured text")
    assert result.compat is Compat.SUPPORTED


def test_pdf_mime_without_magic_is_supported() -> None:
    """A declared application/pdf MIME (no magic in the sniffed head) is SUPPORTED."""
    result = classify_compat("application/pdf", "doc.pdf", b"corrupt-no-magic-but-declared")
    assert result.compat is Compat.SUPPORTED


def test_visio_mime_is_known_unsupported() -> None:
    """Visio (no extractor) → KNOWN_UNSUPPORTED via the MIME prefix."""
    result = classify_compat("application/vnd.ms-visio.drawing", "diagram", b"binary-visio-bytes")
    assert result == CompatResult(Compat.KNOWN_UNSUPPORTED, "application/vnd.ms-visio.drawing")


def test_legacy_binary_office_mime_is_known_unsupported() -> None:
    """Legacy binary .doc (application/msword) → KNOWN_UNSUPPORTED."""
    result = classify_compat("application/msword", "legacy.doc", b"\xd0\xcf\x11\xe0 ole2 header")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


def test_odf_mime_prefix_is_known_unsupported() -> None:
    result = classify_compat("application/vnd.oasis.opendocument.text", "doc.odt", b"random-odf-bytes")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


def test_msdownload_mime_is_known_unsupported() -> None:
    result = classify_compat("application/x-msdownload", "tool", b"MZ binary header")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


# --- extension tiebreak ----------------------------------------------------


def test_known_unsupported_extension_demotes_unknown_to_skip() -> None:
    """An octet-stream with NO decisive magic but a .exe name → KNOWN_UNSUPPORTED."""
    result = classify_compat("application/octet-stream", "installer.exe", b"MZ\x90\x00 random")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


@pytest.mark.parametrize("ext", [".dll", ".vsd", ".vsdx", ".msg", ".odt", ".ods", ".odp", ".pub"])
def test_each_known_unsupported_extension_demotes(ext: str) -> None:
    result = classify_compat("application/octet-stream", f"file{ext}", b"opaque-bytes-no-magic")
    assert result.compat is Compat.KNOWN_UNSUPPORTED


def test_extension_never_upgrades_to_supported() -> None:
    """A .pdf NAME on octet-stream bytes with no PDF magic must NOT become SUPPORTED.

    The extension signal is a KNOWN_UNSUPPORTED-only tiebreak — it can
    demote but never upgrade. Lacking magic/MIME, this stays UNKNOWN so
    extraction still gets a chance.
    """
    result = classify_compat("application/octet-stream", "mystery.pdf", b"not-a-pdf-no-magic")
    assert result.compat is Compat.UNKNOWN


# --- fall-through ----------------------------------------------------------


def test_plain_octet_stream_no_magic_is_unknown() -> None:
    """Generic octet-stream, no magic / unknown extension → UNKNOWN (extract anyway)."""
    result = classify_compat("application/octet-stream", "mystery", b"some opaque payload bytes")
    assert result == CompatResult(Compat.UNKNOWN, "application/octet-stream")


def test_unrecognised_mime_no_signals_is_unknown() -> None:
    result = classify_compat("application/x-weird-thing", "data", b"opaque")
    assert result.compat is Compat.UNKNOWN


def test_empty_name_falls_back_to_magic_and_mime_only() -> None:
    """A missing filename (connector didn't surface ``name``) is tolerated."""
    result = classify_compat("application/octet-stream", "", b"%PDF-1.4 body")
    assert result.compat is Compat.SUPPORTED


def test_empty_bytes_octet_stream_is_unknown() -> None:
    """Zero-length payload with a generic MIME → UNKNOWN (no signal at all)."""
    result = classify_compat("application/octet-stream", "empty", b"")
    assert result.compat is Compat.UNKNOWN
