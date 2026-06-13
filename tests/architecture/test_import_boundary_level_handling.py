"""Faithfulness proof for ``_import_boundary_engine`` ``ImportFrom.level`` handling.

#499 Phase 2 collapsed six import-boundary rules (F26, F27, F34, F35, F37,
F44) into one declarative engine. The first pass introduced a SILENT
divergence: the shared ``_import_modules`` skipped every relative import
(``node.level > 0``) before reading ``node.module``. But five of the six
originals are level-AGNOSTIC — their ``file_has_violation`` / ``_imported_names``
/ ``_plugin_dir_for`` walkers inspect ``node.module`` directly with no
``node.level`` guard, so a relative import whose ``.module`` matches a forbidden
prefix / sibling plugin (``from .kairix.providers import x`` → level=1,
module=``kairix.providers``; ``from ..psycopg2 import bar`` → level=2,
module=``psycopg2``) IS flagged by the original. The blanket skip dropped those.

Only F37 (sync-lib mode) guards ``node.level`` in its original
``_import_targets`` — a relative import cannot reach a third-party sync library
by construction, so it is correctly ignored there.

This module pins the corrected per-rule policy that restores parity with each
original byte-for-behaviour:

* F26 / F27 / F34 / F35 / F44 — level-AGNOSTIC: a relative import whose
  ``.module`` matches the rule's forbidden set IS flagged.
* F37 — level-GUARDED: a relative import whose ``.module`` matches a sync-lib
  root is NOT flagged.

Each test carries an inline sabotage-proof (mutate the import to the negative
shape → confirm the flag clears), so a future regression in the level policy
surfaces here, not silently in production.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CHECKS = _REPO_ROOT / "scripts" / "checks"


def _load(mod_name: str, file_name: str):
    """Load a detector shim by file path with ``scripts/checks`` importable."""
    if str(_CHECKS) not in sys.path:
        sys.path.insert(0, str(_CHECKS))
    spec = importlib.util.spec_from_file_location(mod_name, _CHECKS / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ── F26: prefix mode, level-agnostic ────────────────────────────────────────


def test_f26_relative_import_matching_forbidden_prefix_is_flagged(tmp_path: Path) -> None:
    """``from .kairix.providers import x`` (level=1, module='kairix.providers')
    fires F26 — the original walked ``node.module`` regardless of ``node.level``.

    Sabotage-proof inline: rewrite the relative import to target the allowed
    Protocol surface; the flag clears.
    """
    detector = _load("_f26_level", "check_provider_layer_imports.py")
    root = tmp_path.resolve()
    _write(root, "kairix/core/rel.py", "from .kairix.providers import x\n")
    assert Path("kairix/core/rel.py") in detector.collect_violations(root)

    # Sabotage: relative import that does NOT match a forbidden prefix.
    _write(root, "kairix/core/rel.py", "from .kairix.core.protocols import X\n")
    assert detector.collect_violations(root) == set()


# ── F27: sibling-plugin mode, level-agnostic ────────────────────────────────


def test_f27_relative_import_matching_sibling_plugin_is_flagged(tmp_path: Path) -> None:
    """``from .kairix.providers.azure import h`` inside the ``openai`` plugin
    (level=1, module='kairix.providers.azure') fires F27 — the sibling-plugin
    walk is level-agnostic.

    Sabotage-proof inline: rewrite to target the shared ``_base`` scaffolding;
    the flag clears.
    """
    detector = _load("_f27_level", "check_no_cross_provider.py")
    root = tmp_path.resolve()
    _write(root, "kairix/providers/openai/rel.py", "from .kairix.providers.azure import h\n")
    assert Path("kairix/providers/openai/rel.py") in detector.collect_violations(root)

    # Sabotage: shared base is never cross-plugin.
    _write(root, "kairix/providers/openai/rel.py", "from .kairix.providers._base import Provider\n")
    assert detector.collect_violations(root) == set()


# ── F34: prefix mode, level-agnostic ────────────────────────────────────────


def test_f34_relative_import_matching_forbidden_prefix_is_flagged(tmp_path: Path) -> None:
    """``from .kairix.connectors import c`` (level=1, module='kairix.connectors')
    fires F34 — the audit-proven level-agnostic case.

    Sabotage-proof inline: rewrite to the Protocol surface; the flag clears.
    """
    detector = _load("_f34_level", "check_f34_core_connector_layer_imports.py")
    root = tmp_path.resolve()
    _write(root, "kairix/core/connectors/rel.py", "from .kairix.connectors import c\n")
    assert Path("kairix/core/connectors/rel.py") in detector.collect_violations(root)

    # Sabotage: Protocol import is allowed.
    _write(root, "kairix/core/connectors/rel.py", "from .kairix.core.protocols import SourceConnector\n")
    assert detector.collect_violations(root) == set()


# ── F35: sibling-plugin mode (+ extractor predicate), level-agnostic ─────────


def test_f35_relative_import_matching_sibling_connector_is_flagged(tmp_path: Path) -> None:
    """``from .kairix.connectors.obsidian import scan`` inside the ``sharepoint``
    connector (level=1, module='kairix.connectors.obsidian') fires F35.

    Sabotage-proof inline: rewrite to the shared ``_base``; the flag clears.
    """
    detector = _load("_f35_level", "check_f35_no_cross_connector.py")
    root = tmp_path.resolve()
    _write(root, "kairix/connectors/sharepoint/rel.py", "from .kairix.connectors.obsidian import scan\n")
    assert Path("kairix/connectors/sharepoint/rel.py") in detector.collect_violations(root)

    # Sabotage: shared base is never cross-plugin.
    _write(root, "kairix/connectors/sharepoint/rel.py", "from .kairix.connectors._base import SourceConnector\n")
    assert detector.collect_violations(root) == set()


def test_f35_relative_import_matching_extractor_prefix_is_flagged(tmp_path: Path) -> None:
    """The F35 extractor-ban predicate is also level-agnostic:
    ``from .kairix.extractors.markitdown import M`` (level=1,
    module='kairix.extractors.markitdown') fires F35.
    """
    detector = _load("_f35_extr", "check_f35_no_cross_connector.py")
    root = tmp_path.resolve()
    _write(root, "kairix/connectors/sharepoint/rel.py", "from .kairix.extractors.markitdown import M\n")
    assert Path("kairix/connectors/sharepoint/rel.py") in detector.collect_violations(root)


# ── F44: prefix mode, level-agnostic ────────────────────────────────────────


def test_f44_relative_import_matching_firm_client_is_flagged(tmp_path: Path) -> None:
    """``from ..psycopg2 import connect`` (level=2, module='psycopg2') fires F44 —
    the original ``_imported_names`` reads ``node.module`` with no level guard.

    Sabotage-proof inline: rewrite to an engagement-scope client; the flag clears.
    """
    detector = _load("_f44_level", "check_f44_engagement_firm_boundary.py")
    root = tmp_path.resolve()
    _write(root, "kairix/core/storage/rel.py", "from ..psycopg2 import connect\n")
    assert Path("kairix/core/storage/rel.py") in detector.collect_violations(root)

    # Sabotage: SQLite is engagement-scope and welcome.
    _write(root, "kairix/core/storage/rel.py", "from ..sqlite3 import connect\n")
    assert detector.collect_violations(root) == set()


# ── F37: sync-lib mode, level-GUARDED ───────────────────────────────────────


def test_f37_relative_import_matching_sync_lib_is_not_flagged(tmp_path: Path) -> None:
    """``from ..watchdog import Observer`` (level=2, module='watchdog') is NOT
    flagged by F37 — its original ``_import_targets`` alone guards ``node.level``
    (a relative import cannot reach a third-party sync library by construction).

    Sabotage-proof inline: switch to the ABSOLUTE form in the same forbidden
    location; the flag fires — proving the rule still detects real sync-lib
    reaches and the difference is purely the level policy.
    """
    detector = _load("_f37_level", "check_f37_singular_sync.py")
    root = tmp_path.resolve()
    _write(root, "kairix/corpus/rel.py", "from ..watchdog import Observer\n")
    assert detector.collect_violations(root) == set()

    # Sabotage: the absolute form in the same forbidden tree IS a violation.
    _write(root, "kairix/corpus/rel.py", "from watchdog.observers import Observer\n")
    assert Path("kairix/corpus/rel.py") in detector.collect_violations(root)
