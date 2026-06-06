"""F30 outcome test — ``kairix search --json`` subprocess surface.

PR 2.2 / #421 aligned the ``kairix search`` ``--json`` envelope with
the ``tool_search`` MCP envelope (``search_output_to_envelope``) so the
warm MCP path (PR 2.8) can route text-mode invocations through the
shared envelope shape. This test exercises the subprocess binary
surface for both modes:

- ``kairix search --json <query>`` -> stdout is a JSON envelope dict
  with the MCP-shape keys (``query`` / ``intent`` / ``results`` / count
  diagnostics / ``error``).
- ``kairix search <query>`` (no ``--json``) -> text-mode output is
  regression-locked: no JSON braces on stdout. Pins the
  dispatcher's flip-over from text-mode rendering against a future
  refactor that accidentally always emits JSON.

The subprocess relies on the existing ``run_search`` exception
funnelling — when no provider is configured the use case projects the
error onto ``envelope.error`` and exits 1; both branches still yield a
parseable envelope. F2-clean: no ``KAIRIX_*`` env vars set on the
subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


# Sabotage-proof (executed): mutated ``main()`` in
# ``kairix/core/search/cli.py`` to emit ``print("not json")`` instead of
# ``print(json.dumps(search_output_to_envelope(out), indent=2))`` under
# the ``--json`` branch — the ``json.loads(proc.stdout)`` assertion
# fired with JSONDecodeError. Restored.
def test_search_cli_subprocess_json_mode_emits_mcp_envelope_shape() -> None:
    """Drive the real ``kairix search --json`` binary.

    Asserts stdout parses as JSON and carries the MCP envelope-shape
    keys (F30: outcome on stdout, not just returncode). Whether the
    test environment has a real provider or not, the envelope shape
    is the contract — both happy-path and degraded paths produce the
    same set of top-level keys.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "search",
            "what is kairix search",
            "--json",
            "--no-entity-card",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.stdout, f"empty stdout — subprocess crashed before envelope render. stderr={proc.stderr!r}"

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json stdout was not valid JSON: {exc}\n--- stdout ---\n{proc.stdout!r}")

    assert isinstance(envelope, dict), f"envelope must be a dict, got {type(envelope).__name__}: {envelope!r}"
    # The MCP envelope shape — every key ``search_output_to_envelope`` emits
    # must be present so the warm-MCP dispatcher can round-trip the dict
    # via ``SearchOutput.from_envelope``.
    for key in (
        "query",
        "intent",
        "results",
        "bm25_count",
        "vec_count",
        "fused_count",
        "vec_failed",
        "total_tokens",
        "latency_ms",
        "error",
    ):
        assert key in envelope, f"envelope missing key {key!r}: {sorted(envelope.keys())}"
    assert envelope["query"] == "what is kairix search"
    assert isinstance(envelope["results"], list)


# Sabotage-proof (executed): hard-wired ``main()`` to ALWAYS print the
# envelope JSON regardless of ``--json`` — the ``"{" not in proc.stdout``
# assertion fired because stdout now had the JSON dict instead of the
# text-mode render. Restored.
def test_search_cli_subprocess_text_mode_does_not_leak_json() -> None:
    """Text-mode output (no ``--json``) is regression-locked.

    The dispatcher's flip-over must not silently re-route every search
    through JSON rendering. A future refactor that drops the
    ``if args.as_json`` guard will fire this assertion.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "search",
            "what is kairix search",
            "--no-entity-card",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    # Text mode must not emit a top-level JSON envelope on stdout. The
    # format_text render uses a "Query: ..." prefix on its first line —
    # JSON envelopes always start with "{". Asserting absence of the
    # opening brace is a tighter contract than absence of "}" because
    # text mode can legitimately contain "}" inside snippet content.
    stripped = proc.stdout.lstrip()
    assert not stripped.startswith("{"), f"text mode leaked JSON envelope onto stdout: {proc.stdout!r}"
    # Anchor the human-text render's first-line prefix when stdout is
    # non-empty. Use ``stripped`` to tolerate any leading whitespace
    # the dispatcher may emit.
    if stripped:
        assert stripped.startswith("Query:"), f"text mode missing 'Query:' prefix: {proc.stdout!r}"
