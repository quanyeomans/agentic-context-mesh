# Operational tests as kairix capabilities — design (closes #276)

> **Status**: Implemented. [#276](https://github.com/three-cubes/kairix/issues/276) is CLOSED — the soak / load / probe capabilities shipped under `kairix/quality/probe/` and `kairix/quality/soak/`, and the affordance gate shipped as the `capability-affordance` fitness rule. This document is retained as the durable record of the two design rules below and as the pointer to the shipped surface.
>
> **Companion**: this design is the test-tooling complement to [`sre-worker-design.md`](sre-worker-design.md). The SRE worker doesn't implement health checks itself — it invokes the primitives defined here.

## Shipped state — where the capabilities live now

The design below is realised in code. Use this section as the index; the rest of the document is the rationale.

| design surface | shipped as |
|---|---|
| Soak / load / probe Python APIs | `kairix/quality/probe/{runner,burst,perf_runner,executor,sampler,stats,clients}.py` and `kairix/quality/soak/runner.py` |
| Soak + concurrent-latency CLI | folded into `kairix benchmark` (the former `kairix probe` / `kairix soak` modes — see `kairix/cli.py`) |
| Provider/tuning probe + observability CLIs | `kairix probe-config`, `kairix mcp-calls`, `kairix caches`, `kairix warm` (`kairix/quality/probe/{config_cli,mcp_calls_cli,caches_cli}.py`, `kairix/platform/warm/cli.py`) |
| Affordance enforcement (proposed below as `F25`) | shipped as the id-agnostic **`capability-affordance`** rule (`scripts/checks/check_capability_affordance.py`); the catalogue uses descriptive names, not an `F25` number |
| Capability-probe at the readiness boundary | `kairix/agents/mcp/capability_probe.py` (wired into `/healthz/ready`) |
| PVT transport seam (Phase 4) | `kairix/quality/probe/clients.py` (`SearchClient` Protocol) |

The two rules below are the durable principles this work encoded; treat them as the canonical statement of how an operational test becomes a kairix capability.

## Why this design

Two architectural rules emerged from today's alpha-validation work (#272 Phase 4) and the regression it caught (#275):

**Rule 1 — SDLC vs operational separation.** A test that exists only to manage versioned change belongs in CI. A test with any operational use — periodic health, on-demand diagnostic, post-incident probe — is a first-class kairix capability.

**Rule 2 — Capabilities, not bindings.** Every kairix capability is implemented once as a Python API. CLI, MCP, and (future) HTTP are *bindings* over that API. Which bindings get exposed is a per-capability decision based on safety + cost + invoker, not implicit pattern.

The previous draft of #276 made Rule 1 explicit but stopped short on Rule 2 — it treated "CLI primitive" as the synonym for "operational capability". That conflation matters: it would have left the SRE worker shelling out to subprocesses even when calling the Python API directly (or MCP) would have been cleaner, and it left agents with no path to invoke diagnostic capabilities they have a legitimate reason to use.

## The two rules in practice

### Rule 1 — what stays in `tests/` vs what becomes a capability

**Stays in `tests/`** (CI-only):
- `tests/unit/`, `tests/integration/`, `tests/contracts/`, `tests/bdd/` — correctness
- `mypy --strict` — type correctness
- Architecture fitness functions (F1-F24, G1-G10) — design correctness
- `ruff check` / `ruff format` — style correctness
- `detect-secrets`, confidential-pattern check — supply-chain correctness

These never run in production, never need to be invoked by the SRE worker, never give meaningful signal at runtime. They gate code into main. That's their entire job.

**Becomes a kairix capability** (Python API + bindings):
- Health probes (`onboard check`)
- Quality benchmarks (`benchmark run`)
- Operational maintenance (`store crawl`, `embed`, `worker pause/resume`)
- **New**: soak / load / log-volume probes (this design's Phase 1+2)

A capability earns its slot by being needed in *more than one place*. If only CI would invoke it, it stays in `tests/`.

### Rule 2 — Python API → binding decision matrix

Every capability has one Python implementation. Each binding is a thin shell:

```
kairix.quality.soak.run_soak(...)         # Python API — the source of truth
  ↑                ↑                ↑
  CLI binding                            MCP binding      HTTP binding (future)
  kairix benchmark run --mode soak       tool_soak_run    POST /v1/soak/run
```

Which bindings get exposed per capability:

| factor | impact on MCP exposure |
|---|---|
| Read-only / measurement-only | ✅ MCP-safe |
| Mutates state (e.g., embed, store crawl) | ⚠️ MCP only if mutation is bounded and idempotent |
| Load-generating (concurrent burst, sustained soak) | ❌ CLI-only unless scope-limited (e.g., `tool_probe_search` with hard concurrency cap = 3) |
| Runtime > 30s | ❌ MCP impractical (RPC timeout); CLI + async pattern |
| Has dangerous failure mode if agent invokes accidentally | ❌ CLI-only with operator confirmation |
| Has obvious agentic use case (agent self-diagnosing) | ✅ MCP exposure mandatory |

The decision per capability lives in the capability's design — it is NOT implicit.

## Capabilities catalogue (this design's scope)

### Existing capabilities — binding posture (as shipped)

| capability | Python API | CLI | MCP | rationale for posture |
|---|---|---|---|---|
| `onboard check` | ✅ `kairix.platform.onboard.check.run_onboard_check` | ✅ `kairix onboard check` | ✅ `tool_onboard_check` | read-only, agent-useful — **MCP exposure shipped** (server.py) |
| `worker status` | ✅ | ✅ `kairix worker status` | ✅ `tool_worker_status` | read-only, agent-useful — **MCP exposure shipped** (server.py) |
| `benchmark run` | ✅ | ✅ `kairix benchmark` | ❌ | minutes-long, load-generating — **stays CLI-only** |
| `embed --limit N` | ✅ | ✅ `kairix embed` | ❌ | mutates state, expensive — **stays CLI-only** |
| `store crawl` | ✅ | ✅ `kairix store crawl` | ❌ | mutates Neo4j, minutes-long — **stays CLI-only** |
| `embed rebuild-fts` | ✅ | ✅ | ❌ | destructive recovery action — **stays CLI-only with operator confirmation** |

The two safe read-only capabilities (`onboard check`, `worker status`) were retroactively MCP-exposed exactly as planned — `tool_onboard_check` and `tool_worker_status` are registered in `kairix/agents/mcp/server.py`.

### New capabilities — binding posture (as shipped)

| capability | CLI | MCP | rationale |
|---|---|---|---|
| `soak run` | ✅ folded into `kairix benchmark` (the former `kairix soak` mode); Python API `kairix.quality.soak.runner` | ❌ stub only (`tool_soak_run` returns an operator-handoff envelope) | minutes-long; load-generating; gate behind operator/SRE-worker explicit invocation |
| `probe search` (low-concurrency) | ✅ folded into `kairix benchmark` (the former `kairix probe` concurrent mode); Python API `kairix.quality.probe.run_probe_search` | ✅ `tool_probe_search` with hard cap concurrency≤3, queries≤20 | hard-capped variant is read-only; full variant is load-generating (CLI opens the range) |
| `probe log-volume` | ✅ `kairix benchmark run --mode soak --max-log-volume-mb N` | ❌ | requires running a benchmark; long; better as CLI/scheduled |
| `probe factory-calls` (future) | ✅ | ❌ | development/debugging tool, not a runtime signal |

### `kairix benchmark run --mode soak` — full spec

Repeats a workload and asserts the system holds together across iterations. Catches the #275 class of bug — work that's individually fine but degrades when repeated.

```
kairix benchmark run --suite reflib --repeat 3 [--max-memory-growth-mb 50] [--max-log-volume-mb 5] [--json]
```

Assertions:
- Memory delta per iteration < `--max-memory-growth-mb` (default 50 MB)
- Per-iteration wall time within 20% of first iteration (no degradation)
- Total stderr bytes < `--max-log-volume-mb` × repeat (catches log-spam regressions)
- No new file descriptors held at exit
- Per-iteration `BenchmarkResult` is byte-identical (deterministic — proves no cross-iteration state leakage)

JSON envelope:
```json
{
  "suite": "reflib",
  "repeat": 3,
  "iterations": [
    {"weighted_total": 0.907, "duration_s": 142, "memory_mb": 312, "stderr_bytes": 4096, "fds": 23}
  ],
  "passed": true,
  "failures": []
}
```

**MCP posture**: not exposed. Soak runs take minutes and stress the system — agents have no legitimate reason to trigger them ad-hoc. Invoked by the SRE worker via subprocess and by alpha-deploy webhook on its deploy chain.

### `kairix benchmark run --mode concurrent` / `tool_probe_search` — full spec

CLI surface — full range available:
```
kairix benchmark run --mode concurrent --concurrency 10 --queries 100 [--query-mix builtin|file:path] [--json]
```

MCP surface — hard-capped safe subset:
```
tool_probe_search(query_mix="builtin", concurrency=3, queries=20)
# concurrency clamped to [1, 3]; queries clamped to [1, 20]
```

Assertions (both surfaces):
- p99 latency < threshold (default 5s; configurable in `kairix.config.yaml`)
- Zero exceptions
- No deadlocks (per-invocation timeout)

**MCP posture**: exposed at hard-capped variant. An agent seeing slow searches can verify whether kairix-side latency is the cause without imposing meaningful load. Operator running the CLI for full diagnostic gets the unrestricted form.

### `kairix benchmark run --mode soak --max-log-volume-mb N` — full spec

```
kairix benchmark run --mode soak --suite reflib [--max-bytes-per-case 100] [--json]
```

**MCP posture**: not exposed. Requires running a full benchmark; multi-minute; better as scheduled/CI invocation.

## Affordance — every surface guides agents to the right surface

This is the discoverability layer. Without it, an agent looking for "run a soak test" hits a dead end (MCP doesn't expose `tool_soak_run`) and has no way to know the CLI does. The fix is mandatory cross-surface affordance.

### Affordance pattern 1 — MCP stub tools with operator-handoff messages

For capabilities that are **CLI-only**, register a thin MCP stub that returns a structured envelope pointing the agent to the right escalation:

```python
@server.tool()
def tool_soak_run(suite: str = "reflib", repeat: int = 3) -> dict:
    """Run a kairix benchmark soak. Operator-only — agents must escalate."""
    return {
        "error": "OperatorOnlyCapability",
        "capability": "soak run",
        "reason": "Soak runs take minutes and stress the system. Agents should escalate to their admin.",
        "operator_command": f"kairix benchmark run --suite {suite} --repeat {repeat}",
        "expected_runtime_seconds": 60 * repeat,
        "see_also": ["docs/runbooks/kairix-retrieval-health.md#soak"],
    }
```

The agent gets a *structured failure with the exact escalation command*. That's the F21 affordance pattern (`fix:` / `next:` / `run:`) applied to capability discoverability — every dead end has a marked next step.

### Affordance pattern 2 — `tool_usage_guide` capability index

The existing `tool_usage_guide` (already MCP-exposed) gets a `capabilities` topic listing every kairix capability with its binding posture. An agent searching the guide for "diagnostics", "soak", "probe", "health" lands on:

```markdown
## Diagnostic capabilities

| capability | when to use | how to invoke |
|---|---|---|
| `tool_search` | retrieving content | MCP — direct |
| `tool_probe_search` | "is search slow?" | MCP — direct (hard-capped) |
| Full latency probe | full diagnostic | escalate: `kairix benchmark run --mode concurrent --concurrency 10 --queries 100` |
| Soak test | "does this hold under load?" | escalate: `kairix benchmark run --suite reflib --repeat 3` |
| Onboard check | "is kairix healthy?" | MCP — `tool_onboard_check` |
| Worker state | "is the worker running?" | MCP — `tool_worker_status` |
```

The guide is the index; the stubs are the per-capability handoffs. Both must ship — an agent that knows to call `tool_probe_search` doesn't need the guide; an agent who doesn't know what's available finds it via the guide.

### Affordance pattern 3 — CLI `--help` cross-references MCP

CLI help text on each command's first line includes the MCP equivalent (or its absence):

```
$ kairix benchmark run --mode soak --help
usage: kairix benchmark run --suite SUITE [--repeat N] [...]

Operational soak test — repeat a workload and assert it holds together
across iterations. Catches "unit-fine, scale-fragile" regressions.

MCP equivalent: none (operator-only — soaks are multi-minute load runs).
                Agents that need to verify load behaviour should escalate
                via `tool_soak_run`, which returns the escalation command.

Bindings: CLI only. See docs/architecture/operational-tests-design.md
                    for the per-capability binding decision.
```

```
$ kairix benchmark run --mode concurrent --help
usage: kairix benchmark run --mode concurrent --concurrency N --queries M [...]

Concurrent-load search probe — measures p50/p95/p99 latency.

MCP equivalent: tool_probe_search (hard-capped: concurrency≤3, queries≤20).
                For unrestricted concurrency, use this CLI form.
```

The help text *is* the affordance. An operator looking at `--help` learns whether to run from terminal or escalate to the agent surface.

### Affordance pattern 4 — `tool_capabilities()` introspection (shipped, Phase 3)

A capability for agents to discover the surface programmatically — registered in `kairix/agents/mcp/server.py` (`tool_capabilities()`):

```python
@server.tool()
def tool_capabilities() -> dict:
    """Return the full catalogue of kairix capabilities and bindings."""
    return {
        "capabilities": [
            {"name": "search", "mcp_tool": "tool_search", "cli": "kairix search", "category": "retrieval"},
            {"name": "soak_run", "mcp_tool": None, "cli": "kairix benchmark run --mode soak", "category": "diagnostic-operator-only", "escalate_via": "tool_soak_run"},
            {"name": "probe_search", "mcp_tool": "tool_probe_search", "cli": "kairix benchmark run --mode concurrent", "category": "diagnostic", "mcp_caps": {"concurrency_max": 3, "queries_max": 20}},
        ]
    }
```

The stub-tools + usage-guide pattern already gives agents working discoverability; `tool_capabilities` adds programmatic introspection for AI-driven SRE agents that want to enumerate the surface rather than guess. It shipped alongside the rest of Phase 3.

### Affordance enforcement — the `capability-affordance` fitness rule

To prevent the affordance layer rotting (this design proposed it as `F25`; it shipped under the catalogue's id-agnostic naming as the **`capability-affordance`** rule):

```python
# scripts/checks/check_capability_affordance.py — shipped
# For every CLI subcommand:
#   - either MCP-binds with the same name (tool_<subcommand>) OR
#   - MCP stub exists with operator-handoff envelope
# Sabotage-proof: introducing a new CLI command without an MCP binding
# or stub fails the gate.
```

This is enforced as the `capability-affordance` rule in the fitness catalogue. New CLI capability without affordance → block at pre-commit.

## How invokers use the catalogue

### The SRE worker (#243) — schedule + dispatch

The SRE worker rotates over capability invocations on a cadence. For each capability:
- Prefer Python API (in-process call) where the SRE worker is a Python process AND the call is short
- Subprocess CLI where the worker is Go OR the operation is long-running OR isolation is desired
- MCP where the worker is itself an AI-orchestrator (future shape)

The architectural invariant stands: **the SRE worker is a scheduler over capabilities, not a probe library**. Phase 1 of the SRE worker uses subprocess for everything. Phase 2 onwards may use Python API directly when both processes are Python.

### CI workflows — release-vm-deploy + alpha-gate

The alpha-deploy webhook (Go, on the VM) already shells out to `kairix benchmark run`. Phase 1 adds `kairix benchmark run --mode soak` to the same chain. Both are CLI subprocess invocations.

### Ad-hoc operator commands — terminal

`kairix benchmark run --mode soak`, `kairix benchmark run --mode concurrent`, etc. invoked directly. Standard CLI experience with `--help`, `--json`, and exit-code semantics.

### Agents via MCP

For capabilities exposed via MCP, agents call directly. For CLI-only capabilities, agents call the MCP stub and receive a structured escalation envelope.

## Phased rollout (history — all phases landed)

The rollout below is retained as the record of how the capability set was sequenced. Phases 1–4 have all landed; the one remaining deferral (`--mode stability`) is scoped explicitly in Phase 3.

### Phase 1 — soak primitive + affordance scaffolding ✅ landed

Shipped:
- soak Python API (`kairix.quality.soak.runner`)
- soak run folded into `kairix benchmark` (the former `kairix soak` mode)
- `tool_soak_run` MCP **stub** with operator-handoff envelope (pattern 1)
- `tool_usage_guide` updated with the capabilities table (pattern 2)
- CLI `--help` cross-references MCP / absence (pattern 3)
- `scripts/checks/check_capability_affordance.py` (the `capability-affordance` rule)
- Runbook section in `docs/runbooks/kairix-retrieval-health.md`
- Wired into CI / alpha-deploy webhook

Acceptance (proven at ship time):
- A soak run against pre-#275-fix state fails with `log_volume_exceeded`, exit 1.
- The same run against post-#275-fix state passes.
- `tool_soak_run` returns the expected operator-handoff envelope.
- Agent searching `tool_usage_guide("diagnostics")` gets the capabilities table.
- The `capability-affordance` fitness check blocks a hand-rolled new CLI command without affordance.

### Phase 2 — probe primitives + read-only MCP retroactives ✅ landed

Shipped:
- concurrent-latency probe folded into `kairix benchmark` (the former `kairix probe` mode) + `tool_probe_search` MCP (hard-capped)
- log-volume probe assertions on the soak path + MCP stub
- **Retroactive MCP exposures**: `tool_onboard_check`, `tool_worker_status`
- Tier-2 integration in CI suite

### Phase 3 — burst + failure-injection + capability introspection ✅ landed

Shipped:
- burst mode (CLI-only; `kairix/quality/probe/burst.py`) ✅ landed
- `tool_capabilities()` programmatic introspection (pattern 4; `kairix/agents/mcp/server.py`) ✅ landed
- `--mode stability` (long soak, CLI-only) — **deferred** until burst mode surfaces fault-shape regressions worth catching (see #276 Phase 3 closure)

### Phase 4 — PVT layer (production-truth measurement) ✅ landed

Phase 2 + 3 live verification surfaced that the in-process probe CLI measures the **Python-pipeline regression surface** — cold subprocess + in-process call — not what an agent over MCP actually experiences. The probes still surface the right Tier 1 lever recommendation; they just over-report magnitude.

Phase 4 redefines the layer that owns "what do agents see". Shipped:

- **`docs/architecture/performance-testing-approach.md`** — four-layer ADR (unit / bdd / integration / **PVT**), explicit framing that PVT is opt-in (`KAIRIX_PVT=1`), never CI-blocking.
- **`tests/pvt/`** — the PVT scenario suite (features + steps + `conftest.py` server fixture + `test_pvt_scenarios.py`) describing agent-experienced behaviours: cold-start envelope timing, warm-server baseline, teaming-load latency, session-start storm, in-session stability.
- **`kairix.quality.probe.clients.SearchClient` Protocol** — the seam between the probe runner and the transport. `InProcessSearchClient` is the production implementation.
- **#284** (CLOSED) — the MCP HTTP test harness (server fixture for the PVT layer) landed.

Residual: the production `MCPHttpSearchClient` drop-in is not yet a named class in `kairix/quality/probe/clients.py` — `clients.py` still ships only `InProcessSearchClient`, so the concurrent / burst probe CLIs remain the Python-pipeline regression gate (not the production-latency gate). The runbook §6 measurement-shape caveat applies until that production transport client lands.

## Out of scope (deliberately)

- **Pure micro-benchmarks** (pytest-benchmark style). Solve a different problem.
- **Replacement for `kairix benchmark run`**. That measures retrieval *quality*; soak/probe measure system *health*. Complements, not substitutes.
- **Distributed load**. Single-host first.
- **Auto-remediation triggered by probes**. The SRE worker design covers this; the probes themselves are observation-only.
- **Universal MCP exposure of every CLI**. Per-capability decision via Rule 2 — explicit, not blanket.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| MCP surface bloats to mirror every CLI | Rule 2: explicit per-capability decision; the `capability-affordance` rule enforces affordance not exposure |
| Agent triggers expensive operations via MCP | MCP variants are hard-capped (concurrency, queries, runtime); full surface is CLI-only |
| Stub tools mislead agents into thinking the operation isn't supported | Stub returns structured envelope with `operator_command` and `expected_runtime_seconds` — clear path to escalation |
| `tool_usage_guide` capabilities table drifts from reality | the `capability-affordance` rule cross-checks the table against the actual CLI dispatch + MCP registry; new capability without table entry fails the gate |
| Retroactive MCP exposure breaks existing agents | New MCP tools are additive; existing tool surface unchanged |

## Related

- [#276](https://github.com/three-cubes/kairix/issues/276) — this issue
- [#275](https://github.com/three-cubes/kairix/issues/275) — the regression that motivated this design
- [#243](https://github.com/three-cubes/kairix/issues/243) — SRE worker (primary capability consumer)
- [`sre-worker-design.md`](sre-worker-design.md) — companion; specifies the worker as a scheduler over the capabilities defined here
- [#272](https://github.com/three-cubes/kairix/issues/272) — alpha-validation chain (Phase 1 proving ground)
- [#284](https://github.com/three-cubes/kairix/issues/284) — MCP HTTP test harness for the Phase 4 PVT layer (CLOSED)
- Retroactive MCP exposure of `onboard check` + `worker status` — **shipped** as `tool_onboard_check` / `tool_worker_status` (`kairix/agents/mcp/server.py`)
