"""kairix.secrets — canonical credential naming + resolution boundary.

Public surface for callers:

* :func:`get_secret` / :func:`load_secrets` / :func:`refresh_secrets`
  / :func:`neo4j_uri` / :func:`neo4j_uri_configured` /
  :func:`neo4j_user` / :func:`neo4j_password` /
  :func:`set_llm_endpoint` / :func:`set_llm_api_key` — historical
  resolver API; preserved verbatim from the pre-package layout.
* :class:`SecretsLoader` + :class:`SecretsResolver` +
  :class:`SecretNotFoundError` — new canonical-naming entry point.
  Construct ``SecretsLoader()`` and inject it via a connector's
  ``secrets=`` kwarg; tests pass :class:`tests.fakes.FakeSecretsLoader`.
* :func:`canonical_secret_name` / :func:`canonical_env_var` /
  :func:`parse_canonical_name` — pure functions for inspecting / round-
  tripping canonical names. See :mod:`kairix.secrets.naming` for the
  ambiguity-resolution rule.

The package layout intentionally hides the historical resolver under
:mod:`kairix.secrets._legacy` so the F4 / F15 / F76 path allowlists
apply to the whole ``kairix/secrets/`` tree.
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
    "set_llm_api_key",
    "set_llm_endpoint",
]
