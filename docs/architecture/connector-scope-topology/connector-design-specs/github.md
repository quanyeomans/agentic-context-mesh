# GitHub connector — design spec (Wave E)

> Per-connector design spec across the five operating dimensions: **functions/actions,
> observability, agent affordance, failure modes / proactive resolution, performance.**
> Source-side facts from `../01-source-analysis.md §GitHub`; capability model from `../ADR.md`;
> performance from `../05-non-functionals.md`. GitHub's distinctive proactive cases —
> **installation-token rotation (1 h TTL)** and **secondary/abuse rate limits** — extend the
> Slack-derived `proactive-failure-modes.md`.

## 0. Greenfield → target

**No current implementation.** Not in `kairix/connectors/`, not registered. Sanctioned
change-detection lib (**F37**): `dulwich` (pure-Python git — clone / tree-walk fallback for
monorepos). REST + GraphQL go through `httpx` (a general dep, not F37-restricted). No
cross-connector / extractor imports (F35).

```mermaid
flowchart LR
    subgraph creds["Credential (OAuthConnector)"]
        JWT["App JWT (signed by private key)"] -->|POST /app/installations/{id}/access_tokens| TOK["installation token<br/>TTL 1h ⏱"]
        TOK -->|rotate at T-50%| TOK
    end
    subgraph streams["Per-repo streams"]
        CODE["code: compare refs / Git Trees<br/>(dulwich clone fallback >100k)"] --> CE[ChangeEvent]
        ISS["issues/PRs: since= / GraphQL"] --> CE
        REL["releases / discussions / wiki"] --> CE
    end
    TOK --> streams
    WH["webhooks: push / issues / pull_request / …"] --> CE
```

---

## 1. Identity, capabilities, containers

