# `kairix connect` — OAuth2 token capture for connectors

Operator-only CLI family for capturing OAuth2 tokens for connector authentication. Currently covers Google Workspace (Gmail / Drive / Calendar) and Slack workspaces; GitHub App lands in a follow-up release sharing the same `kairix.connect` abstractions.

This is the package documentation. Operator-facing setup steps live in [`docs/operations/secrets-configuration.md`](../../docs/operations/secrets-configuration.md). The architectural contract is [`docs/architecture/ADR-032-oauth2-connect-flow.md`](../../docs/architecture/ADR-032-oauth2-connect-flow.md).

## What this package does

You run a single command:

```bash
kairix connect google-gmail --client-secret-path ~/Downloads/client_secret.json
```

`kairix.connect` then:

1. Reads the OAuth client credentials from the downloaded `client_secret.json`.
2. Starts a localhost HTTP listener on `127.0.0.1:8080/oauth2callback` (configurable via `--port`).
3. Opens your default browser to Google's consent screen.
4. Catches the OAuth callback containing the authorization code.
5. Exchanges the code for tokens via Google's OAuth2 endpoint.
6. Writes the four canonical-named secrets to your chosen store backend (file / Azure Key Vault / stdout).
7. Prints a success summary listing the canonical secret names that were populated.

The connector itself reads tokens via the existing `kairix.secrets` resolver — unchanged. The `connect` command only *writes*.

## GCP setup walkthrough (one-time)

The four steps you have to do **once per kairix install** before running `kairix connect google-*` for the first time:

