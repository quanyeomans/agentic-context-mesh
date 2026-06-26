# ADR-032 — `kairix connect <service>` OAuth2 flow

**Status:** Accepted — shipped v2026.6.8
**Supersedes:** none. **Builds on:** ADR-031 (canonical credential naming + SecretsLoader)
**Owner:** platform / operator surface

## Context

kairix's Google (Gmail / Drive / Calendar), Slack, and GitHub connectors all
expect long-lived credentials in canonical KV (per ADR-031). Provisioning those
credentials today is byzantine — the operator has to manually navigate each
service's developer console, generate tokens, copy-paste into KV. Three pain
points kill operators:

1. **Google refresh tokens silently expire after 7 days** when the OAuth
   consent screen is in "Testing" mode (the default). Operators discover this
   when sync stops working a week later with no clear remediation.
2. **Slack requires creating an app + installing it + collecting 4 tokens**
   (bot-token, app-token, client-id, client-secret) from the api.slack.com UI,
   one workspace at a time.
3. **GitHub PATs are per-user and rotate manually**, with no visibility into
   which scopes are granted; the better path (GitHub App installations) is
   even more byzantine to set up by hand.

Drive + Calendar additionally have a **silent bug**: they expect a raw
`access_token` and surface a `CredentialExpiredError` to the operator on 401
— no automatic refresh. Gmail's connector got this right; Drive + Calendar
were modelled on it but the refresh closure was never ported.

## Decision

Add a `kairix connect <service>` CLI command family that automates the
OAuth2 authorization flow end-to-end:

1. Operator runs `kairix connect google-gmail --client-secret-path PATH`
   (or `kairix connect slack`, `kairix connect github-app`).
2. kairix opens a browser to the service's consent screen.
3. A localhost HTTP listener (default `127.0.0.1:8080/oauth2callback`)
   catches the OAuth callback with the authorization code.
4. kairix exchanges the code for tokens, writes them to canonical KV
   names (or `$KAIRIX_SECRETS_FILE` for local-dev), and prints a
   success summary including which canonical names were populated.

The connector code itself reads tokens via the existing
`SecretsLoader` chain — unchanged. The `connect` command only *writes*
to that surface. Connectors then refresh transparently per tick via
the official service library (Google: `google-auth`; Slack: long-lived
bot tokens, no refresh needed; GitHub App: JWT signed per call).

Backwards-compat: every connector continues to work with operator-
provisioned tokens that bypass `kairix connect`. The new CLI is an
*additional* path, not a replacement.

## Architecture

### Core abstractions — `kairix/connect/`

```
kairix/connect/
  __init__.py
  cli.py                   # `kairix connect <service>` dispatcher
  protocols.py             # OAuth2Flow, TokenStore, RefreshableToken Protocols
  listener.py              # Localhost HTTP callback listener (shared)
  store/
    file_store.py          # ~/.config/kairix/secrets/kairix.env writes
    azure_kv_store.py      # `az keyvault secret set` shell-out
    stdout_store.py        # TSV emission for UNIX pipelines
  oauth2/
    google.py              # GoogleOAuth2Flow (3 service variants share this)
    slack.py               # SlackOAuth2Flow (workspace = instance slot)
    github_app.py          # GitHubAppFlow (JWT + installation-id capture)
  refresh.py               # RefreshableToken wrapper used by connectors
```

### Protocols (the SOLID dependency-inversion boundary)

