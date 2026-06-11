# Cold-start envelope — operator reference

**Audience:** operators triaging an MCP client that surfaced a `KAIRIX_COLD_START` response, and developers verifying the envelope contract didn't drift.

## What this is

When an agent or HTTP client calls the kairix MCP server during the brief window between the uvicorn port binding and the retrieval stack finishing its one-time warm-up, the server returns HTTP 503 with a structured `KAIRIX_COLD_START` envelope and a `Retry-After` header. The envelope is the documented retry contract: well-behaved clients wait the advertised seconds and retry the same call — the second call lands on a warm process and returns normally. This is the system working as designed, not a bug. The contract exists because, before #383/#406, MCP clients saw an opaque `fetch_failed` during this window and silently dropped the call instead of retrying. The reference envelope below is the byte-exact response captured from the production VM during the 2026-06-06 cold-start drill, and is locked in place by the nightly soak test `tests/soak/test_cold_start_envelope_visible_on_restart.py`.

## Reference envelope

Captured 2026-06-06T13:12:16Z from the production VM during a deliberate `docker compose restart kairix`. Source artefact: `/tmp/cold_start_drill_20260606T131156Z/first_response_headers.txt` + `first_response_body.json` on the VM. The bytes below are verbatim — every field, every value.

```
HTTP/1.1 503 Service Unavailable
date: Sat, 06 Jun 2026 13:12:16 GMT
server: uvicorn
retry-after: 8
content-length: 632
content-type: application/json

{
    "status": "retryable_not_ready",
    "error": "ColdStart",
    "error_code": "KAIRIX_COLD_START",
    "tool": "/mcp",
    "retry_after_ms": 8000,
    "estimated_seconds_remaining": 8.0,
    "guidance": "kairix is warming (one-time cost per process). next: wait ~8s and retry — subsequent calls in this process return immediately.",
    "agent_instruction": "next: pause retry_after_ms then call '/mcp' again. fix: if the second call still returns ColdStart, surface \"kairix still warming after ~8s\" to the user and ask whether to proceed without retrieval — this is a transient process-boot state, not a hard failure.",
    "see_also": [
        "docs/operations/MCP-DEPLOYMENT.md"
    ]
}
```

Field-by-field meaning:

| Field | Meaning |
|---|---|
| `status` | Machine-readable state. Always `retryable_not_ready` for cold-start — distinct from `error` (permanent) or `unavailable` (downstream down). |
| `error` / `error_code` | The dual identifier MCP clients pivot on. `error_code` is the stable machine field; `error` is the human-readable class name. |
| `tool` | The request path the middleware intercepted. `/mcp` here because the gate fires before the MCP router resolves which named tool the client was calling. |
| `retry_after_ms` | Advertised back-off in milliseconds. Matches the `retry-after` header (in seconds) within 1s rounding. |
| `estimated_seconds_remaining` | Best-estimate of remaining warm time. Static (8.0) when warm hasn't started; live from `WarmProgress` once warm is in flight. |
| `guidance` | Operator-facing prose with the F21 `next:` action marker. |
| `agent_instruction` | LLM-agent-facing prose with both `next:` (the action) and `fix:` (the escalation) markers — agents pivot on these to find action steps without re-reading the prose. |
| `see_also` | Docs an operator or agent should consult for context — currently the single MCP deployment doc. |

## Timeline — production drill 2026-06-06

The drill at `/tmp/cold_start_drill_20260606T131156Z/log.txt` captured the full restart-to-warm window:

1. **T+0 (13:11:56Z)** — drill start; warm baseline `curl http://127.0.0.1:8090/mcp` returns HTTP 200 in 3.84s.
2. **T+4s (13:12:00Z)** — `docker compose restart kairix` issued. Container reports `Restarting` then `Started`.
3. **T+15s..T+20s** — port 8090 not yet responsive. Polling sees HTTP 000 (connection refused) on attempts 1–6, ~1s apart.
4. **T+21s (13:12:16Z)** — first responsive call. HTTP 503, 632-byte body, `retry-after: 8` header — the envelope above.
5. **T+22s..T+23s (post-drill polling)** — still cold on the next two polls.
6. **T+24s (13:12:19Z)** — first warm response (HTTP 200) from the same endpoint.

**Total restart-to-warm: ~14s.** The 5–6s of "connection refused" before the first 503 is the uvicorn process restarting and re-binding the port; once bound, the cold-start middleware short-circuits with the envelope until warm completes ~3s later. The Retry-After hint of 8s comfortably covers this window, so a single retry after the advertised back-off lands on a warm process.

