"""F30 outcome test — ``kairix reference-library`` subprocess surface.

Pays down ``kairix/knowledge/reflib/cli.py`` from the F30 baseline.

The reflib CLI exposes ``status`` (read-only, file-system-only),
``install`` (writes to Neo4j; needs the driver), and ``extract``
(placeholder). ``status`` is the natural subprocess outcome path:
it walks the configured reflib root and prints collection counts +
entity-file metadata without external dependencies.

Boundary chain exercised:

  subprocess([kairix, reference-library, status, --reflib-root <tmp>, --json])
    → kairix/knowledge/reflib/cli.py:main → _cmd_status
    → _resolve_reflib_root (arg wins, no env read)
    → _discover_collections + _read_entity_files
    → json.dumps over the status dict → stdout
    → CLI exits 0

The error-path test points ``install`` at an empty tmp tree — the
``entities/`` directory is missing so the CLI prints a structured
error message to stderr and exits 1.

F2-clean: no ``KAIRIX_*`` env mutation in the subprocess invocation.

Sabotage-proof anchor: mutating ``_resolve_reflib_root`` to return
``"/sabotage"`` instead of ``arg`` makes the status envelope's
``reflib_root`` field point at the sabotage path → the assertion
``envelope["reflib_root"] == str(tmp_path)`` fails. Verified locally.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed_minimal_reflib_root(root: Path) -> None:
    """Lay out a minimal reflib root: two collection dirs + an entities/
    dir containing nodes.json + edges.json. Matches the shape
    ``_read_entity_files`` expects.
    """
    (root / "collection-alpha").mkdir(parents=True, exist_ok=True)
    (root / "collection-alpha" / "README.md").write_text("# Alpha\n", encoding="utf-8")
    (root / "collection-beta").mkdir(parents=True, exist_ok=True)
    (root / "entities").mkdir(parents=True, exist_ok=True)
    (root / "entities" / "nodes.json").write_text(
        json.dumps([{"id": "n1", "label": "Outcome", "name": "Test Outcome"}]),
        encoding="utf-8",
    )
    (root / "entities" / "edges.json").write_text(
        json.dumps([{"source": "n1", "target": "n2", "type": "RELATED"}]),
        encoding="utf-8",
    )


def test_reflib_status_subprocess_json_envelope_outcome(tmp_path: Path) -> None:
    """Drive ``kairix reference-library status --reflib-root <tmp> --json``.

    Asserts on the JSON envelope content the operator (and the future
    inventory dashboard) consumes — collection list, node/edge counts,
    last-modified timestamp.
    """
    _seed_minimal_reflib_root(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "reference-library",
            "status",
            "--reflib-root",
            str(tmp_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"reflib status exited {proc.returncode}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    envelope = json.loads(proc.stdout)
    assert envelope["reflib_root"] == str(tmp_path), (
        f"reflib_root mismatch: expected {tmp_path}, got {envelope.get('reflib_root')!r}"
    )
    assert envelope["entities_dir_exists"] is True, f"entities/ should exist: {envelope!r}"
    assert envelope["node_count"] == 1, f"expected 1 node, got {envelope['node_count']}"
    assert envelope["edge_count"] == 1, f"expected 1 edge, got {envelope['edge_count']}"
    assert "collection-alpha" in envelope["collections"], (
        f"expected collection-alpha in collections, got {envelope['collections']!r}"
    )
    assert "collection-beta" in envelope["collections"], (
        f"expected collection-beta in collections, got {envelope['collections']!r}"
    )


def test_reflib_install_subprocess_missing_entities_dir(tmp_path: Path) -> None:
    """``reference-library install`` against a reflib root with no
    entities/ directory must surface the gap on stderr and exit
    non-zero. Closes the binary-surface error path.
    """
    # Empty reflib root — no entities/ subdir.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "reference-library",
            "install",
            "--reflib-root",
            str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1, (
        f"reflib install expected exit 1, got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "entities directory not found" in proc.stderr, f"expected entities-dir error on stderr, got: {proc.stderr!r}"