```python
# kairix/connect/protocols.py
@runtime_checkable
class OAuth2Flow(Protocol):
    """One operator-driven authorization flow.

    Lifecycle:
      1. discover_client_credentials() → reads client_id / client_secret
         from the operator-supplied source (client_secret.json file,
         CLI args, or KV — service-specific).
      2. authorize() → opens browser, captures callback, exchanges
         code for tokens. Returns CapturedTokens (frozen dc).
      3. Caller passes CapturedTokens to a TokenStore.
    """
    service_area: str            # e.g. "gmail" — feeds canonical naming
    scopes: tuple[str, ...]      # provider-specific scope strings

    def discover_client_credentials(self) -> ClientCredentials: ...
    def authorize(self, *, listener: CallbackListener) -> CapturedTokens: ...


@runtime_checkable
class CallbackListener(Protocol):
    """Localhost HTTP server that catches OAuth callbacks."""
    @property
    def redirect_uri(self) -> str: ...
    def wait_for_callback(self, timeout_s: float = 120.0) -> CallbackResult: ...
    def close(self) -> None: ...


@runtime_checkable
class TokenStore(Protocol):
    """Writes captured tokens to canonical names via the operator's
    chosen backend (file, KV, stdout)."""
    def store(self, *, scope: Scope, area: str, instance: str | None,
              tokens: CapturedTokens) -> WriteReport: ...


@runtime_checkable
class RefreshableToken(Protocol):
    """Connector-side wrapper that auto-refreshes tokens before HTTP calls.

    Connectors hold a RefreshableToken instance; their HTTP client
    calls .headers() on every request. If the token is expired,
    .headers() refreshes transparently. The connector never sees
    refresh-token specifics — just calls a method that returns
    valid auth headers.
    """
    def headers(self) -> dict[str, str]: ...
    def is_expired(self) -> bool: ...
    def refresh(self) -> None: ...
```

### Strategy pattern for per-service code

Each `OAuth2Flow` implementation lives in `kairix/connect/oauth2/<service>.py`
and is a thin wrapper around the official client library. The shared
abstractions (listener, store, refresh) are in `kairix/connect/`.

DRY surface: ~80% of the listener + store + CLI dispatch code is
shared. Per-service code is the ~20% that varies (scope strings,
authorize URL construction, token-exchange request shape, refresh
shape).

### Library choices (one per service)

| Service | Library | Why |
|---|---|---|
| Google (Gmail/Drive/Cal) | `google-auth-oauthlib` for flow, `google-auth` for refresh | Official; handles refresh transparently; works with raw httpx for the actual API calls (kairix already uses httpx) |
| Slack | `slack-sdk` (`WebClient.oauth_v2_access`) | Official; already a kairix dep; bot tokens are long-lived (no refresh) |
| GitHub App | `pyjwt[crypto]` + raw httpx | JWT-based; no per-service framework needed; installation-token exchange is a single httpx call |

`google-api-python-client` deliberately NOT used — its discovery-based
client gen is heavy and kairix already uses raw httpx for the Gmail /
Drive / Calendar API calls.

### Layering (F26 / F35 alignment)

- `kairix/connect/` imports from `kairix.core.protocols`, `kairix.secrets`, and stdlib only. No reach into `kairix/connectors/<x>/`.
- `kairix/connectors/<x>/auth.py` imports from `kairix/connect/refresh.py` (the RefreshableToken Protocol) but never from `kairix/connect/oauth2/`.
- Bidirectional independence: connectors can resolve their auth without `kairix.connect` ever being imported (legacy KV path stays open).

### Storage strategy (per ADR-031 canonical naming)

- **Default backend** (local dev): writes to `$KAIRIX_SECRETS_FILE` (default `~/.config/kairix/secrets/kairix.env`). Operator's existing pip-install secrets file.
- **`--store=azure-kv`** (vault name resolved from `$KAIRIX_KV_NAME`): writes via the Azure SDK using the operator's current identity (managed identity preferred, see below).
- **`--store=azure-kv:<vault-name>`**: writes to the explicitly-named vault (overrides `$KAIRIX_KV_NAME`).
- **`--store=azure-kv:https://<vault>.vault.azure.net/`**: writes to the explicit vault URL (covers sovereign clouds + non-default DNS suffixes).
- **`--store=stdout`**: emits TSV `<CANONICAL_ENV_VAR>\t<value>` lines for piping to anything (`tee`, `op`, custom KV import scripts).
- **`--store=aws-secretsmanager:<region>`**: future; AWS shape mirrors Azure (uses `boto3` Default Credential Provider Chain).

### Operator setup for `--store=azure-kv`

