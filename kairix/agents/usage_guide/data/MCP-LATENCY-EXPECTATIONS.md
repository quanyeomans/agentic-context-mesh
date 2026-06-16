# MCP latency expectations

Agents calling kairix over MCP see different response times depending on
whether the server has done the same work recently. The first call after
startup is slow because the server has to load models and warm caches;
later calls are fast because those caches are already populated. This
page tells you how long each tool usually takes, how to pick a client
timeout, and how to plan a task window when you need to make a lot of
calls.

## Per-tool latency table

The numbers below are observed in production. `p50` means half the
calls finish at or under that time; `p99` means 99% of calls finish at
or under that time. "Warm" means the server has answered a similar
call in the last few minutes. "Cold" means it has not.

| Tool | p50 warm | p99 warm | p50 cold | p99 cold | Recommended client timeout | Recommended task budget |
|---|---|---|---|---|---|---|
| search | 0.5s | 3s | 5s | 15s | 30s | 1x |
| entity | 1s | 5s | 3s | 10s | 30s | 1x |
| timeline | 3s | 15s | 10s | 30s | 60s | 2x |
| bootstrap | 1s | 5s | 3s | 10s | 30s | 1x |
| usage_guide | 0.2s | 1s | 0.5s | 2s | 10s | 1x |
| brief | 5s | 15s | 15s | 45s | 90s | 3x |
| prep | 3s | 10s | 10s | 30s | 60s | 3x |
| contradict | 10s | 30s | 30s | 90s | 120s | 4x |

The "task budget" column is a multiplier on the p99 cold figure — see
the planning section below for how to use it.

## ColdStart response handling

When the server is still warming up, it answers with HTTP 503 and a
JSON body that looks like this:

```json
{
  "error": "ColdStart",
  "retry_after_seconds": 8,
  "message": "kairix is still loading models; try again shortly"
}
```

`retry_after_seconds` tells you how long the server expects to take
before it can answer your call. Back off for at least that long and
retry the same call **once**. If the second attempt also returns
ColdStart, surface the message to the user and stop — repeatedly
hammering the endpoint will not make it warm up faster.

The ColdStart envelope is defined in `kairix/agents/mcp/cold_start.py`.

## Error response handling

When a tool call fails for any reason other than ColdStart, the
response carries an `error` field:

```json
{
  "error": "ValueError: unknown collection 'foo'",
  "results": []
}
```

If `error` is set, the work did **not** succeed. Do not retry. Pass
the error message back to the user along with what you were trying
to do, so the user can decide what to fix. Retrying an error response
risks running the same bad call again and burning more time.

## Planning a task window for memory-heavy work

If your task makes many calls to the slower tools (`brief`, `prep`,
`contradict`), pick a task window that is at least **2x the sum of the
expected p99 cold latencies** for everything you plan to do. The cold
figures are the right ones to plan against because a single slow call
can show up anywhere in your sequence — caches can drop entries between
calls.

### Worked example

You plan to brief 6 agents and run 3 prep queries.

* 6 briefs at p99 cold = 6 x 15s = 90s
* 3 prep queries at p99 cold = 3 x 10s = 30s
* Sum = 120s
* Task window = 2 x 120s = **240 seconds minimum**

That is the floor. If your environment is shared with other heavy
callers, double it again.

### Quick rule of thumb

If you do not want to do the arithmetic:

* All-fast tools (search / entity / bootstrap / usage_guide):
  budget 60 seconds per call.
* Mixed (includes timeline or prep): budget 120 seconds per call.
* Heavy (brief or contradict): budget 240 seconds per call.

Multiply by the number of calls you plan to make, then add 30 seconds
of headroom for the first cold call.
