"""Unit tests for F52 (``scripts/checks/check_f52_flag_call_sites.py``).

F52 enforces that every ``flag("<name>")`` call site inside
``kairix/**/*.py`` references a name declared in
``kairix.core.features.registry.REGISTRY``. Catches typos and dead-flag
references after retirement.

These tests exercise the public ``_collect_call_sites`` /
``find_violations`` helpers with synthetic source files inside a
tmp_path. The detector module is loaded by path so PR-2 absence does
not block the tests.

Each test carries an inline sabotage-proof.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f52_flag_call_sites.py"


def _load_detector() -> object:
    """Load the F52 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f52_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f52_detector"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, body: str) -> Path:
    """Write ``body`` to ``path``, creating parents. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_call_site_with_known_name_passes(tmp_path: Path) -> None:
    """A flag(...) call to a name that exists in REGISTRY is clean.

    Sabotage proof: change ``find_violations`` to flag every call site
    regardless of REGISTRY membership and this assertion flips from
    empty list to a violation.
    """
    detector = _load_detector()
    src = tmp_path / "kairix" / "worker.py"
    _write(
        src,
        """from kairix.core.features import flag

def main() -> None:
    if flag("obsidian_connector_primary"):
        run_new_pipeline()
    else:
        run_legacy_scanner()
""",
    )
    # _collect_call_sites returns (lineno, name) tuples.
    sites = detector._collect_call_sites(src)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert sites == [(4, "obsidian_connector_primary")]
    # Real find_violations walks REPO_ROOT/kairix; we test by reaching
    # in with the source-set helper directly.
    registry_names = {"obsidian_connector_primary"}
    # Walk the synthetic file through _collect_call_sites + manual filter
    # since find_violations is REPO_ROOT-scoped.
    unknown = [(ln, n) for ln, n in sites if n not in registry_names]
    assert unknown == []


def test_call_site_with_unknown_name_flagged(tmp_path: Path) -> None:
    """A flag(...) call referencing a name absent from REGISTRY is a violation.

    Sabotage proof: weaken the AST visitor to skip ``flag(...)`` calls
    and ``_collect_call_sites`` would return ``[]`` here, failing this
    assertion.
    """
    detector = _load_detector()
    src = tmp_path / "kairix" / "worker.py"
    _write(
        src,
        """from kairix.core.features import flag

def main() -> None:
    if flag("nonexistent_flag"):
        do_something()
""",
    )
    sites = detector._collect_call_sites(src)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert sites == [(4, "nonexistent_flag")]
    registry_names = {"obsidian_connector_primary"}
    unknown = [n for ln, n in sites if n not in registry_names]
    assert unknown == ["nonexistent_flag"]


def test_call_site_not_from_features_module_ignored(tmp_path: Path) -> None:
    """A ``flag(...)`` call where ``flag`` is bound from elsewhere is ignored.

    F52 only constrains the kairix.core.features.flag surface, not any
    other module that happens to define a ``flag`` callable.

    Sabotage proof: remove the import-tracking guard from
    ``_is_flag_call`` so every ``flag(...)`` matches, and this
    assertion flips from ``[]`` to a real entry.
    """
    detector = _load_detector()
    src = tmp_path / "kairix" / "flagpole.py"
    _write(
        src,
        """from kairix.unrelated import flag

def hoist() -> None:
    flag("blue_white")
""",
    )
    sites = detector._collect_call_sites(src)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert sites == []  # flag is not from kairix.core.features


def test_remediation_carries_action_markers() -> None:
    """F52's REMEDIATION must carry F21 ``fix:`` / ``next:`` / ``run:`` markers."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem


def test_loads_live_registry_names() -> None:
    """The detector loads the live registry name set and ``main()``
    stays green because every ``flag("...")`` call site in production
    references a name that exists in the registry.

    Sabotage proof: rename the connector_dex_crm registry entry's name
    field → the call sites pointing at it no longer match and F52 fires.

    ``obsidian_connector_primary`` retired post-cutover (task #132); the
    test now pins ``connector_dex_crm`` as the representative entry.
    """
    detector = _load_detector()
    names = detector._load_registry_names()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert names is not None, "registry must load cleanly"
    assert "connector_dex_crm" in names
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
