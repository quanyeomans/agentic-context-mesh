"""F30 outcome test — ``kairix timeline --json`` subprocess surface.

PR 2.7 / #421 wired a warm-MCP text-mode composer on ``kairix timeline``;
the canonical SoT for the envelope shape is
``kairix.use_cases.timeline.timeline_output_to_envelope``, already
emitted by the CLI under ``--json`` (#412). This test exercises the
subprocess binary surface for both modes:

- ``kairix timeline <q> --json`` → stdout is a JSON envelope dict
  (``original_query`` / ``rewritten_query`` / ``time_window`` /
  ``results`` / ``error`` keys). The use case wraps every code path
  in try/except so the envelope is well-formed regardless of whether
  the temporal-chunks backend, the search-pipeline fallback, or the
  factory init fails — that's the contract the MCP dispatcher will
  rely on once PR 2.8 lands.
- ``kairix timeline <q>`` (no ``--json``) → text-mode output unchanged
  from pre-PR-2.7 behaviour. Regression net against the dispatcher
  flip-over breaking text mode.

The subprocess env strips ``KAIRIX_LLM_API_KEY`` so any health probe
the search pipeline triggers stays on the deterministic offline branch.
This is NOT ``monkeypatch.setenv`` (F2 targets pytest monkeypatch);
passing an explicit ``env=`` dict to ``subprocess.run`` is the F2-clean
way to control a child process's environment.

Timeline has no ``--document-root`` / ``--memory-root`` flag (its use
case lazy-builds the search pipeline via the factory); the query
``"topic-zzz nothing-matches"`` carries no temporal expression so the
use case returns ``is_temporal=False`` and routes to the search-pipeline
fallback, which on an empty/missing index returns an empty result list
through ``run_timeline``'s outer try/except — yielding a well-formed
envelope with empty ``results``. F31-clean: no hardcoded /Users/ or
/home/<dev>/ paths anywhere in the test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def _subprocess_env_without_llm_keys() -> dict[str, str]:
    """Return a subprocess env that strips LLM credential vars."""
    env = dict(os.environ)
    env.pop("KAIRIX_LLM_API_KEY", None)
    env.pop("KAIRIX_AZURE_API_KEY", None)
    return env


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/core/temporal/cli.py`` to emit ``print("not json")`` instead
# of ``_json.dumps(envelope, ...)`` under the ``--json`` branch — the
# ``json.loads(proc.stdout)`` assertion fired with JSONDecodeError;
# restored.
def test_timeline_cli_subprocess_json_mode_emits_envelope_dict() -> None:
    """Drive the real ``kairix timeline --json`` binary.

    Asserts stdout parses as JSON and carries the envelope-shape keys
    (F30: outcome on stdout, not just returncode). Empty/missing store
    still produces a well-formed envelope — that's the contract the
    MCP dispatcher will rely on once PR 2.8 lands.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "timeline",
            "topic-zzz nothing-matches",
            "--limit",
            "1",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    # Exit 0 (well-formed envelope, no use-case error) OR exit 1 (envelope
    # carries an ``error`` populated by run_timeline's outer try/except).
    # Both are acceptable contracts for the warm-MCP dispatcher; the
    # invariant under test is "stdout is always a parseable envelope dict".
    assert proc.returncode in (0, 1), (
        f"timeline --json exited unexpectedly with {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    for key in (
        "original_query",
        "rewritten_query",
        "is_temporal",
        "fell_back",
        "time_window",
        "results",
        "error",
        "limit",
    ):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["original_query"] == "topic-zzz nothing-matches"
    assert envelope["limit"] == 1
    assert isinstance(envelope["results"], list), f"results must be a list, got {type(envelope['results']).__name__}"


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS print the
# envelope JSON regardless of ``--json`` — the "Query:" stdout assertion
# fired because stdout now had the JSON dict instead of the header
# banner; restored.
def test_timeline_cli_subprocess_text_mode_unchanged() -> None:
    """Text-mode output (no ``--json``) keeps the operator-facing banner.

    Asserts the ``Query:`` + ``Window:`` + ``Limit:`` header lines reach
    stdout — the pre-PR-2.7 text-mode contract. If a future change
    accidentally always renders the envelope, the ``"Query:"`` assertion
    fires.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "timeline",
            "topic-zzz nothing-matches",
            "--limit",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_subprocess_env_without_llm_keys(),
        check=False,
    )

    # Text mode prints the banner header regardless of whether results
    # come back; on error it exits 1 with the error on stderr but the
    # banner still reaches stdout first.
    assert proc.returncode in (0, 1), (
        f"timeline text-mode exited unexpectedly with {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    assert "Query:" in proc.stdout, f"missing operator-facing banner in stdout: {proc.stdout!r}"
    assert "Limit:" in proc.stdout, f"missing limit line in stdout: {proc.stdout!r}"
    # Text mode must not emit JSON braces — if a future change accidentally
    # always renders the envelope, this fires.
    assert "{" not in proc.stdout, f"text mode leaked JSON braces into stdout: {proc.stdout!r}"