The `kairix connect` command writes to KV using the same Azure SDK
chain (`DefaultAzureCredential`) that the rest of the kairix
deployment uses for reads — but with an additional permission
requirement, because writing a secret is a higher privilege than
reading it.

**Identity options (in order of preference):**

1. **VM system-assigned managed identity** *(strongly recommended for
   production)* — no secrets on the host, automatic rotation, scoped
   to the VM. Enable on the VM via the Azure portal or:
   ```bash
   az vm identity assign --resource-group <rg> --name <vm-name>
   ```
   Then capture the principal id and assign roles (next section).

2. **VM user-assigned managed identity** *(production, shared identity
   across multiple VMs in the same engagement)* — one identity, many
   VMs. Create the identity once, attach to each VM:
   ```bash
   az identity create --resource-group <rg> --name kairix-prod-mi
   az vm identity assign --resource-group <rg> --name <vm-name> \
     --identities <managed-identity-resource-id>
   ```

3. **Service principal with client secret** *(CI / non-VM deployments)*
   — `az login --service-principal -u <app-id> -p <secret> --tenant <tenant>`
   before running `kairix connect`. Rotate the secret per your usual
   schedule.

4. **Local dev: interactive `az login`** *(laptops, one-off operator
   workstation)* — `az login` (browser flow) before running
   `kairix connect`. Lasts until the operator's Azure refresh token
   expires (typically 24h - 7d).

**Required role assignments on the Key Vault:**

The identity needs both READ and WRITE permission on secrets. The
canonical Azure RBAC role for this is **`Key Vault Secrets Officer`**
(includes `secret/set`, `secret/get`, `secret/list`, `secret/delete`).
The more restrictive `Key Vault Secrets User` (read-only) is NOT
sufficient.

Assign at the vault scope:
```bash
# Replace with your values:
KV_NAME="your-vault-name"
PRINCIPAL_ID="<the managed identity's principalId or service principal's app id>"
SUBSCRIPTION="$(az account show --query id -o tsv)"
RG="<vault's resource group>"

az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$PRINCIPAL_ID" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV_NAME"
```

For VMs already using `Key Vault Secrets User` to READ secrets via
`fetch-secrets.sh`, the operator has two choices:

- **(a) Use a separate identity for `kairix connect`** — keep the VM's
  read-only managed identity untouched; run `kairix connect` from
  a workstation with `az login` against a service principal that has
  Secrets Officer. The new tokens land in KV; the VM's next
  `fetch-secrets` cycle picks them up. **Recommended for production:
  minimum-privilege on the always-on VM.**

- **(b) Upgrade the VM's managed identity to Secrets Officer** —
  simpler but broader privilege. Acceptable for single-operator
  deployments; not for shared-team production where the read/write
  split matters.

**Endpoint specification (`KAIRIX_KV_NAME`):**

The vault is resolved in this order (first match wins):

1. Explicit CLI: `--store=azure-kv:<vault-name>` or
   `--store=azure-kv:<full-vault-url>`
2. Environment: `$KAIRIX_KV_NAME` (the same env var
   `fetch-secrets.sh` reads — keeps operator setup symmetric)
3. **No fallback.** If neither is set, the connect command fails with:
   ```
   error: --store=azure-kv requires a vault name.
   fix: set KAIRIX_KV_NAME=<your-vault>, OR
        pass --store=azure-kv:<vault-name> on the command line.
   next: az keyvault list --query "[].name" -o tsv  # to confirm available vaults
   ```

**Sovereign / non-public clouds** (Azure Government, Azure China,
private vault DNS): pass the full vault URL via
`--store=azure-kv:https://<vault>.vault.usgovcloudapi.net/` — the
Azure SDK's `SecretClient` honours the URL's hostname and routes to
the correct cloud's auth endpoint automatically.

**Verifying the setup before running `kairix connect`:**