1. **Create or pick a GCP project.** Open the [GCP console](https://console.cloud.google.com/) → project selector → either pick an existing project or create a new one.

2. **Enable the APIs you need.** Under *APIs & Services → Library*, search for and enable:
   - Gmail API (if you'll connect Gmail)
   - Google Drive API (if you'll connect Drive)
   - Google Calendar API (if you'll connect Calendar)

3. **Set the OAuth consent screen to Production.** Under *APIs & Services → OAuth consent screen*:
   - **User type:** External
   - Fill in App name, support email, developer email
   - Add scopes you want (the connectors use read-only scopes — `gmail.readonly`, `drive.readonly`, `calendar.readonly`)
   - **Click "Publish App" to move from Testing to Production.**

   This step is critical and unavoidable. In Testing mode Google silently expires refresh tokens after 7 days; your connector will stop working a week after capture with a confusing error. Production mode keeps refresh tokens valid until the operator (or Google's revocation policy) actively revokes them.

4. **Create the OAuth client.** Under *APIs & Services → Credentials*:
   - Click **Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "kairix-connect")
   - After creation, click the download icon to download the JSON (e.g. `client_secret_<long-id>.json`)

Save that JSON somewhere you can reference it — `~/Downloads/client_secret.json` is fine.

## Running `kairix connect`

For each Google service you want to connect:

```bash
kairix connect google-gmail --client-secret-path ~/Downloads/client_secret.json
kairix connect google-drive --client-secret-path ~/Downloads/client_secret.json
kairix connect google-calendar --client-secret-path ~/Downloads/client_secret.json
```

You can use the **same** `client_secret.json` for all three. The captured tokens land under distinct canonical names so they don't clobber each other:

| Subcommand | Canonical secret names written |
|---|---|
| `google-gmail` | `KAIRIX_CONNECTOR_GMAIL_{CLIENT_ID,CLIENT_SECRET,REFRESH_TOKEN,ACCESS_TOKEN}` |
| `google-drive` | `KAIRIX_CONNECTOR_GOOGLE_DRIVE_{CLIENT_ID,CLIENT_SECRET,REFRESH_TOKEN,ACCESS_TOKEN}` |
| `google-calendar` | `KAIRIX_CONNECTOR_GOOGLE_CALENDAR_{CLIENT_ID,CLIENT_SECRET,REFRESH_TOKEN,ACCESS_TOKEN}` |

(Canonical names follow [ADR-031](../../docs/architecture/ADR-031-canonical-credential-naming.md).)

## Store backends

Pass `--store=` to control where tokens land:

```bash
# Default — writes to $KAIRIX_SECRETS_FILE (default ~/.config/kairix/secrets/kairix.env)
kairix connect google-gmail --client-secret-path <path>

# Stdout — TSV lines you can pipe anywhere
kairix connect google-gmail --client-secret-path <path> --store=stdout

# Azure Key Vault — reads $KAIRIX_KV_NAME for the vault short-name
kairix connect google-gmail --client-secret-path <path> --store=azure-kv

# Azure Key Vault — explicit short-name (overrides $KAIRIX_KV_NAME)
kairix connect google-gmail --client-secret-path <path> --store=azure-kv:my-vault

# Azure Key Vault — full URL (sovereign clouds, non-default DNS)
kairix connect google-gmail --client-secret-path <path> --store=azure-kv:https://my-vault.vault.usgovcloudapi.net/
```

Azure Key Vault requires the identity running `kairix connect` to have the **Key Vault Secrets Officer** role on the target vault. The more restrictive **Key Vault Secrets User** role is read-only and insufficient for writes. See ADR-032 §"Operator setup for `--store=azure-kv`" for the full identity-options matrix.

## Failure modes

Every failure surface emits an actionable hint:

- **Browser doesn't open** → fix: confirm the consent screen URL is reachable; on headless VMs run from your workstation.
- **Port in use** → the listener scans forward up to 50 ports automatically; on persistent collisions pass `--port 9090`.
- **Consent denied** → re-run and approve the consent screen.
- **Callback timeout (120s default)** → the browser may not have opened; try again with `--port` to a known-free port.
- **Missing `client_secret.json`** → download fresh from the GCP console (see GCP setup §3 above).
- **KV write denied** → confirm the identity has Key Vault Secrets Officer (not just Secrets User).

## Library dependencies

`kairix.connect` adds these dependencies:

- `google-auth>=2.40` + `google-auth-oauthlib>=1.2` — Google OAuth2 dance and refresh.
- `azure-identity>=1.19` + `azure-keyvault-secrets>=4.9` — Azure Key Vault writes (only loaded when `--store=azure-kv*`).

All four are lazy-imported: operators who only use the file or stdout store never load the Azure SDKs; operators who pass their own `token_exchanger` to `GoogleOAuth2Flow` in tests never load `google-auth-oauthlib`.

## Slack setup walkthrough (one-time per workspace)

For each Slack workspace you want kairix to read, do this once at https://api.slack.com/apps:

1. **Create the Slack app.** Click **Create New App → From scratch**, name it (e.g. `kairix-connect`), pick the workspace you're installing into.

2. **Configure OAuth scopes.** Under **OAuth & Permissions → Scopes → Bot Token Scopes**, add (at minimum): `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `users:read`. This is the default scope set `kairix connect slack` requests; if your workspace needs a narrower scope, override with `--scopes` on the CLI (rare).

3. **Configure the redirect URL.** Under **OAuth & Permissions → Redirect URLs**, add `http://127.0.0.1:8080/oauth2callback` (the default port `kairix connect` listens on; if you use `--port 9090`, register `http://127.0.0.1:9090/oauth2callback` instead). Slack requires the redirect URL to match exactly.

4. **Capture client_id + client_secret.** Under **Basic Information → App Credentials**, copy the **Client ID** and **Client Secret**. You'll pass both on the kairix CLI in step 5.

5. **Run kairix connect.** From your workstation:
   ```bash
   kairix connect slack --workspace alpha --client-id <client-id> --client-secret <client-secret>
   ```
   The browser opens to Slack's install screen — pick the workspace and click **Allow**. The captured bot token lands in your secrets store under the per-workspace canonical name `KAIRIX_CONNECTOR_SLACK_<workspace-uppercase>_BOT_TOKEN`.

You can run this multiple times for different workspaces — each run lands tokens under a distinct `--workspace` slug, so workspace `alpha` and workspace `coach` co-exist in the same KV without collision.

**Optional: Socket Mode (`app_token`).** Slack's `oauth.v2.access` does NOT return the `xapp-…` app-level token used for Socket Mode push events. Capture it manually from **Basic Information → App-Level Tokens → Generate** and pre-populate `kairix-connector-slack-<workspace>-app-token` in your KV; the connector picks it up automatically when present.

**Canonical names written by `kairix connect slack`:**

| Subcommand | Canonical secret names written |
|---|---|
| `slack --workspace <NAME>` | `KAIRIX_CONNECTOR_SLACK_<NAME>_{CLIENT_ID, CLIENT_SECRET, BOT_TOKEN}` (CLIENT_ID / CLIENT_SECRET are written so subsequent re-runs can re-exchange without re-prompting) |

Slack bot tokens never expire — there is no `REFRESH_TOKEN` or `ACCESS_TOKEN` written for Slack (those fields would be empty and the file store skips empty leaves at write time). The connector reads the bot token directly and uses a `StaticRefreshableToken` wrapper that never refreshes.

## What's not yet covered

- **Headless `--no-browser` mode** — paste-the-URL-into-your-local-browser flow. Planned for a future release. For now `kairix connect` fails fast on headless VMs with a hint to run from your workstation.
- **Service-account JSON / GCP Workload Identity Federation** — out of scope; operators on those auth shapes provision tokens directly into KV.
- **AWS Secrets Manager** — planned; the SDK shape mirrors Azure but isn't yet implemented.
- **Slack `app_token` auto-capture** — Slack's OAuth v2 response doesn't return the app-level token; operators on Socket Mode capture it manually (one-time, per the optional step in the Slack setup above).

## Architecture

```
kairix/connect/
├── __init__.py
├── README.md             # this file
├── cli.py                # `kairix connect <service>` dispatcher (SUBCOMMAND_REGISTRY)
├── listener.py           # localhost HTTP callback listener (shared)
├── protocols.py          # OAuth2Flow / CallbackListener / TokenStore / RefreshableToken
├── refresh.py            # RefreshableToken wrappers used by connectors at runtime
├── oauth2/
│   ├── __init__.py
│   ├── google.py         # GoogleOAuth2Flow — gmail / google-drive / google-calendar
│   └── slack.py          # SlackOAuth2Flow — per-workspace bot-token capture
└── store/
    ├── __init__.py
    ├── _leaves.py        # shared leaf-derivation helper (per-service shape)
    ├── file_store.py     # writes $KAIRIX_SECRETS_FILE
    ├── azure_kv_store.py # writes via azure-identity + azure-keyvault-secrets
    └── stdout_store.py   # TSV emission
```

The Protocol surface (`protocols.py`) is the only thing connectors import. The store implementations are private to `kairix.connect`; per F26/F35 layering, the connectors must not import them. The Drive + Calendar connectors import `kairix.connect.refresh` (and only that) to get the `GoogleRefreshableToken` wrapper they need at runtime.
