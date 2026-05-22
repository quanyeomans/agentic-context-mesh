"""Layout analysis + reading-order reconstruction for the OCR extractor.

Tesseract handles single-column pages well out of the box. Multi-
column pages (two-column journal articles, three-column newspapers)
break because Tesseract scans left-to-right across the whole page
width — the reading order ends up interleaved across columns.

This module's helpers exist for the multi-column path. The MM-2
landing exposes the seam (``reconstruct_reading_order``) so the
extractor can call it on every page; today the implementation is a
single-column passthrough that simply joins the text in vertical
order. The Wave 3 follow-up will swap in the proper column detector
without touching the extractor's call site.

Kept separate from :mod:`preprocess` because reading-order is a
post-OCR concern (operates on text + bounding boxes), not a pixel
concern.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextRegion:
    """One layout region detected on a page.

    The default region the extractor produces is the whole page; the
    multi-column detector will produce one region per column. Fields
    are deliberately tight (no ``dict[str, Any]``) per F42.
    """

    text: str
    top: int
    left: int
    width: int
    height: int


def reconstruct_reading_order(regions: tuple[TextRegion, ...]) -> str:
    """Join text from a tuple of :class:`TextRegion` in reading order.

    Reading order is top-to-bottom, then left-to-right within
    horizontally-overlapping rows. The implementation is intentionally
    simple — a stable sort on ``(top, left)`` — because the MM-2
    landing only exercises the single-region case (the extractor
    treats the whole page as one region today). The multi-column
    Wave-3 detector will produce richer region tuples that this
    function handles correctly under the same sort.
    """
    if not regions:
        return ""
    ordered = sorted(regions, key=lambda r: (r.top, r.left))
    return "\n\n".join(r.text for r in ordered if r.text.strip())


def whole_page_region(text: str, width: int, height: int) -> TextRegion:
    """Construct a single :class:`TextRegion` covering the whole page."""
    return TextRegion(text=text, top=0, left=0, width=width, height=height)


__all__ = [
    "TextRegion",
    "reconstruct_reading_order",
    "whole_page_region",
]
