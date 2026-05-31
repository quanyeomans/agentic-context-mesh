# ADR-031 — Canonical credential naming + SecretsLoader

## Status

Accepted. Implementation lands alongside this ADR. Legacy aliases stay
in place behind a deprecation warning until the next stable release
after a successful dual-write soak.

## Context

Every kairix connector and provider grew an ad-hoc env-var convention
during its first wave (`KAIRIX_M365_TENANT_ID`,
`APPLE_CALDAV_USERNAME_ALPHA`, `GITHUB_KAIRIX_PAT`, …). Operators
ended up maintaining a separate list per connector — and per KV
provider (Azure KV, AWS Secrets Manager, GCP Secret Manager,
1Password, LastPass). Three problems compounded:

1. **No single rule the operator can apply** when adding a new
   connector. The operator had to read each connector's README to
   discover the env-var name + KV name pair it expected.
2. **No deterministic mapping** from a connector's identity (which
   plugin, which instance, which leaf) to the secret name. Adding a
   second instance of a connector (per-vault Obsidian, per-workspace
   Slack) required ad-hoc disambiguation.
3. **No way to introspect a deployment** before the first ingest — a
   missing secret surfaced as the first connector that failed to
   construct, not as a holistic preflight.

## Decision

### Schema — derive every name from identity

Every kairix credential identity is a 4-tuple ``(scope, area,
instance, leaf)``:

* `scope` is one of `connector`, `provider`, `infra`.
* `area` is the connector/provider/infra component name as it appears
  in the codebase (`sharepoint`, `m365`, `github`, `azure-openai`,
  `embed`, `llm`, `neo4j`). Python-style underscores in module names
  become hyphens in the canonical name (`apple_caldav` →
  `apple-caldav`).
* `instance` is the optional disambiguator when multiple instances of
  the same area exist (per-vault Obsidian `tcv`, per-agent Slack
  workspace `alpha`/`builder`/`coach`, multi-tenant GitHub PAT
  `kairix`/`openclaw`). Singleton areas (only one M365 tenant per
  deployment) leave instance as `None`.
* `leaf` is the specific credential slot (`tenant-id`, `client-secret`,
  `pat`, `api-key`, `encryption-password`).

Two derivations, deterministic, pure-function:

```
KV secret name:  kairix-<scope>-<area>[-<instance>]-<leaf>
Env var name:    KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>
```

The env-var form is just the KV form uppercased with hyphens swapped
for underscores. Operators learn ONE rule.

### Parser tie-break

`parse_canonical_name` cannot tell
``kairix-connector-sharepoint-tenant-id`` (instance=`None`,
leaf=`tenant-id`) apart from
``kairix-connector-sharepoint-tenant`` with instance=`tenant`,
leaf=`id`. The chosen tie-break: **leaf is the last hyphen-separated
token; everything between the area and the last token is the
instance**.

This is the simplest defensible rule. Multi-slot leaves (`tenant-id`,
`client-secret`) parse back partially split, which is acceptable
because actual resolution flows through
`LEGACY_ALIASES` (which stores the well-formed identity tuple
directly). The parser exists for inspection tooling
(`kairix secrets verify`, future operator scripts) — not for the hot
path.

### SecretsLoader

`kairix.secrets.SecretsLoader` is the canonical resolver. Resolution
order (first hit wins):

1. **Canonical env-var** — `os.environ.get(canonical_env_var(...))`.
2. **Legacy alias** — every entry in `LEGACY_ALIASES`. A hit returns
   the value and emits `DeprecationWarning` naming the alias + the
   canonical replacement.
3. **KV-backed file mount** — `/run/kairix/secrets/<canonical-name>`,
   the CSI-driver mount path.
4. **Legacy chain** — falls through to the historical
   `kairix.secrets._legacy.get_secret` chain (per-file secrets,
   bundle file, `az keyvault secret show`).

The loader is a **constructor seam**, never a module-level singleton.
Connectors take a `secrets: SecretsResolver` kwarg and tests pass
`FakeSecretsLoader(values={...})` from `tests/fakes.py` — never
`monkeypatch.setenv("KAIRIX_*")` (F2).

`SecretsLoader.require(...)` raises `SecretNotFoundError` with a
message naming the canonical KV name AND the canonical env-var name —
the operator can paste either straight into their tooling.

### Migration strategy — dual-write

Legacy env-var names stay registered in
`kairix.secrets._legacy_aliases.LEGACY_ALIASES`. A hit emits a single
`DeprecationWarning` per call site naming the alias + canonical
replacement. The operator runs `kairix secrets migrate-list` to dump
the full legacy → canonical mapping as TSV, pipes that into
`az keyvault secret set` (or equivalent), and on the next release
the alias entry is removed from the map.