**`kind`**: `github`. **Credential boundary**: one **App installation** (one org or selected
repos; token exchanged from a JWT signed by the App's private key, **1 h TTL**) OR one PAT
(fine-grained / classic, user-bound). `cc_pair` = (connector, one credential, one
installation/scope). One App → many independent installations (`../01 §GitHub`).

**Container model.** A **Container = one repository**. Each repo carries multiple item streams
(code tree, issues, PRs, discussions, releases, wiki) — the cursor is per-repo-per-stream
(commit SHA for code; `since=` timestamp for issues/PRs). `iter_containers` =
`GET /installation/repositories` (App) or user-repo enumeration (PAT).

**Hierarchy.** `Org → repo → branch/ref → tree(dir) → file` (`../01`). `load_hierarchy` emits
Org / repo / directory `HierarchyNode`s (dirs via Git Trees `recursive=1`). **F58**:
org → repo → dir ordering.

**AccessType** (`../ADR.md`). `AccessType.SYNC` for private repos (org membership + repo
visibility + branch protection define access; `SlimConnectorWithPermSync` mirrors
collaborators/teams). Public repo → `AccessType.PUBLIC` + `public` tier. F39: public→`public`,
internal (GHEC)→`internal`, private→`client-confidential` (`../01 §GitHub`).

**Capability declaration (target).**

| Capability | Implements? | Why |
|---|---|---|
| `SourceConnector` (base) | ✅ | enumerate repos / fetch blob+issue / link / sensitivity |
| `PollConnector` | ✅ | ref-compare for code, `issues?since=` for tickets |
| `CheckpointedConnector` | ✅ | commit SHA + `since=` cursor per stream |
| `EventConnector` | ✅ | org/repo webhooks (`push`, `issues`, `pull_request`, `discussion`, `release`, `repository`, `delete`, `installation_repositories`) |
| `SlimConnector` | ✅ | tree SHAs + issue numbers (cheap prune enumeration) |
| `SlimConnectorWithPermSync` | ✅ | collaborators + teams ACL |
| `Resolver` | ✅ | replay failed blob/issue fetches; **force-push full-container refresh** |
| `HierarchyConnector` | ✅ | Org → repo → dir |
| `OAuthConnector` | ✅ | App JWT→installation-token exchange + OAuth App user flow |

---

## 2. Functions / actions

F42 returns. Chunk writes carry F39 + `chunker_version` (**F55**; code → `TreeSitterChunker`
per `(kind=code, mime)`, issues/PRs → `PerTicketChunker`, `../08`).

| Action | Signature | GitHub API | Notes |
|---|---|---|---|
| Enumerate containers | `iter_containers()` | `GET /installation/repositories` | one Container/repo |
| Hierarchy | `load_hierarchy(cc_pair)` | `GET /repos/{o}/{r}/git/trees/{sha}?recursive=1` | org→repo→dir; truncates >100k → `dulwich` clone |
| Poll code | `list_changes(container)` | `GET /repos/{o}/{r}/commits?since=`, ref compare | keyed on commit SHA |
| Poll tickets | (same) | `GET …/issues?since=`, `pulls`, GraphQL `discussions` | `since=` cursor |
| Checkpoint resume | `load_from_checkpoint(container, ckpt)` | SHA / `since=` | per-stream |
| Fetch body | `fetch(item_id) -> RawArtefact` | `GET …/contents/{path}` (base64 ≤1 MB) or Git Blobs API; issue/PR JSON | LFS (>100 MB) separate auth |
| Source link | `source_link(item_id)` | `html_url` | |
| Sensitivity | `sensitivity_for(item_id)` | repo `visibility` | public/internal/private → tier |
| Slim | `retrieve_all_slim_docs(…)` | tree SHAs + issue numbers | prune |
| Slim + perms | `retrieve_all_slim_docs_with_perms(…)` | `GET …/collaborators` + teams | ACL |
| Failure replay | `reindex(failures, *, include_permissions=False)` | per-blob/issue re-fetch | `Resolver` |
| Subscribe | `subscribe(callback_url)` | `POST /orgs/{o}/hooks` or `/repos/{o}/{r}/hooks` | validate `X-Hub-Signature-256` HMAC |
| Handle event | `handle_event(event)` | `push` / `issues` / `pull_request` / … | dedup on delivery id |
| **Rotate token** | (credential provider) | `POST /app/installations/{id}/access_tokens` | **1 h TTL — first-class rotation** |

**`ChangeEvent.op` mapping**: new file/issue → `CREATED`; edit → `MODIFIED`; **force-push** →
`MODIFIED` + full-container reconcile (history rewritten; ingest keyed on commit SHA must
re-derive, Break #7); repo archived → `ARCHIVED`; repo/installation access removed
(`installation_repositories` removed) → `ACCESS_LOST`; ref/file delete → `DELETED`.

---

## 3. Observability

**Counters**: `blobs_fetched`, `issues_fetched`, `prs_fetched`, `tree_walks`,
`force_push_reconciles`, `rest_requests`, `graphql_points_consumed`,
`rest_403_secondary_total` (abuse/secondary limit — distinct from primary),
`installation_token_rotations`, `collaborators_synced`.

**Gauges**: `rest_rate_remaining` (of 5000/h), `rest_rate_reset_in_seconds`,
`graphql_points_remaining` (of 5000/h), `installation_token_expires_in_seconds` (**counts down
from 3600 — the rotation trigger**), `freshness_age_seconds{repo}`.

**Lifecycle events**: `installation_token_rotated`, `force_push_detected{repo, ref}`,
`secondary_rate_limit_hit{endpoint}`, `repo_archived`, `installation_repositories_changed`,
`webhook_ping_received`, `lfs_object_skipped{path}`, `tree_truncated_fallback_to_clone{repo}`,
`backfill_started` / `_completed`.

**Structured-log field set**: `cc_pair_id`, `container_id` (=repo full_name),
`x-github-request-id` (invaluable on a support ticket), `x-ratelimit-remaining`,
`x-ratelimit-reset`, `retry_after`, `delivery_id` (webhook idempotency).

**Surfaces**: `ResultEnvelope` freshness per repo; `tool_worker_status` rollup;
`connector status` (§4). `installation_token_expires_in_seconds` is the proactive-rotation
signal; `rest_rate_remaining` is the backoff signal.

---

## 4. Agent affordance

MCP + CLI parity (F53); F30 + F45 per new surface.

**Status reads**:

| Agent need | MCP tool | CLI verb | Envelope |
|---|---|---|---|
| Is GitHub current + within budget? | `tool_connector_status("github")` | `kairix connector status github` | `{cc_pair, token_expires_in, rest_rate_remaining, repos:[{full_name, state, age_seconds}], dead_letter_count}` |
| Why did this blob/issue fail? | `tool_connector_deadletters("github")` | `kairix connector deadletters github` | `[{item_id, failure_kind, failure_message, last_attempt}]` |
| Capability set | `tool_connector_capabilities("github")` | `kairix connector capabilities github` | `{capabilities:[…], access_type, repo_count}` |

**Triggerable actions**:

| Agent need | MCP tool | CLI verb | Effect |
|---|---|---|---|
| Force repo re-sync | `tool_connector_resync("github", repo?)` | `kairix connector resync github [--repo O/R]` | reset SHA/`since=` cursor (rate-limit aware) |
| Replay failures | `tool_connector_reindex("github")` | `kairix connector reindex github` | `Resolver.reindex(failures)` |
| Reconcile after force-push | `tool_connector_reconcile("github", repo)` | `kairix connector reconcile github --repo O/R` | full-container hierarchy refresh (Break #7) |
| Rotate credential | (operator) | `kairix cc-pair rotate-credential <id>` | reuses `cc-pair` CLI |

---

## 5. Failure modes & proactive resolution

GitHub **inherits the generic patterns from `proactive-failure-modes.md`** (subscription/webhook
lifecycle, rate-limit token-bucket, credential rotation under cc_pair lock,
`ContainerAccessDenied` semantics). The rows below are the **GitHub-specific instantiations and
additions**:

| Failure | Detection | **Proactive behaviour** | Template ref |
|---|---|---|---|
| Installation token expiry (1 h) | `installation_token_expires_in_seconds` < threshold | **rotate at T-50% under per-cc_pair lock** (Break #13, Onyx `OnyxDBCredentialsProvider`); never let a long backfill outrun the token | §"Credential rotation" |
| Secondary/abuse rate limit | `403` + `Retry-After` on bursty parallelism | **drop to sequential per-installation**; token bucket distinct from primary; back off per `Retry-After` | §"Rate-limit token-buckets" (GitHub-specific: two buckets — primary 5000/h + secondary) |
| Primary rate exhausted | `x-ratelimit-remaining = 0` | **wait until `x-ratelimit-reset`**; surface `rest_rate_remaining` gauge | §"Rate-limit token-buckets" |
| Force-push history rewrite | `push` event `forced=true` / SHA mismatch | **full-container reconcile** via `Resolver` + hierarchy refresh; re-key on new SHAs | GitHub-specific (not in template) |
| Repo archived | `repository` event `archived=true` | no webhooks → **poll-only or accept staleness**; mark container | §"webhook lifecycle" (degrade to poll) |
| Installation repos removed | `installation_repositories` removed | `ContainerAccessDenied` per repo; cc_pair alive | §"ContainerAccessDenied" |
| Tree > 100k entries | Git Trees `truncated=true` | **fall back to `dulwich` clone** + local walk | GitHub-specific |
| LFS object (>100 MB) | LFS pointer file | **skip with `lfs_object_skipped` event** (separate auth, deferred) | GitHub-specific |
| Webhook signature invalid | `X-Hub-Signature-256` HMAC mismatch | **reject** (security); log `webhook_signature_rejected` | §"webhook lifecycle" |

**Token-rotation timeline** (the credential-rotation stress case the template generalises):

```
  t=0   JWT → installation token (TTL 3600s)
  t=1800  expires_in crosses 50% ──→ rotate under cc_pair lock ──→ new token
          (in-flight requests drain on old token; new requests use new token)
  backfill of 100k items (~5h) spans ~10 rotations — each silent, no cc_pair interruption
  rotation fails (private key revoked) ──→ CredentialExpiredError ──→ cc_pair INVALID (F57)
```

---

## 6. Performance

Linked to `../05-non-functionals.md` (GitHub row):

- **Storage** (`../05 §Storage`): source file ~58 KB, issue/PR ~29 KB. 50 repos / ~10k files +
  5k issues ≈ **750 MB**.
- **Rate** (`../05 §Rate-limit`): PAT 5000/h REST + 5000 pts/h GraphQL; App 5000–15000/h scaling
  with seats. **Secondary limits punish bursty parallelism → sequential per-installation
  safer** (`../01`). **Concurrency cap default = 4 repos**.
- **Backfill** (`../05 §Initial-backfill`): 10k ≈ 30 min, 100k ≈ 5 h.
- **Conversion**: source files ~5 ms (or clone overhead amortised, `../05`). Tree-walk via Git
  Trees; clone fallback for monorepos.

---

## 7. Capability declaration (target code shape)

```python
class GitHubConnector(                           # kairix/connectors/github/connector.py
    SourceConnector, PollConnector, CheckpointedConnector,
    EventConnector, SlimConnector, SlimConnectorWithPermSync,
    Resolver, HierarchyConnector, OAuthConnector,
):
    kind = "github"
    # OAuthConnector covers BOTH the App JWT→installation-token exchange
    # (the 1h-TTL rotation surface) and the OAuth App user flow.
```

`dulwich` confined to this plugin per F37; REST/GraphQL via `httpx`.

---

## 8. F-rule & test obligations

- **F37** — `dulwich` only under `kairix/connectors/github/`.
- **F39** — chunk writes carry sensitivity (repo-visibility-derived).
- **F42** — frozen-dc returns (`Container`, `HierarchyNode`, `Subscription`, slim docs).
- **F55** — code → `TreeSitterChunker`, issues/PRs → `PerTicketChunker` (`../08`), both carrying
  `chunker_version`.
- **F56** — capability declaration + inventory contract test.
- **F58** — `load_hierarchy` parent-before-child (org→repo→dir).
- **F45 / F36 / F43** — new MCP tools + CLI verbs + `connector_github.feature` +
  `e2e_connector_sync.feature` row + `tests/contracts/test_github_protocol.py` (fake + real via
  `httpx` mock transport).
- **F54** — webhook-vs-poll, perm-sync, GraphQL-vs-REST each behind a flag with both-branch
  tests.

---

## 9. Open decisions

1. **GraphQL vs REST default** — GraphQL is point-budgeted (5000 pts/h) and better for
   issue/PR/discussion threads; REST for blobs. Default per stream?
2. **Wikis** — separate git repo (`../01`); in or out of Wave E?
3. **Actions logs / packages / projects** — out of scope for Wave E? (likely yes)
4. **Monorepo clone threshold** — tree-truncation fallback at 100k entries; pin the
   `dulwich`-clone size ceiling.
5. **PAT classic coarse scope** (`repo` = all-or-nothing) — discourage in favour of
   fine-grained PAT / App?
