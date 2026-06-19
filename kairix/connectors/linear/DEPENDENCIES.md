# Dependency rationale

This connector introduces **no new third-party dependency**.

Linear ships a single official GraphQL API over plain HTTPS, so the
connector talks to it directly using `httpx` — the HTTP client kairix
already depends on. We deliberately avoid the Linear SDK: it adds a
heavy transitive set for what is a small POST-a-query surface, and a
raw GraphQL client keeps the import boundary tight (F35) and the
HTTPS-only guard (spec §3) under our own control.

## Current dependencies

_None beyond the existing kairix dependency set._ The connector imports
only:

- `httpx` — already a core kairix dependency (the GraphQL POST + 429
  backoff path).
- the Python standard library (`logging`, `time`, `urllib.parse`,
  `datetime`, `dataclasses`).
- `kairix.core.*` / `kairix.secrets.*` — the Protocol + secret-resolution
  surfaces.

## Adding a new dependency

1. Justify against "could `httpx` + stdlib do this?" first — for a
   single GraphQL endpoint the answer is almost always yes.
2. If a dependency is genuinely needed, add a one-line rationale here
   (`package — reason`) and confirm it does not break F35 (no
   cross-connector / extractor imports).
