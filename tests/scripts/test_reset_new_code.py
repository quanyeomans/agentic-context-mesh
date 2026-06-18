"""Outcome tests for ``scripts/sonar/reset_new_code.py`` (#547).

The script POSTs to SonarCloud's ``/api/new_code_periods/set`` to reset
the drifted New Code window for ``main``. These tests inject a FAKE
``ApiCaller`` transport (no real network, no monkeypatch — F1/F2) and
assert: (a) it reads → sets → reads with the right endpoints + params,
and (b) it fails CLOSED (exit 1) on a non-2xx API response without
having silently "succeeded".

The module lives outside the ``kairix`` package, so it is loaded by
path like the other ``scripts/`` outcome tests.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

# Load the reset module directly — it lives outside the kairix package.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sonar" / "reset_new_code.py"
_spec = importlib.util.spec_from_file_location("_reset_new_code", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_reset = importlib.util.module_from_spec(_spec)
sys.modules["_reset_new_code"] = _reset
_spec.loader.exec_module(_reset)


pytestmark = pytest.mark.unit


class _RecordingCaller:
    """Fake ApiCaller — records every (method, path, params) call and
    returns canned JSON. Optionally raises a given exception on the first
    call to simulate an API failure."""

    def __init__(self, *, raise_on_first: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self._raise_on_first = raise_on_first
        self._fired = False

    def __call__(self, method: str, path: str, **params: str) -> dict:
        self.calls.append((method, path, dict(params)))
        if self._raise_on_first and not self._fired:
            self._fired = True
            raise self._raise_on_first
        return {"newCodePeriod": {"type": "PREVIOUS_VERSION"}}


def _http_error(code: int) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(
        url="https://sonarcloud.io/api/new_code_periods/set",
        code=code,
        msg="Forbidden",
        hdrs=None,  # type: ignore[arg-type]  # urllib accepts None hdrs; not read in the fail path
        fp=io.BytesIO(b"Insufficient privileges"),
    )


def test_reset_posts_set_endpoint_with_correct_params() -> None:
    caller = _RecordingCaller()
    rc = _reset.main(caller=caller)

    assert rc == 0
    # The POST /set is the load-bearing call — endpoint + params must be exact.
    posts = [c for c in caller.calls if c[0] == "POST"]
    assert len(posts) == 1, f"expected exactly one POST, got {caller.calls}"
    _method, path, params = posts[0]
    assert path == "/api/new_code_periods/set"
    assert params == {
        "project": "three-cubes_kairix",
        "branch": "main",
        "type": "PREVIOUS_VERSION",
    }


def test_reset_reads_period_before_and_after_set() -> None:
    caller = _RecordingCaller()
    rc = _reset.main(caller=caller)

    assert rc == 0
    # show → set → show ordering proves the script observes the change.
    methods_paths = [(c[0], c[1]) for c in caller.calls]
    assert methods_paths == [
        ("GET", "/api/new_code_periods/show"),
        ("POST", "/api/new_code_periods/set"),
        ("GET", "/api/new_code_periods/show"),
    ]


def test_reset_fails_closed_on_non_2xx() -> None:
    """A 403 (token lacks Administer permission) must fail CLOSED — exit 1,
    not a silent success. The HTTPError surfaces on the very first GET so
    the POST never fires (nothing is half-applied)."""
    caller = _RecordingCaller(raise_on_first=_http_error(403))
    rc = _reset.main(caller=caller)

    assert rc == 1
    # Only the first GET was attempted; no POST went out.
    assert [c[0] for c in caller.calls] == ["GET"]


def test_reset_fails_closed_when_set_itself_errors() -> None:
    """If the POST /set is the call that 4xx's, the script must still fail
    closed (exit 1) rather than reporting the reset as applied."""

    class _SetFails(_RecordingCaller):
        def __call__(self, method: str, path: str, **params: str) -> dict:
            self.calls.append((method, path, dict(params)))
            if method == "POST":
                raise _http_error(400)
            return {"newCodePeriod": {"type": "PREVIOUS_VERSION"}}

    caller = _SetFails()
    rc = _reset.main(caller=caller)

    assert rc == 1
    # The set was attempted; the trailing "after" read never ran.
    assert [c[0] for c in caller.calls] == ["GET", "POST"]


def test_main_returns_2_when_token_unset() -> None:
    """No caller injected + an empty token → usage error (exit 2). The
    empty token is passed explicitly through the ``token=`` seam, so the
    missing-credential branch is exercised WITHOUT mutating process env
    (F1/F2-clean — no monkeypatch on the boundary)."""
    rc = _reset.main(token="")
    assert rc == 2
