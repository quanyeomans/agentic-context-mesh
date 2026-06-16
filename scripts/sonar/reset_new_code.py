"""Reset SonarCloud's "New Code" period for the ``main`` branch.

Why this exists (#547)
----------------------
SonarCloud's per-branch "New Code" definition drifts: pre-existing code
that passed at the prior green analysis ends up inside the "new code"
window, so the server-side Quality Gate fails main on accumulated debt
(``new_reliability_rating``, ``new_code_smells``). Because
``sonar.qualitygate.wait=true`` makes the in-CI scan exit WITH the gate
verdict, that flips the whole ci.yml run to ``failure`` — which (until
#548) blocked the alpha release-gate.

The durable fix is a one-time reset of the New Code reference, then
``type=PREVIOUS_VERSION`` mode so each release rolls the window forward
(the scanner emits ``sonar.projectVersion`` per release; see ci.yml).
Setting "Previous version" / "Specific analysis" / "Specific date" is a
Web-API-or-UI-only action — it is NOT a scanner property — so this
script drives the API directly, mirroring ``triage_hotspots.py``'s
Bearer-token pattern so the decision is reviewable in git history rather
than a hand-typed UI click.

Usage (locally with an admin SONAR_TOKEN env var):
    SONAR_TOKEN=xxxx python3 scripts/sonar/reset_new_code.py

Usage (CI):
    Triggered via .github/workflows/sonar-new-code-reset.yml
    workflow_dispatch. The workflow reads SONAR_TOKEN from GH secrets.

Idempotent: prints the New Code period before and after the set, so a
re-run is a no-op observation when already on PREVIOUS_VERSION.

References:
- https://sonarcloud.io/web_api/api/new_code_periods
- POST /api/new_code_periods/set — set a branch's New Code definition.
- GET  /api/new_code_periods/show — read the current definition.

Exit codes:
  0 — reset applied (or already in the requested state)
  1 — the API call failed (non-2xx) — fails closed, nothing was changed
  2 — usage error (SONAR_TOKEN unset)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

SONAR_BASE = "https://sonarcloud.io"
PROJECT_KEY = "three-cubes_kairix"
BRANCH = "main"
# "Previous version" — the only New Code mode drivable purely by a scanner
# property (sonar.projectVersion, emitted per release in ci.yml). Clean for
# trunk-based single-main analysis; self-maintaining via release bumps.
NEW_CODE_TYPE = "PREVIOUS_VERSION"


class ApiCaller(Protocol):
    """Transport seam: ``(method, path, **form_params) -> parsed JSON``.

    Injected so the reset logic is exercised with a fake in tests (no
    real network, no monkeypatch — F1/F2). The production default is
    ``_default_caller`` below.
    """

    def __call__(self, method: str, path: str, **params: str) -> dict: ...


def _default_caller(token: str) -> ApiCaller:
    """Real SonarCloud transport — Bearer-token urllib POST/GET.

    Mirrors ``scripts/sonar/triage_hotspots.py``'s ``_api`` helper.
    Raises ``urllib.error.HTTPError`` on non-2xx; the caller fails closed.
    """

    def _call(method: str, path: str, **params: str) -> dict:
        url = SONAR_BASE + path
        body: bytes | None = None
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "kairix-sonar-reset/1.0",
        }
        if method == "GET" and params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        elif params:
            body = urllib.parse.urlencode(params).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    return _call


def show_period(caller: ApiCaller) -> dict:
    """Return the current New Code period for ``BRANCH`` (GET /show)."""
    return caller(
        "GET",
        "/api/new_code_periods/show",
        project=PROJECT_KEY,
        branch=BRANCH,
    )


def set_period(caller: ApiCaller) -> None:
    """Set the New Code period for ``BRANCH`` to ``NEW_CODE_TYPE`` (POST /set).

    The /set endpoint returns an empty 204 body on success.
    """
    caller(
        "POST",
        "/api/new_code_periods/set",
        project=PROJECT_KEY,
        branch=BRANCH,
        type=NEW_CODE_TYPE,
    )


def reset_new_code(caller: ApiCaller) -> int:
    """Read → set → read the New Code period. Fails closed on any HTTP error.

    Returns the process exit code (0 ok, 1 API failure).
    """
    try:
        before = show_period(caller)
        print(f"before: {json.dumps(before, sort_keys=True)}")
        set_period(caller)
        print(f"set: project={PROJECT_KEY} branch={BRANCH} type={NEW_CODE_TYPE}")
        after = show_period(caller)
        print(f"after: {json.dumps(after, sort_keys=True)}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        print(
            f"::error::SonarCloud new-code reset failed — HTTP {exc.code} {exc.reason}: {detail}",
            file=sys.stderr,
        )
        print(
            "::error::fix: confirm SONAR_TOKEN has Administer permission on the project; "
            "next: re-run the workflow once the token scope is corrected",
            file=sys.stderr,
        )
        return 1
    return 0


def main(caller: ApiCaller | None = None, token: str | None = None) -> int:
    """Run the reset.

    ``caller`` injects the transport (a fake in tests, F1/F2-clean). When
    omitted, the real ``_default_caller`` is built from ``token`` — which
    itself defaults to the ``SONAR_TOKEN`` env var at the boundary. Tests
    drive the missing-token branch by passing ``token=""`` explicitly, so
    no process-env mutation is needed.
    """
    if caller is None:
        if token is None:
            token = os.environ.get("SONAR_TOKEN", "")
        if not token:
            print("ERROR: SONAR_TOKEN env var not set", file=sys.stderr)
            return 2
        caller = _default_caller(token)
    return reset_new_code(caller)


if __name__ == "__main__":
    sys.exit(main())
