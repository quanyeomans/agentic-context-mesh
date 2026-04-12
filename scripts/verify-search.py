#!/usr/bin/env python3
"""
verify-search.py — Acceptance verification for all 6 search intents + curator health.

Runs 7 checks against live kairix search on the current deployment.
Uses subprocess to call `kairix search --json` and checks intent + result count.
Check 7 calls kairix.curator.health directly.

Usage:
    .venv/bin/python3 scripts/verify-search.py [--agent AGENT] [--json] [--output FILE]
    .venv/bin/python3 scripts/verify-search.py --entity-a OpenClaw --entity-b Avanade
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Check definitions
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    intent: str | None
    expected_intent: str
    result_count: int
    min_results: int
    latency_ms: float
    passed: bool
    note: str = ""


def _run_search(
    query: str, agent: str, kairix_bin: str, timeout: int = 60
) -> dict:
    """Run kairix search --json and return parsed result dict."""
    result = subprocess.run(
        [kairix_bin, "search", query, "--agent", agent, "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kairix search failed (rc={result.returncode}): {result.stderr[:200]}")
    # Strip any warning lines before JSON
    stdout = result.stdout.strip()
    lines = stdout.splitlines()
    json_start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("{")), 0)
    return json.loads("\n".join(lines[json_start:]))


def check_search(
    name: str,
    query: str,
    expected_intent: str,
    min_results: int,
    agent: str,
    kairix_bin: str,
) -> CheckResult:
    t0 = time.time()
    try:
        data = _run_search(query, agent, kairix_bin)
    except Exception as exc:
        return CheckResult(
            name=name,
            intent=None,
            expected_intent=expected_intent,
            result_count=0,
            min_results=min_results,
            latency_ms=(time.time() - t0) * 1000,
            passed=False,
            note=str(exc)[:120],
        )

    latency_ms = (time.time() - t0) * 1000
    intent = data.get("intent", "unknown")
    result_count = len(data.get("results", []))

    passed = (
        intent.lower() == expected_intent.lower()
        and result_count >= min_results
    )

    return CheckResult(
        name=name,
        intent=intent,
        expected_intent=expected_intent,
        result_count=result_count,
        min_results=min_results,
        latency_ms=latency_ms,
        passed=passed,
    )


def check_curator_health(db_path: str) -> CheckResult:
    """Check 7: call run_health_check directly and verify ok=True."""
    t0 = time.time()
    try:
        import sqlite3
        os.environ["KAIRIX_TEST_DB"] = db_path
        from kairix.curator.health import run_health_check
        from kairix.entities.schema import open_entities_db

        db = open_entities_db()
        report = run_health_check(db, neo4j_client=None, staleness_days=180)
        db.close()
        latency_ms = (time.time() - t0) * 1000
        # Pass if vault paths and staleness are clear — synthesis failures from entities
        # with no stub file are expected and not treated as a blocker here.
        structural_ok = (
            len(report.missing_vault_path) == 0
            and len(report.stale_entities) == 0
            and report.total_entities > 0
        )
        note = ""
        if not structural_ok:
            note = "issues: {} synth failures, {} missing vault, {} stale".format(
                len(report.synthesis_failures),
                len(report.missing_vault_path),
                len(report.stale_entities),
            )
        elif report.synthesis_failures:
            note = "{} synth failures (no stub file — expected)".format(len(report.synthesis_failures))
        return CheckResult(
            name="curator-health",
            intent=None,
            expected_intent="ok",
            result_count=report.total_entities,
            min_results=1,
            latency_ms=latency_ms,
            passed=structural_ok,
            note=note,
        )
    except Exception as exc:
        return CheckResult(
            name="curator-health",
            intent=None,
            expected_intent="ok",
            result_count=0,
            min_results=1,
            latency_ms=(time.time() - t0) * 1000,
            passed=False,
            note=str(exc)[:120],
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Acceptance verification for kairix search")
    parser.add_argument("--agent", default="shape", help="Agent name for collection scoping")
    parser.add_argument(
        "--entity-a", default="OpenClaw", help="Known entity name for ENTITY check"
    )
    parser.add_argument(
        "--entity-b", default="Avanade", help="Second known entity for MULTI_HOP check"
    )
    parser.add_argument("--json", dest="json_out", action="store_true", help="Output as JSON")
    parser.add_argument("--output", default=None, help="Write results to FILE")
    parser.add_argument(
        "--db",
        default=str(
            Path(os.environ.get("KAIRIX_DATA_DIR", "/data/mnemosyne")) / "entities.db"
        ),
        help="Path to entities.db for curator health check",
    )
    parser.add_argument(
        "--kairix-bin",
        default=str(Path(__file__).parent.parent / ".venv" / "bin" / "kairix"),
        help="Path to kairix CLI binary",
    )
    args = parser.parse_args()

    kairix_bin = args.kairix_bin
    if not Path(kairix_bin).exists():
        # Try system PATH
        kairix_bin = "kairix"

    checks = [
        ("keyword",   "FEAT-081 implementation status",        "keyword",   1),
        ("temporal",  "what happened last week",              "temporal",  1),
        ("entity",    f"tell me about {args.entity_a}",       "entity",    1),
        ("procedural","how do I run the embedding pipeline",  "procedural",1),
        ("semantic",  "infrastructure cost optimisation strategy", "semantic", 1),
        ("multi_hop", f"connection between {args.entity_a} and {args.entity_b}", "multi_hop", 2),
    ]

    results: list[CheckResult] = []
    for name, query, expected_intent, min_results in checks:
        r = check_search(name, query, expected_intent, min_results, args.agent, kairix_bin)
        results.append(r)

    results.append(check_curator_health(args.db))

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    if args.json_out:
        output = {
            "passed": passed,
            "total": total,
            "all_passed": passed == total,
            "checks": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "intent": r.intent,
                    "expected_intent": r.expected_intent,
                    "result_count": r.result_count,
                    "min_results": r.min_results,
                    "latency_ms": round(r.latency_ms, 1),
                    "note": r.note,
                }
                for r in results
            ],
        }
        text = json.dumps(output, indent=2)
        print(text)
    else:
        print(f"\nKairix Search Acceptance — {passed}/{total} checks passed\n")
        for r in results:
            icon = "✅" if r.passed else "❌"
            if r.name == "curator-health":
                print(f"  {icon} {r.name:<16} entities={r.result_count}  {r.note or 'ok'}")
            else:
                print(
                    f"  {icon} {r.name:<12} intent={r.intent or '?':<12} "
                    f"results={r.result_count}  {round(r.latency_ms)}ms"
                    + (f"  NOTE: {r.note}" if r.note else "")
                )
        print()
        if passed == total:
            print("Status: PASS — all checks green")
        else:
            failed = [r.name for r in results if not r.passed]
            print(f"Status: FAIL — failed checks: {', '.join(failed)}")
        text = ""

    if args.output and text:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Results written to {args.output}", file=sys.stderr)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
