# Linear connector

First-party connector that brings your Linear workspace's roadmap and
docs into the knowledge store — initiatives, projects, issues, plus
standalone documents and project status updates. One connector instance
covers one Linear workspace.

## What it does

Each sync tick:

1. Polls five Linear entity types — issues, projects, documents,
   initiatives, and project updates — over the Linear GraphQL API,
   filtered and ordered by `updatedAt` so only items changed since the
   last run come back.
2. Emits one change event per item, each with a type-prefixed id
   (`issue:ENG-42`, `project:<uuid>`, `document:<uuid>`,
   `initiative:<uuid>`, `projectUpdate:<uuid>`) so the rest of the
   pipeline can tell entity types apart without a second lookup.
3. Renders each item to Markdown — a title heading, a short field block
   (state, assignee, team, project, labels, link), and the body (Linear
   content is already Markdown).
4. Surfaces envelope metadata (author, created/updated dates, labels,
   state, team, project, link) so the search layer can boost recency,
   surface labels as tags, and attribute items to their author.

All traffic is HTTPS-only. The endpoint is hard-coded to
`https://api.linear.app/graphql` and the client refuses any non-HTTPS
address.

## What it requires

- A Linear workspace.
- A **Linear API key** (a personal or workspace key). Create one in
  Linear under Settings → Account → Security & access → Personal API
  keys, or Settings → API for a workspace key. The key is a bearer
  credential — keep it in your secret store, never in a file the app
  can't write.
- No extra Python packages. The connector talks to Linear over plain
  HTTPS using the HTTP client kairix already ships — there is no
  third-party Linear SDK dependency (see `DEPENDENCIES.md`).

## How operators enable it

Put the API key in your secret store under the canonical name the
connector resolves: scope `connector`, area `linear`, leaf `api_key`.
For env-var deployments that means
`KAIRIX_CONNECTOR_LINEAR_API_KEY`; for a Key Vault mount it is the
matching `connector-linear-api-key` secret.

Then flip the feature flag and add the connector to your
`kairix.config.yaml`:

```yaml
features:
  connector_linear: true

topology:
  connectors:
    - id: linear-prod
      kind: linear
      name: "Linear workspace"
      default_sensitivity: internal        # roadmap/docs are company-internal; override per-deploy
      refresh_freq_seconds: 900            # how often to poll, in seconds (900 = every 15 min)
  credentials:
    - id: linear-cred
      kind: bearer_token                   # credential type (the API key is a bearer token)
      secret_name: connector-linear-api-key   # secret store name (without the kairix- prefix)
      admin_public: true                   # every agent may search this source
  cc_pairs:
    - id: cc-linear
      connector: linear-prod
      credential: linear-cred
      name: "Linear workspace pair"        # required
      access_type: PUBLIC                  # every agent can search the workspace
  collections:
    - name: linear
      sources:
        - cc_pair: cc-linear
          path_filter: "*"                 # everything the connector returns
```

The feature flag defaults OFF, so adding this connector is a no-op until
you deliberately turn it on.

## How fresh is it

The connector finds changes by polling, not by webhooks. That means new
edits show up on the next poll — within your poll interval (15 minutes
at the `refresh_freq_seconds: 900` shown above). Roadmap and
docs change on a human cadence, so this is plenty fresh for questions
like "what's our roadmap?" or "what does this design doc say?". Polling
also needs only outbound HTTPS, which keeps it friendly to locked-down
deployments — no public callback for Linear to reach. Sub-minute
freshness via webhooks is a planned future option for teams that need it
and can expose a callback. See the design spec §13 for the full
reasoning.

## What it does NOT do (yet)

- It does **not** use webhooks for real-time updates (poll-only for now).
- It does **not** support per-team or per-project sensitivity overrides
  — every item gets the connector's `default_sensitivity`.
- It does **not** ingest comments, cycles, or milestones as their own
  items. Milestone and member-project names are folded into the parent
  project/initiative Markdown.
- It does **not** span more than one workspace per API key — one key,
  one workspace.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Rate-limit pauses | Linear returned HTTP 429 | The client reads `Retry-After`, backs off, and retries automatically (bounded). No operator action needed. |
| HTTP 401 on every call | API key revoked or wrong | Generate a fresh Linear API key and update the `connector-linear-api-key` secret. |
| One item missing, sync continues | A single malformed item | Logged and skipped; the rest of the tick still lands (per-item isolation). |
| Endpoint error at startup | A non-HTTPS endpoint override | Remove the override; the connector only talks to `https://api.linear.app/graphql`. |

## References

- `kairix/connectors/linear/connector.py` — the source-of-truth for this
  connector's behaviour.
- `tests/bdd/features/connector_linear.feature` — the behavioural
  contract under test.
- `docs/architecture/connector-scope-topology/connector-design-specs/linear.md`
  — the full design spec (capabilities, queries, cursor model,
  poll-over-webhooks decision).