No code change is required at the connector site to start using the
canonical name — the loader walks all sources in priority order. The
operator decides when to cut over per credential.

### Operator surface — `kairix secrets`

Two subcommands, both with a `--json` envelope variant:

* `kairix secrets verify` — walks every registered alias entry and
  asks `SecretsLoader` to resolve. Renders a per-row status
  (`present` / `present-via-legacy` / `MISSING`) + the canonical KV
  name. Exits non-zero if any required secret is missing — suitable
  for a pre-deploy gate.
* `kairix secrets migrate-list` — emits the TSV mapping of every
  registered legacy env-var to its canonical KV name. Pipes cleanly
  into bulk-provisioning loops.

CLI ↔ MCP parity (per `cli-mcp-feature-parity`) tracked separately;
the MCP tool is not in scope for this ADR but is a follow-up under
the same canonical-naming surface.

## Consequences

### Positive

* **Single rule across providers.** Operators learn one mapping and
  apply it to every connector / provider / infra credential. New
  connectors plug into the same convention without code review of
  their secret-name conventions.
* **Operator-facing preflight.** `kairix secrets verify` is a single
  command that confirms every credential resolves before the first
  ingest tick. Currently the operator finds out about a missing
  credential when a connector's first batch fails.
* **No central registry burden.** A connector's secret needs are
  whatever `loader.require(...)` calls it makes in its constructor.
  Adding a new connector does not require touching a central manifest.
* **Multi-instance support.** The instance segment is part of the
  schema. Per-vault Obsidian / per-agent Slack / per-tenant GitHub
  configurations slot in without inventing new conventions.

### Negative

* **Initial alias-map maintenance.** `LEGACY_ALIASES` is hand-curated.
  Every secret consumed by the current connector / provider set has a
  row; the row is removed when the corresponding KV secret is
  promoted to the canonical name. The orchestrator owns the
  per-row removal cadence; failing to remove a row leaves the
  deprecation warning firing forever.
* **Parser ambiguity for multi-slot leaves.** The tie-break (leaf is
  the last token) means `kairix-connector-sharepoint-tenant-id`
  parses as `(connector, sharepoint, tenant, id)` not
  `(connector, sharepoint, None, tenant-id)`. Inspection tooling
  treating the parsed tuple as ground truth without round-tripping
  through `LEGACY_ALIASES` may render incorrect identities.
  Documented in the module docstring + this ADR.

## Alternatives considered

### Per-connector secret schema

Reject. The current state. Every connector author invents conventions
that operators carry. The whole motivation for this ADR is to remove
that burden.

### Centralised registry of secret slots

Reject. Adds a single global file every new connector must edit —
exactly the kind of central choke point the plug-in architecture was
designed to avoid. The canonical naming scheme makes the registry
unnecessary: the connector's constructor calls are the registration.

### One-shot rename, no dual-write

Reject. Forces every operator to migrate their KV before the next
deploy. The dual-write + DeprecationWarning pattern is the standard
kairix cutover protocol (see `feature-flag-architecture.md`); this
ADR composes that pattern with the existing legacy resolver chain so
nothing breaks on the upgrade.

## Implementation notes

* `kairix/secrets/` is a package containing `naming.py`, `loader.py`,
  `_legacy_aliases.py`, `cli.py`, and `_legacy.py` (the historical
  resolver, preserved verbatim). The package `__init__.py` re-exports
  the historical surface so existing callers
  (`from kairix.secrets import get_secret`) keep working.
* F4 / F15 / F76 check scripts updated to recognise
  `kairix/secrets/**` as the boundary (previously hardcoded
  `kairix/secrets.py`).
* `FakeSecretsLoader` lives in `tests/fakes.py` and is the canonical
  test double. Existing tests that used `monkeypatch.setenv("KAIRIX_*")`
  are not migrated by this ADR — F2 is unchanged and the historical
  tests are grandfathered. New tests use the fake.

## Test coverage summary

* `tests/unit/test_secrets_naming.py` — 18 tests for
  `canonical_secret_name` / `canonical_env_var` / `parse_canonical_name`.
* `tests/unit/test_secrets_loader.py` — 13 tests for the resolution
  chain (env / legacy alias / KV mount / chain / miss).
* `tests/unit/test_secrets_cli.py` — 7 outcome tests for
  `verify` / `migrate-list`.
* `tests/bdd/features/secrets_cli.feature` + steps — 3 scenarios
  (all-present, one-missing, migrate-list-TSV).
* `tests/contracts/test_secrets_protocol.py` — 10 parametrised
  contract tests proving real + fake satisfy the Protocol.
