# How To: Set Up the Linear Connector

**Purpose:** Bring your Linear workspace's roadmap and docs into the knowledge store — initiatives, projects, issues, standalone documents, and project status updates — so your team and your agents can answer "what's our roadmap?" or "what does this design doc say?" from search.

The Linear connector ships **turned off**. Installing or upgrading kairix changes nothing until you follow the steps below to turn it on. The cutover is four moves: store a Linear API key, add a config block, flip the `connector_linear` switch, and let the worker pull the data in.

> Docker Compose is the recommended deployment. Where a step differs for a pip / systemd install, the pip form is shown alongside.

---

## Prerequisites

- A running kairix deployment that passes `kairix onboard check` (see [how-to-upgrade-kairix](how-to-upgrade-kairix.md) if you are not green yet).
- A Linear workspace you can sign in to.
- Permission to edit your `kairix.config.yaml` and to write to your secret store (the same store that already holds your LLM key — see [secrets-configuration](../secrets-configuration.md)).
- Outbound HTTPS from the deployment to `api.linear.app`. The connector only makes outbound calls; it never needs an inbound callback, so it works behind a firewall.

One connector instance covers one Linear workspace.

---

## Step 1 — Get a Linear API key

The connector signs in to Linear with an API key. Create one in Linear:

1. Open **Settings** in Linear.
2. Go to **Account → Security & access → Personal API keys** (a key tied to your own account), or **Settings → API** for a workspace-wide key.
3. Create a new key, give it a clear label such as `kairix-knowledge-store`, and copy the value. Linear shows the key once — copy it now.

The key is a password. Keep it out of files, chat, and your shell history. The next step stores it safely.

---

## Step 2 — Store the key in the kairix secret store

The connector reads the key under one canonical name: `kairix-connector-linear-api-key`. Store it there so the connector finds it on startup.

Pipe the value in from your terminal so it never lands in shell history:

```bash
# Docker
printf '%s' '<paste-your-linear-api-key>' | docker compose exec -T kairix kairix secrets set kairix-connector-linear-api-key

# Pip
printf '%s' '<paste-your-linear-api-key>' | kairix secrets set kairix-connector-linear-api-key
```

If you keep secrets in a cloud key vault (Azure Key Vault, AWS Secrets Manager, GCP Secret Manager), add the secret there under the same name instead, and let your usual sidecar pull it in. See [secrets-configuration](../secrets-configuration.md) for the per-provider recipes.

Confirm the value resolves:

```bash
# Docker
docker compose exec kairix kairix secrets verify

# Pip
kairix secrets verify
```

The Linear key should appear as present, not missing.

---

## Step 3 — Add the connector to your config

Open your `kairix.config.yaml` and add the block below. It turns the flag on, declares the connector, points it at the key you stored, and creates one `linear` collection so the content is searchable. The key names match the shape every other connector uses in `kairix.config.example.yaml` — keep them exactly as shown.

```yaml
features:
  connector_linear: true

topology_v2:
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

Notes:

- `default_sensitivity: internal` marks the content as company-internal. Change it if your roadmap is more or less open.
- `secret_name: connector-linear-api-key` is the secret name **without** the `kairix-` prefix — it points at the `kairix-connector-linear-api-key` value you stored in Step 2.
- `name:` is **required** on the cc_pair. Leave it out and the worker fails to read the config on startup.
- `refresh_freq_seconds: 900` polls every 15 minutes. Roadmap and docs change on a human cadence, so that is plenty fresh; raise or lower it to suit.

If your `kairix.config.yaml` already has a `features:` or `topology_v2:` section, merge these keys into the existing ones rather than adding a second copy. Then check it parses:

```bash
# Docker
docker compose exec kairix kairix config validate

# Pip
kairix config validate
```

---

## Step 4 — Restart and let the worker pull the data in

The connector registers and starts polling on worker startup, so restart the worker to apply the config:

```bash
# Docker
docker compose restart kairix

# Pip — restart your `kairix worker run` process
```

On the next sync tick the worker polls Linear for everything changed since the last run (on the first run, everything), renders each item to Markdown, and feeds it into the same pipeline that makes other sources searchable. The first sync of a large workspace may take a few ticks to drain.

---

## Step 5 — Verify ingestion

First confirm the connector registered cleanly:

```bash
# Docker
docker compose exec kairix kairix onboard check

# Pip
kairix onboard check
```

Look for a passing `topology_v2_cc_pairs_registered` row — it confirms your Linear cc_pair is live. You can also list every flag and its state:

```bash
kairix features status
```

In the table, the `connector_linear` row should show **`true`** in the EFFECTIVE column.

Then confirm content is actually searchable. Pick a phrase you know is in your Linear roadmap or a Linear document and search for it:

```bash
kairix search "<a phrase from a Linear project or doc>"
```

You should get a hit whose source link points at `https://linear.app/...`. If the first search comes back empty, give the worker a tick or two to finish the initial pull and search again.

That's it — the Linear connector is live. New edits in Linear show up on the next poll.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `connector_linear` still shows `false` in `kairix features status` after editing the config | The worker hasn't re-read the config, or `connector_linear: true` sits under the wrong key | Confirm `connector_linear: true` is under the top-level `features:` block, then restart the worker (`docker compose restart kairix`, or restart your `kairix worker run` process). Flags apply on startup. |
| `topology_v2_cc_pairs_registered` fails in `onboard check`, or `kairix config validate` errors | The config didn't parse, or the worker hasn't applied the cc_pair yet | Run `kairix config validate`: a missing `name:` on the cc_pair, `cc_pair_id` instead of `cc_pair` in a collection source, or `credential_ref` instead of `secret_name` will each stop the config from loading. Fix, then restart the worker. |
| Auth failure — the worker logs show HTTP 401 on every Linear call | The API key is wrong, revoked, or stored under the wrong name | Generate a fresh key (Step 1), store it again under `kairix-connector-linear-api-key` (Step 2), run `kairix secrets verify`, then restart the worker. |
| Sync pauses, logs mention rate-limit or HTTP 429 | Linear is asking the connector to slow down | No action needed. The connector reads Linear's `Retry-After` hint, waits, and retries on its own. It catches up on the next ticks. |
| Nothing ingested — search finds no Linear items | The first pull hasn't finished, the key is missing, or the config didn't apply | Check, in order: `kairix secrets verify` shows the Linear key present; `kairix config validate` passes; `kairix onboard check` shows `topology_v2_cc_pairs_registered` passing; the worker has run at least one tick since the restart. Then search again. |
| One item is missing but the rest are present | A single malformed item was skipped | This is expected per-item isolation — the bad item is logged and skipped so the rest of the sync still lands. No action needed. |

---

## Related

- [secrets-configuration](../secrets-configuration.md) — how to store and rotate connector secrets across Docker / pip and every secrets manager
- [how-to-upgrade-kairix](how-to-upgrade-kairix.md) — get a healthy deployment before adding a connector
- [INDEX](INDEX.md) — full runbook registry
- `kairix/connectors/linear/README.md` — what the connector ingests + its failure modes
- `docs/architecture/connector-scope-topology/connector-design-specs/linear.md` — the full design spec (capabilities, cursor model, poll-over-webhooks decision)
