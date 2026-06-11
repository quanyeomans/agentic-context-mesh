"""kairix.secrets — canonical credential naming + resolution boundary.

Public surface for callers:

* :func:`get_secret` / :func:`load_secrets` / :func:`refresh_secrets`
  / :func:`neo4j_uri` / :func:`neo4j_uri_configured` /
  :func:`neo4j_user` / :func:`neo4j_password` /
  :func:`set_llm_endpoint` / :func:`set_llm_api_key` — multi-source
  resolver (env / per-file mount / bundle file / KV CLI). Used by
  connectors that haven't migrated to :class:`SecretsLoader` yet.
* :class:`SecretsLoader` + :class:`SecretsResolver` +
  :class:`SecretNotFoundError` — canonical-naming entry point.
  Construct ``SecretsLoader()`` and inject it via a connector's
  ``secrets=`` kwarg; tests pass :class:`tests.fakes.FakeSecretsLoader`.
* :func:`canonical_secret_name` / :func:`canonical_env_var` /
  :func:`parse_canonical_name` — pure functions for inspecting / round-
  tripping canonical names. See :mod:`kairix.secrets.naming` for the
  ambiguity-resolution rule.
* :func:`set_secret` / :func:`resolve_bundle_path` — write-side
  persistence: upsert one canonical secret into the operator bundle
  file the read side hydrates at boot. Backs ``kairix secrets set``
  and the setup wizard.

The legacy env-var alias chain (``LEGACY_ALIASES`` + the loader's
``_try_legacy_aliases`` / ``_default_legacy_chain`` fallback) was
retired in #369 after the canonical-naming migration completed.
Operators must now use ``KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>``
env vars or the matching KV-mount file names.
"""

from __future__ import annotations

from kairix.secrets._legacy import (
    get_secret,
    load_secrets,
    load_secrets_file,
    neo4j_password,
    neo4j_uri,
    neo4j_uri_configured,
    neo4j_user,
    refresh_secrets,
    set_llm_api_key,
    set_llm_endpoint,
)
from kairix.secrets.loader import (
    SecretNotFoundError,
    SecretsLoader,
    SecretsResolver,
)
from kairix.secrets.naming import (
    Scope,
    canonical_env_var,
    canonical_secret_name,
    parse_canonical_name,
)
from kairix.secrets.store import (
    resolve_bundle_path,
    set_secret,
)

__all__ = [
    "Scope",
    "SecretNotFoundError",
    "SecretsLoader",
    "SecretsResolver",
    "canonical_env_var",
    "canonical_secret_name",
    "get_secret",
    "load_secrets",
    "load_secrets_file",
    "neo4j_password",
    "neo4j_uri",
    "neo4j_uri_configured",
    "neo4j_user",
    "parse_canonical_name",
    "refresh_secrets",
    "resolve_bundle_path",
    "set_llm_api_key",
    "set_llm_endpoint",
    "set_secret",
]