```bash
# 1. Confirm the identity has the right role.
az role assignment list \
  --assignee "$PRINCIPAL_ID" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV_NAME" \
  --query "[].roleDefinitionName" -o tsv
# Expected: "Key Vault Secrets Officer"

# 2. Confirm read works (Secrets User permission, subset of Officer):
az keyvault secret list --vault-name "$KV_NAME" --query "[?starts_with(name,'kairix-')].name" -o tsv

# 3. Confirm write works — dry-run with a throwaway secret:
az keyvault secret set --vault-name "$KV_NAME" --name kairix-connect-test --value test
az keyvault secret delete --vault-name "$KV_NAME" --name kairix-connect-test
```

If steps 2 + 3 succeed, `kairix connect ... --store=azure-kv` will
work. If step 3 fails with "Forbidden", the role assignment is the
fix — re-check Secrets Officer is granted at the vault scope.

**Library choice:** `azure-identity` for the credential chain
(`DefaultAzureCredential` walks managed identity → env vars → Azure
CLI cached login in that order) + `azure-keyvault-secrets` for the
`SecretClient.set_secret(name, value)` call. Both are official
Microsoft libraries; both are stable.

Canonical names follow ADR-031 exactly:
- `kairix-connector-gmail-{client-id, client-secret, refresh-token, access-token}`
- `kairix-connector-google-drive-{client-id, client-secret, refresh-token, access-token}`
- `kairix-connector-google-calendar-{client-id, client-secret, refresh-token, access-token}`
- `kairix-connector-slack-{workspace}-{bot-token, app-token, client-id, client-secret}` (instance slot = workspace per #362 — this work depends on #362 landing first or extends it)
- `kairix-connector-github-{app-id, app-private-key, installation-id}` (App mode) OR `kairix-connector-github-pat` (user OAuth mode, fallback)

### Refresh handling (connector-side)

Drive + Calendar connectors get the same `_refresh_token` pattern Gmail
already has. The shared `RefreshableToken` abstraction wraps `google-auth`'s
`Credentials.refresh(Request())` call so connectors don't reimplement
the refresh dance.

Slack bot tokens never expire — Slack connector returns a static
`StaticRefreshableToken(bot_token)`.

GitHub App: each API call signs a fresh JWT (lasts 10 min), exchanges
it for an installation access token (lasts 1h, cached in memory),
uses that as Bearer. No persistent refresh state needed.

## CLI surface

```
kairix connect google-gmail --client-secret-path ~/Downloads/client_secret.json [--store=file|azure-kv:<vault>|stdout] [--port 8080]
kairix connect google-drive --client-secret-path ~/Downloads/client_secret.json [...]
kairix connect google-calendar --client-secret-path ~/Downloads/client_secret.json [...]

kairix connect slack --workspace alpha [--client-id ID --client-secret SECRET] [...]
  # If --client-id / --client-secret omitted, reads from `kairix-connector-slack-{workspace}-client-{id,secret}` in KV if present, OR prompts operator.

kairix connect github-app --app-id ID --private-key-path PATH [--instance NAME] [...]
  # Captures installation-id by listening for the GitHub App install callback.
```

Each subcommand:
1. Reads / validates client credentials from the operator-supplied source.
2. Starts the localhost listener.
3. Opens browser to the service's authorize URL with the listener's `redirect_uri`.
4. Waits for callback (timeout 120s by default; configurable).
5. Exchanges code for tokens via the service's token endpoint.
6. Writes tokens to the configured `TokenStore`.
7. Prints a summary: canonical names written, store backend used, expiry hints.

Cold-start affordance per F21: every failure surface (browser doesn't
open, port in use, consent denied, callback timeout, network error)
emits `fix:` / `next:` / `run:` markers.

## Test pyramid (per ADR-024)

### BDD (F45 — every new top-level capability has a feature file)

One feature per service variant, exercising:
- Happy path: operator runs connect → captured tokens written
- Consent denied: callback returns `error=access_denied` → CLI fails with clear remediation
- Port collision: requested port in use → next free port tried + reported
- Missing client_secret.json: clear error + path hint
- Already-connected (existing tokens present): refuse without `--force`, print existing canonical names
- Callback timeout: 120s elapsed → CLI fails with "browser may not have opened" hint

Files:
- `tests/bdd/features/cli_connect_google.feature` (covers all 3 Google variants via scenario outline)
- `tests/bdd/features/cli_connect_slack.feature`
- `tests/bdd/features/cli_connect_github_app.feature`

Step impls in `tests/bdd/steps/cli_connect_*_steps.py`.

### Contract tests (F43 + F68)

Every `OAuth2Flow` / `CallbackListener` / `TokenStore` / `RefreshableToken`
implementation must satisfy the Protocol shape AND ship a failure-injection
contract test per F68:

| Protocol | Failure injection shape |
|---|---|
| `OAuth2Flow.discover_client_credentials` | `raises FileNotFoundError` (missing client_secret.json) |
| `OAuth2Flow.authorize` | `returns_partial` (no refresh_token granted) |
| `CallbackListener.wait_for_callback` | `times_out` (operator never completes browser flow) |
| `TokenStore.store` | `unauthorized` (KV write fails) |
| `RefreshableToken.refresh` | `unavailable` (network down during refresh) |

Files: `tests/contracts/test_oauth2_flow_protocol.py`, `tests/contracts/test_token_store_protocol.py`, etc.

### Unit tests (F7 — ≥90% per file)

Per implementation file. Each test composes via canonical fakes
(`FakeBrowser`, `FakeCallbackListener`, `FakeTokenStore`,
`FakeRefreshableToken`) from `tests/fakes.py` — no `@patch`,
no `monkeypatch.setenv`. Constructor injection only.

### Integration (F47 — factory composed)

`tests/integration/test_connect_command_<service>_lifecycle.py` per
service: composes via `kairix.cli` subprocess with a fake OAuth
provider (embedded mock HTTP server), asserts the file written by
`FileTokenStore` contains the expected canonical env-var lines.

### E2E (F48 — composed production path)

`tests/e2e/test_composed_connect_google_path.py`: the full operator
journey end-to-end.
- Setup: write a fake `client_secret.json` to tmp_path
- Run: `python -m kairix.cli connect google-gmail --client-secret-path <path> --store=file`
- Mock browser: a callback request is POSTed to the listener's
  redirect_uri with the fake code
- Assert: canonical env-var lines appear in the configured secrets file;
  subsequent `kairix worker run` (mocked Gmail API) uses the captured
  refresh_token to refresh and make a Gmail API call

Same pattern for `tests/e2e/test_composed_connect_slack_path.py` and
`tests/e2e/test_composed_connect_github_app_path.py`.

Carries `@pytest.mark.e2e`, runs in CI Stage 4.5.

### Sabotage proofs (per `feedback_sabotage_must_be_executed`)

Every new test_* exercises the mutate → fail → restore cycle. Final
agent report quotes the actual failure message from each mutation.

## F-rule alignment

| Rule | Status |
|---|---|
| F1 (no internal patching) | All tests inject via constructor seams |
| F2 (no env monkeypatch) | All paths injected via dataclasses |
| F7 / F9 (per-file coverage ≥90%) | Yes |
| F17 (no duplicated string ≥3x) | Canonical name constants hoisted |
| F21 (agent-actionable error surfaces) | Every failure emits `fix:` / `next:` / `run:` |
| F25 (capability-affordance) | `connect` added to operator-only allowlist with rationale |
| F26 / F35 (layering) | `kairix/connect/` doesn't import from connectors and vice versa |
| F30 (CLI outcome tests) | Subprocess invocation with stdout/stderr asserts |
| F45 (new CLI ⇒ BDD same commit) | One feature file per service |
| F46 (BDD via factory) | Yes |
| F47 (integration via `build_*`) | Yes |
| F48 (composed E2E path) | One E2E test per service |
| F54 (feature flags both-branch) | N/A — `connect` is not feature-flag-gated |
| F68 (failure-injection contract tests) | One per Protocol method per implementation |

## Dependencies added to pyproject.toml

- `google-auth>=2.40`
- `google-auth-oauthlib>=1.2`
- `pyjwt[crypto]>=2.10` (for GitHub App JWT signing)
- `cryptography>=43` (pinned; transitive of `pyjwt[crypto]`)
- `slack-sdk>=3.34` (verify already present; bump version if needed)
- `azure-identity>=1.19` (managed-identity / service-principal credential chain for `--store=azure-kv`)
- `azure-keyvault-secrets>=4.9` (`SecretClient.set_secret` for `--store=azure-kv`)

Each justified per `services/<x>/DEPENDENCIES.md`-style rationale block
in the connect README.

## Documentation updates

- This ADR (`docs/architecture/ADR-032-oauth2-connect-flow.md`)
- `docs/operations/secrets-configuration.md` — add `kairix connect` recipes per service
- `docs/getting-started/quick-start.md` — add `kairix connect` step after the secrets-setup block
- `CHANGELOG.md` — v2026.5.32 entry: "new `kairix connect` family" + "Drive + Calendar now auto-refresh tokens (was silently failing on 401)"
- New `kairix/connect/README.md` — operator setup guide per service (the GCP/Slack/GitHub one-time setup steps that ARE unavoidable, called out explicitly)

## Build sequencing

**Sequential, NOT parallel.** Agent A builds the core abstractions
(`protocols.py`, `listener.py`, `store/`, `refresh.py`, CLI dispatcher,
the Google implementation as the canonical first instance) — this
includes the shared shape that B and C will follow. ~6-8h wall clock
with full BDD + Contract + Unit + Integration + E2E + sabotage proofs.

Once A lands cleanly, Agent B (Slack) and Agent C (GitHub App) dispatch
in parallel. Each one shorter (~3-4h) because they reuse A's
abstractions and only implement the per-service `OAuth2Flow`.

**Why sequential, not 3-parallel from the start:** if A's abstractions
are wrong, B and C build on a broken foundation and we waste agent
cycles. A's choices in Protocol shape, scope handling, refresh wrapping,
and store backend wire-up are load-bearing decisions that B and C
trust without re-litigating.

## Out of scope for this ADR

- Service-account JSON / GCP IAM Workload Identity Federation (different
  shape; useful for server-to-server in GCP; orthogonal to operator
  OAuth flows)
- M365 delegated-user mode (already covered by `msal`; would be an
  ADR-033 if operators request it)
- Apple CalDAV (no OAuth surface; app-specific password stays)
- Dex / Anthropic / OpenAI / Anthropic (no OAuth surface; API keys only)
- Notion (Internal Integration tokens are already minimal — single click
  in Notion UI, no automation worth building)

## Open questions

1. **Slack workspace instance plumbing** — does this land alongside #362
   (per-workspace instance support in canonical naming), or does
   `kairix connect slack` ship with `instance=None` initially and the
   per-workspace shape arrive in a follow-up? *Resolution:* ship
   `connect slack` with `--workspace NAME` flag using the instance slot
   from day one; #362 closes when the LEGACY_ALIASES rows are
   extended for the per-workspace shape.
2. **`kairix connect` writing to operator's KV** — the `--store=azure-kv:<vault>`
   path shells out to `az keyvault secret set`. Does the operator's
   shell already have `az login` credentials? *Resolution:* if not,
   we fail with `next: run 'az login' then re-run kairix connect`.
   Don't try to handle Azure auth ourselves.
3. **Browser-launch on remote servers (headless VM deployments)** —
   `kairix connect` on a headless VM can't open a browser locally.
   *Resolution:* support `--no-browser` mode that prints the authorize
   URL for the operator to paste into their local browser; operator
   then runs `kairix connect ... --paste-code XYZ` to finish the
   exchange. Treat as v2 — ship the localhost flow first.

## Acceptance

- All 3 services have working `kairix connect <service>` commands
- BDD + Contract + Unit + Integration + E2E tests green per F-rule discipline
- CHANGELOG entry covers operator-visible changes
- ADR-031 SecretsLoader resolution path unchanged (verify via existing tests)
- `docs/operations/secrets-configuration.md` updated with new recipes
- Sabotage proofs documented in agent reports
