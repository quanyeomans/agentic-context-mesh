"""Unit tests for F51 (``scripts/checks/check_f51_flag_retirement.py``).

F51 enforces that every ``FeatureFlag`` in the registry has a
``target_retire_in`` within 6 months of the current ``setuptools-scm``
version. Past that deadline, the gate fires unless a
``# retire-extension: <reason>`` comment is adjacent to the entry.

These tests exercise the public ``find_violations`` entry point with
synthetic registries + synthetic registry source, so the rule is
provable without depending on PR-2 having landed.

Each test carries an inline sabotage-proof — mutate the production code
and re-run; the assertion flips.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_f51_flag_retirement.py"


def _load_detector() -> object:
    """Load the F51 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f51_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f51_detector"] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _FakeFlag:
    """Synthetic FeatureFlag stand-in for testing F51 logic.

    Carries only the field F51 inspects (``target_retire_in``); the real
    dataclass has more shape but F51 only reads this one attribute.
    """

    target_retire_in: str


def test_empty_registry_returns_no_violations() -> None:
    """Vacuous-green: an empty registry produces zero violations.

    Sabotage proof: change ``find_violations`` to iterate ``[None]``
    instead of ``registry.items()`` and this test surfaces the regression.
    """
    detector = _load_detector()
    result = detector.find_violations({}, "", "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_flag_past_deadline_without_extension_flagged() -> None:
    """A flag with a deadline >6 months out from current version fires.

    Current version: ``v2026.5.22``. Deadline window: 6 months → ``v2026.11.22``.
    A flag with ``target_retire_in="v2027.5.22"`` (a year out) violates.

    Sabotage proof: mutate ``_add_six_months`` to add 12 months and this
    assertion goes from list-of-one to empty.
    """
    detector = _load_detector()
    registry = {"long_lived_flag": _FakeFlag(target_retire_in="v2027.5.22")}
    # Source text contains no extension comment.
    src = """REGISTRY = {
    "long_lived_flag": FeatureFlag(
        target_retire_in="v2027.5.22",
    ),
}
"""
    result = detector.find_violations(registry, src, "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == ["kairix/core/features/registry.py:flag=long_lived_flag"]


def test_retire_extension_comment_exempts_flag() -> None:
    """A ``# retire-extension: <reason>`` comment above the entry exempts it.

    Same flag, same deadline, but the registry source carries the
    rationale comment immediately above the entry. F51 must not fire.

    Sabotage proof: change ``_find_extension_comment_lines`` to return
    ``set()`` unconditionally and this assertion goes from empty list to
    a violation entry.
    """
    detector = _load_detector()
    registry = {"long_lived_flag": _FakeFlag(target_retire_in="v2027.5.22")}
    src = """REGISTRY = {
    # retire-extension: blocked on Wave 5 dispatch; bumped 2026-05-22
    "long_lived_flag": FeatureFlag(
        target_retire_in="v2027.5.22",
    ),
}
"""
    result = detector.find_violations(registry, src, "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_flag_within_deadline_not_flagged() -> None:
    """A flag whose deadline is inside the 6-month window is healthy.

    Sabotage proof: invert the comparison in ``find_violations`` (use
    ``<`` instead of ``>``) and this assertion flips.
    """
    detector = _load_detector()
    registry = {"short_flag": _FakeFlag(target_retire_in="v2026.7.22")}  # 2 months out
    result = detector.find_violations(registry, "", "v2026.5.22")  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == []


def test_remediation_carries_action_markers() -> None:
    """F51's REMEDIATION must carry F21 ``fix:`` / ``next:`` / ``run:`` markers."""
    detector = _load_detector()
    rem = detector.REMEDIATION.lower()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert "fix:" in rem
    assert "next:" in rem
    assert "run:" in rem


def test_loads_live_registry_after_pr6() -> None:
    """``_load_registry`` returns a non-None dict containing the
    canonical connector_* entry. The detector resolves cleanly —
    ``main()`` returns 0 because every registered flag's
    ``target_retire_in`` is within 6 months of the current
    setuptools-scm version.

    Sabotage proof: remove the connector_dex_crm entry from REGISTRY →
    ``"connector_dex_crm" in registry`` becomes False and this test
    fails on the explicit-key assertion.

    ``obsidian_connector_primary`` retired post-cutover (task #132); the
    test now pins ``connector_dex_crm`` as the representative entry.
    """
    detector = _load_detector()
    registry = detector._load_registry()  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert registry is not None, "registry must load cleanly"
    assert "connector_dex_crm" in registry, (
        f"expected connector_dex_crm entry; got: {sorted(registry) if registry else 'None'}"
    )
    assert detector.main() == 0  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs


def test_alpha_tail_and_scm_dev_versions_now_parse() -> None:
    """The repo's real tag/version shapes resolve to a date (PLA-277).

    Pre-fix, ``_VERSION_RE`` had no branch for a PEP-440 pre-release tail
    attached without a separator, so the ``release-alpha.yml`` tag shape
    (``v2026.6.28a5``) and the ``setuptools-scm`` ``aN.devM`` shape
    (``2026.6.28a6.dev2``) both parsed to None — which made
    ``find_violations`` return [] and F51 permanently vacuous.

    Sabotage proof (executed): revert ``_VERSION_RE`` to
    ``r"^v?(\\d{4})\\.(\\d{1,2})\\.(\\d{1,2})(?:[.\\-+].*)?$"`` and both
    assertions return None instead of the date.
    """
    detector = _load_detector()
    parse = detector._parse_calver  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert parse("v2026.6.28a5") == date(2026, 6, 28)
    assert parse("2026.6.28a6.dev2") == date(2026, 6, 28)


@pytest.mark.parametrize("current_version", ["v2026.6.28a5", "2026.6.28a6.dev2"])
def test_beyond_window_flag_fires_for_alpha_shaped_current_version(
    current_version: str,
) -> None:
    """A beyond-window flag is flagged when the CURRENT version carries the
    alpha tail — the shape that pre-PLA-277 short-circuited
    ``find_violations`` to [] (vacuous-green), because the unparseable
    current version made the deadline never evaluate.

    Current ``v2026.6.28a5`` → deadline ``2026.12.28``; a flag targeting
    ``v2027.5.23`` (≈11 months out) exceeds the 6-month ceiling and fires.

    Sabotage proof (executed): revert ``_VERSION_RE`` to reject the alpha
    tail → ``current_version`` parses to None → ``find_violations``
    returns [] and this assertion fails.
    """
    detector = _load_detector()
    registry = {"long_lived_flag": _FakeFlag(target_retire_in="v2027.5.23")}
    src = """REGISTRY = {
    "long_lived_flag": FeatureFlag(
        target_retire_in="v2027.5.23",
    ),
}
"""
    result = detector.find_violations(registry, src, current_version)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert result == ["kairix/core/features/registry.py:flag=long_lived_flag"]


def test_git_describe_fallback_resolves_a_parseable_version(tmp_path: Path) -> None:
    """The git-tag fallback resolves a parseable version when setuptools-scm
    is not importable (it is only a *build* dependency), so the deadline
    still evaluates (PLA-277).

    Hermetic by construction: it builds a throwaway git repo under
    ``tmp_path`` with a single tagged commit and points the fallback at it
    via the documented ``cwd`` parameter. It does NOT read the repo's own
    tags, so it is immune to the "passes local, fails Linux CI" trap where
    the CI checkout has fetched no tags (which would make a REPO_ROOT-only
    assertion return None and fail).

    Sabotage proof (executed): replace the ``_version_from_git_describe``
    body with ``return None`` (i.e. remove the fallback) → ``resolved`` is
    None and the first assertion fails.
    """
    detector = _load_detector()
    repo = tmp_path / "scmless_checkout"
    repo.mkdir()
    # Isolate from the host's global/system git config so commit.gpgsign or
    # a missing identity on the runner can't break the fixture commit.
    git_env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=git_env, check=True, capture_output=True, text=True)

    _git("init")
    _git("config", "user.email", "agent-alpha@example.com")
    _git("config", "user.name", "agent-alpha")
    _git("commit", "--allow-empty", "-m", "seed")
    _git("tag", "v2026.6.28")

    resolved = detector._version_from_git_describe(cwd=repo)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
    assert resolved is not None
    assert detector._parse_calver(resolved) == date(2026, 6, 28)  # type: ignore[attr-defined]  # detector loaded by path; mypy can't see attrs
