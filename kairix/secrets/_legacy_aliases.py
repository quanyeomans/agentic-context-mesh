"""Legacy env-var aliases for the canonical secret naming scheme.

Maps every canonical identity tuple ``(scope, area, instance, leaf)``
to a list of legacy env-var names the loader should also try when the
canonical env-var lookup misses. A miss + legacy hit returns the
legacy value and emits a :class:`DeprecationWarning` so operators see
which alias they're depending on.

Each entry is paired with a one-release retirement target — the
loader keeps the alias resolving until the next release after a
successful dual-write soak, then the alias is removed.

The map is **internal** — connectors and providers don't read it
directly. They call :meth:`kairix.secrets.loader.SecretsLoader.get` /
``.require`` with their canonical identity tuple; the loader walks the
alias list internally.

Construction
------------
Built by walking every ``get_secret("connector-…")`` / ``get_secret(
"kairix-…")`` call site in ``kairix/connectors/**/*.py`` and
``kairix/providers/**/*.py`` plus the historic ``_SECRET_ENV_MAP`` in
:mod:`kairix.secrets._legacy`. Each call became a ``(scope, area,
instance, leaf)`` row; the matching legacy env-var(s) are the values.

Future connectors that ship with the canonical name from day one do
not need an alias row.
"""

from __future__ import annotations

from kairix.secrets.naming import Scope

# Hoisted leaf constant — F17 (duplicated string ≥10 chars used ≥3 times).
# Shared across M365, Slack, Gmail rows that all carry an OAuth-style
# client secret with the same leaf name.
_LEAF_CLIENT_SECRET = "client-secret"  # noqa: S105 — secret-SLOT name (the leaf identifier), not a value  # pragma: allowlist secret

