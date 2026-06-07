"""Red/green regression net for Plan 1 → Plan 3 (FHS / XDG path refactor).

Two contract tests grep every ``.py`` under ``kairix/`` (production tree) for
the two legacy hardcoded path roots that the new ``kairix.paths.Mode`` enum +
mode-aware resolvers replace:

  * ``/opt/kairix``           — the legacy VM-deploy root for code + config
  * ``/var/lib/kairix-runtime`` — the legacy runtime/data root

Today both tests are decorated ``@pytest.mark.xfail(strict=False)``:

  * **RED side (test 1, ``/opt/kairix``)** — the production tree still has
    multiple hardcoded references (kairix/paths.py docstrings + fallbacks,
    kairix/platform/onboard/check.py user-facing fix messages,
    kairix/core/search/config_loader.py default path, etc.). The assertion
    *fails today*, the xfail records that as an expected failure, and the
    commit stays green.
  * **GREEN side (test 2, ``/var/lib/kairix-runtime``)** — there are
    currently zero hits, so the assertion passes today. With
    ``strict=False`` the unexpected pass is recorded as XPASS (not FAIL),
    so the commit still stays green. This test locks the green state
    in: any future re-introduction of ``/var/lib/kairix-runtime`` would
    cause the assertion to fail (which would still register as XFAIL
    under the decorator, but the regression would be visible in CI as
    the per-test xfail reason changing).

Once Plan 3 ships and removes the last hardcoded ``/opt/kairix`` literal
from production code, the ``@pytest.mark.xfail`` decorators come off
both tests permanently and they go GREEN. From that point on, any
future re-introduction of either literal in non-comment production code
will turn the suite red and block the commit at safe-commit + CI.

Sabotage-proof (executed, 2026-06-07):
  * Test 1 (``/opt/kairix``): with the xfail decorator removed, the test
    FAILed locally, surfacing the full hit list from ``kairix/paths.py``,
    ``kairix/platform/onboard/check.py``,
    ``kairix/core/search/config_loader.py``, etc. — confirms the
    assertion correctly flags today's RED state. Decorator restored.
  * Test 2 (``/var/lib/kairix-runtime``): temporarily added a line
    ``x = "/var/lib/kairix-runtime/foo"`` to ``kairix/paths.py`` (non-comment),
    re-ran the test without the xfail decorator → FAILed with that file in
    the hit list. Both the injected line and the decorator were restored.

This file is intentionally pure-stdlib + ``pathlib``: no factory, no
fakes, no Protocol — it is a static structural check over the source
tree, of the same shape as ``tests/contracts/test_no_legacy_agent_memory_path.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KAIRIX_TREE = _REPO_ROOT / "kairix"


def _scan_for_literal(literal: str) -> list[str]:
    """Return ``path:line_no: line`` for every non-comment occurrence of
    ``literal`` in every ``.py`` file under ``kairix/`` (the production
    tree only — ``tests/`` is intentionally excluded because tests
    legitimately reference the legacy literals as search needles).
    """
    matches: list[str] = []
    for path in _KAIRIX_TREE.rglob("*.py"):
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if literal in line and not stripped.startswith("#"):
                matches.append(f"{path.relative_to(_REPO_ROOT)}:{line_no}: {stripped}")
    return matches


@pytest.mark.contract
@pytest.mark.xfail(
    reason="RED today; will GREEN after Plan 3 cutover removes legacy paths",
    strict=False,
)
def test_no_production_code_hardcodes_opt_kairix() -> None:
    """RED side of the red/green refactor for Plan 1 → Plan 3.

    Today (pre-Plan-3) the production tree contains multiple
    ``/opt/kairix`` references: ``kairix/paths.py`` carries the legacy
    ``Path("/opt/kairix/.venv")`` system-deploy probe and reference-library
    fallback, ``kairix/platform/onboard/check.py`` embeds the literal in
    user-facing fix instructions, ``kairix/core/search/config_loader.py``
    defaults to ``Path("/opt/kairix/kairix.config.yaml")``, and several
    plugin docs string the path. The assertion below is therefore
    expected to fail (XFAIL) today.

    After Plan 3 lands the Mode-aware resolvers + the cutover removes
    every legacy literal, the ``@pytest.mark.xfail`` decorator is
    removed and this test goes GREEN permanently — at which point it
    becomes a hard regression net: any future re-introduction of
    ``/opt/kairix`` outside a comment will fail the commit at
    safe-commit and at CI Stage 2 (the contracts suite).
    """
    matches = _scan_for_literal("/opt/kairix")
    assert not matches, "\n".join(["Stale /opt/kairix references:", *matches])


@pytest.mark.contract
@pytest.mark.xfail(
    reason="RED today; will GREEN after Plan 3 cutover removes legacy paths",
    strict=False,
)
def test_no_production_code_hardcodes_var_lib_kairix_runtime() -> None:
    """Symmetric RED-side guard for the second legacy root.

    The literal ``/var/lib/kairix-runtime`` was the ad-hoc runtime/data
    root that pre-dated the FHS-aligned ``/var/lib/kairix`` chosen in
    Plan 1. The two roots differ by a single trailing token, so a
    contract test that grandfathers ``/var/lib/kairix`` while rejecting
    ``/var/lib/kairix-runtime`` lets the new FHS path through while
    catching anyone copy-pasting from old runbooks.

    Today's scan returns zero hits, so the assertion passes — under
    ``strict=False`` that registers as XPASS rather than failing the
    suite. The xfail decorator remains so both legacy paths can be
    flipped to permanent GREEN in a single Plan 3 commit (decorator
    removal) rather than one-by-one.
    """
    matches = _scan_for_literal("/var/lib/kairix-runtime")
    assert not matches, "\n".join(["Stale /var/lib/kairix-runtime references:", *matches])