## What to do when you see this

**Agent / MCP client side.** The contract is one retry, then escalate.

1. Parse the response body as JSON.
2. Read `retry_after_ms` (or the `Retry-After` header — they agree). Wait that long.
3. Retry the same call once.
4. If the retry also returns `KAIRIX_COLD_START`, surface "kairix is still warming after ~Ns" to the user. Do NOT substitute a memory-based answer — `KAIRIX_COLD_START` is a wait-and-retry state, never a permanent failure.

The reference SDK behaviour and the parsing recipe for stdio / SSE clients live in [`docs/operations/MCP-DEPLOYMENT.md`](../MCP-DEPLOYMENT.md) under "Cold-start affordance contract".

**Operator side.** A single cold-start envelope per process boot is normal. Investigate when:

- **Multiple cold-start envelopes per hour** — implies the kairix container is restarting more than once per process lifetime. Check `docker logs app-kairix-1 | grep event=mcp_process_started` to count restarts; sustained restart cadence usually means an OOM or a healthcheck flapping. See [worker-memory-and-swap.md](worker-memory-and-swap.md) for the OOM case.
- **Envelope persists past 30 seconds** — warm-up is failing, not slow. Check `docker logs app-kairix-1 | grep event=mcp_warm_failed` for the underlying step (`build_search_pipeline` → SQLite path/schema issue; `probe_search` → vector index missing). Triage path lives in [`docs/operations/MCP-DEPLOYMENT.md`](../MCP-DEPLOYMENT.md) under "Cold-start affordance contract — Layer 3 — structured startup logs".
- **`Retry-After` header missing from the 503** — the middleware regressed. Run the soak test below; this is exactly what it pins.

## How to reproduce locally

The drill recipe is reproducible against any kairix HTTP deployment. Substitute `your-vm.example.com` and the deployment's HTTP port (default `8080`; the 2026-06-06 drill VM used a `KAIRIX_HOST_PORT=8090` override) for your environment.

```bash
# 1. SSH to the host running the kairix container.
ssh your-vm.example.com

# 2. Pick a writable scratch dir for the captured artefacts.
outdir="/tmp/cold_start_drill_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$outdir"

# 3. Capture a warm baseline so you can compare timings later.
curl -sS -o /dev/null -w 'warm: HTTP %{http_code} time %{time_total}s\n' \
  http://127.0.0.1:8080/mcp | tee "$outdir/log.txt"

# 4. Trigger the restart. The container takes 5-10s to re-bind the port.
docker compose restart kairix

# 5. Poll the endpoint until the first responsive call (HTTP != 000).
#    The first responsive call should be HTTP 503 with the cold envelope.
for i in $(seq 1 10); do
  status=$(curl -sS -o "$outdir/attempt_${i}_body.json" \
    -D "$outdir/attempt_${i}_headers.txt" \
    -w '%{http_code}' http://127.0.0.1:8080/mcp || echo 000)
  printf '  attempt %d: HTTP %s\n' "$i" "$status" | tee -a "$outdir/log.txt"
  [ "$status" != "000" ] && break
  sleep 1
done

# 6. Inspect the captured envelope. Compare to the Reference envelope above.
cat "$outdir/attempt_${i}_headers.txt"
cat "$outdir/attempt_${i}_body.json" | python3 -m json.tool
```

Expected: the captured `attempt_N_body.json` matches the Reference envelope above field-for-field (the `tool` value will be `/mcp`, the `retry_after_ms` will be `8000`).

## Soak-test enforcement

The envelope shape is locked in place by [`tests/soak/test_cold_start_envelope_visible_on_restart.py`](../../../tests/soak/test_cold_start_envelope_visible_on_restart.py). The test boots a real uvicorn server in-thread, binds an ephemeral port, hits `/mcp` over the loopback network, and asserts every field of the envelope plus the `Retry-After` header. It also runs the `cold_start_recovery` journey: cold-call → parse envelope → wait `retry_after_ms` → retry → assert HTTP 200 from the real handler.

The soak runs nightly on `main` in [`soak-suite.yml`](../../../.github/workflows/soak-suite.yml). If any field drifts (renamed, dropped, type changed) or the `Retry-After` header goes missing, the nightly soak fails before the regression reaches operator-facing deployments.

To run the test on demand against your branch:

```bash
python3 -m pytest tests/soak/test_cold_start_envelope_visible_on_restart.py -m soak -v
```

Or trigger the full soak suite via GitHub Actions:

```bash
gh workflow run soak-suite.yml
```