# (scope, area, instance, leaf) -> list of legacy env-var names to try
# in order. The first hit wins; the loader emits a single
# DeprecationWarning per hit naming the alias and the canonical
# replacement so operators know what to rotate in their KV.
#
# Conventions in this map:
#   * Leaf names use hyphenated single-slot identifiers where the
#     original code used them (``tenant-id`` stays ``tenant-id``).
#   * The legacy env-var list always names the existing kairix-side
#     env var FIRST (e.g. ``KAIRIX_LLM_API_KEY``); any operator-facing
#     legacy form (``M365_TENANT_ID``) comes after.
#   * Removal target is one release after a successful dual-write soak.
#     The orchestrator owns the per-row removal; this map is the
#     contract surface, not the schedule.
LEGACY_ALIASES: dict[tuple[Scope, str, str | None, str], list[str]] = {
    # ── Infrastructure: Neo4j ──────────────────────────────────────
    ("infra", "neo4j", None, "password"): [
        "KAIRIX_NEO4J_PASSWORD",
    ],
    ("infra", "neo4j", None, "uri"): [
        "KAIRIX_NEO4J_URI",
    ],
    ("infra", "neo4j", None, "user"): [
        "KAIRIX_NEO4J_USER",
    ],
    # ── Providers: LLM (chat) ──────────────────────────────────────
    ("provider", "llm", None, "api-key"): [
        "KAIRIX_LLM_API_KEY",
    ],
    ("provider", "llm", None, "endpoint"): [
        "KAIRIX_LLM_ENDPOINT",
    ],
    ("provider", "llm", None, "model"): [
        "KAIRIX_LLM_MODEL",
    ],
    # ── Providers: embeddings ──────────────────────────────────────
    ("provider", "embed", None, "api-key"): [
        "KAIRIX_EMBED_API_KEY",
    ],
    ("provider", "embed", None, "endpoint"): [
        "KAIRIX_EMBED_ENDPOINT",
    ],
    ("provider", "embed", None, "model"): [
        "KAIRIX_EMBED_MODEL",
    ],
    # ── Connectors: M365 (shared tenant for SharePoint + email
    #    headers + calendar) ──────────────────────────────────────
    ("connector", "m365", None, "tenant-id"): [
        "CONNECTOR_M365_TENANT_ID",
        "KAIRIX_M365_TENANT_ID",
        "M365_TENANT_ID",
    ],
    ("connector", "m365", None, "client-id"): [
        "CONNECTOR_M365_CLIENT_ID",
        "KAIRIX_M365_CLIENT_ID",
        "M365_CLIENT_ID",
    ],
    ("connector", "m365", None, _LEAF_CLIENT_SECRET): [
        "CONNECTOR_M365_CLIENT_SECRET",  # pragma: allowlist secret
        "KAIRIX_M365_CLIENT_SECRET",  # pragma: allowlist secret
        "M365_CLIENT_SECRET",  # pragma: allowlist secret
    ],
    # ── Connectors: Slack ──────────────────────────────────────────
    # Singleton (default) — instance=None form. Pre-dates the
    # per-workspace plumbing; kept for backwards-compat with existing
    # operator deployments.
    ("connector", "slack", None, "bot-token"): [
        "CONNECTOR_SLACK_BOT_TOKEN",
    ],
    ("connector", "slack", None, "app-token"): [
        "CONNECTOR_SLACK_APP_TOKEN",
    ],
    ("connector", "slack", None, "client-id"): [
        "CONNECTOR_SLACK_CLIENT_ID",
    ],
    ("connector", "slack", None, _LEAF_CLIENT_SECRET): [
        "CONNECTOR_SLACK_CLIENT_SECRET",  # pragma: allowlist secret
    ],
    # Per-workspace shape (ADR-032 Phase 2 / #362 resolution). The
    # ``instance`` slot carries the workspace name so two Slack
    # workspaces (``alpha``, ``coach``) can co-resident in the same
    # KV without clobbering each other. New deployments use this
    # shape; the singleton rows above stay for back-compat. Operators
    # provisioning per-workspace tokens before #362 closes use the
    # canonical names directly (no legacy alias to map) — the canonical
    # name already names the workspace via the instance slot:
    #   kairix-connector-slack-<workspace>-bot-token
    #   kairix-connector-slack-<workspace>-app-token
    #   kairix-connector-slack-<workspace>-client-id
    #   kairix-connector-slack-<workspace>-client-secret
    # Per-workspace rows land here on a per-deployment basis as
    # operators retire whatever legacy single-token env-var they were
    # using. No compile-time enumeration of workspace names; rows
    # land in operator-controlled overrides at provision time.
    # ── Connectors: GitHub ─────────────────────────────────────────
    ("connector", "github", None, "pat"): [
        "CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN",
    ],
    ("connector", "github", None, "app-id"): [
        "CONNECTOR_GITHUB_APP_ID",
    ],
    ("connector", "github", None, "installation-id"): [
        "CONNECTOR_GITHUB_INSTALLATION_ID",
    ],
    ("connector", "github", None, "app-private-key"): [
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY",
    ],
    ("connector", "github", None, "webhook-secret"): [
        "CONNECTOR_GITHUB_WEBHOOK_SECRET",  # pragma: allowlist secret
    ],
    # ── Connectors: Notion ─────────────────────────────────────────
    ("connector", "notion", None, "token"): [
        "CONNECTOR_NOTION_TOKEN",
    ],
    # ── Connectors: Google Drive ───────────────────────────────────
    ("connector", "google-drive", None, "access-token"): [
        "CONNECTOR_GOOGLE_DRIVE_ACCESS_TOKEN",
    ],
    # ── Connectors: Gmail ──────────────────────────────────────────
    ("connector", "gmail", None, "client-id"): [
        "CONNECTOR_GMAIL_CLIENT_ID",
    ],
    ("connector", "gmail", None, _LEAF_CLIENT_SECRET): [
        "CONNECTOR_GMAIL_CLIENT_SECRET",  # pragma: allowlist secret
    ],
    ("connector", "gmail", None, "refresh-token"): [
        "CONNECTOR_GMAIL_REFRESH_TOKEN",
    ],
    ("connector", "gmail", None, "access-token"): [
        "CONNECTOR_GMAIL_ACCESS_TOKEN",
    ],
    # ── Connectors: Apple CalDAV (basic auth — app-specific password) ──
    ("connector", "apple-caldav", None, "username"): [
        "CONNECTOR_APPLE_CALDAV_USERNAME",
    ],
    ("connector", "apple-caldav", None, "access"): [
        "CONNECTOR_APPLE_CALDAV_PASSWORD",  # pragma: allowlist secret
    ],
    # ── Connectors: Dex CRM ────────────────────────────────────────
    ("connector", "dex", None, "api-key"): [
        "CONNECTOR_DEX_API_KEY",
    ],
}


# Reverse-lookup: legacy env-var name -> canonical KV name. Used by
# ``kairix secrets migrate-list`` to emit the operator migration table
# without re-walking the alias dict for every row.
def legacy_to_canonical_map() -> dict[str, str]:
    """Return ``{legacy_env_var: canonical_kv_name}`` for every alias."""
    from kairix.secrets.naming import canonical_secret_name

    out: dict[str, str] = {}
    for (scope, area, instance, leaf), legacy_names in LEGACY_ALIASES.items():
        canonical = canonical_secret_name(scope, area, instance, leaf)
        for legacy in legacy_names:
            out[legacy] = canonical
    return out


__all__ = ["LEGACY_ALIASES", "legacy_to_canonical_map"]
