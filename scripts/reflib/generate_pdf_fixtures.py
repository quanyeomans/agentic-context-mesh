#!/usr/bin/env python3
"""Generate 10 synthetic born-digital PDFs for the per-type fixture corpus.

ADR-028 measurement prereq. Writes minimal valid PDF 1.4 documents from
the stdlib (no reportlab dependency). The output is text-extractable
by ``pdfminer-six`` / ``pdfplumber`` so the eval harness can chunk +
embed them through the real pipeline.

Why stdlib-only: keeps the generator runnable in any kairix venv that
ships the [pdf] markitdown extras, without pulling in another binary
font dependency.

Run from repo root:
    python3 scripts/reflib/generate_pdf_fixtures.py

Output: reference-library/per-type-fixtures/pdf/*.pdf (10 files)
"""

from __future__ import annotations

import random
import sys
import zlib
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent))

from scripts.reflib._fixture_vocab import (  # noqa: E402 — sys.path tweak above
    AGENTS,
    PROJECTS,
    QUARTERS,
    TOPICS,
)

SEED = 28028
N_FIXTURES = 10


def _escape_pdf_text(text: str) -> str:
    """Escape PDF string literals (parens + backslashes)."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_content_stream(lines: list[str]) -> bytes:
    """Build a PDF content stream that draws ``lines`` top-down."""
    # Helvetica 12pt at (50, 750), then move down 16 units per line.
    parts = ["BT", "/F1 12 Tf", "50 750 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            parts.append("0 -16 Td")
        parts.append(f"({_escape_pdf_text(line)}) Tj")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1", errors="replace")
    return zlib.compress(stream)


def _write_pdf(output: Path, lines: list[str]) -> None:
    """Write a minimal one-page PDF carrying ``lines``."""
    compressed = _build_content_stream(lines)
    # Build PDF objects: 1=Catalog, 2=Pages, 3=Page, 4=Font, 5=Content
    objects: list[bytes] = []

    def _obj(idx: int, body: bytes) -> bytes:
        return f"{idx} 0 obj\n".encode() + body + b"\nendobj\n"

    objects.append(_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append(_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"))
    objects.append(
        _obj(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        )
    )
    objects.append(_obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.append(
        _obj(
            5,
            b"<< /Length "
            + str(len(compressed)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + compressed
            + b"\nendstream",
        )
    )

    # Assemble PDF with xref
    out = bytearray(b"%PDF-1.4\n%\xff\xff\xff\xff\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_offset = len(out)
    out.extend(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n")
    out.extend(b"0000000000 65535 f \n")
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    output.write_bytes(bytes(out))


def _compose_lines(project: str, topic: str, rng: random.Random, *, density: str) -> list[str]:
    """Compose the text lines for one PDF, varying by density."""
    lines = [f"{project.upper()} - {topic.upper()}"]
    lines.append("")
    if density == "short":
        lines.append(f"This document records the {topic} for {project}.")
        lines.append(f"Owner: {rng.choice(AGENTS)}. Reviewers: {rng.choice(AGENTS)}.")
        lines.append(f"Effective from {rng.choice(QUARTERS)} this fiscal year.")
    else:
        for _ in range(rng.randint(20, 32)):
            agent = rng.choice(AGENTS)
            verb = rng.choice(["proposed", "approved", "deferred", "rejected", "scheduled"])
            action = rng.choice(TOPICS)
            quarter = rng.choice(QUARTERS)
            lines.append(f"- {agent} {verb} the {action} action for {project} in {quarter}.")
    return lines


def generate(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    written = 0
    for i in range(N_FIXTURES):
        project, _focus = PROJECTS[i % len(PROJECTS)]
        topic = TOPICS[i % len(TOPICS)]
        density = "short" if i < 5 else "long"
        slug_topic = topic.replace(" ", "-")
        path = output_dir / f"{project}-{slug_topic}-{density}.pdf"
        _write_pdf(path, _compose_lines(project, topic, rng, density=density))
        written += 1
    return written


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = repo_root / "reference-library" / "per-type-fixtures" / "pdf"
    n = generate(output_dir)
    print(f"pdf fixtures: wrote {n} files to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
