"""``kairix.connect`` — operator-driven OAuth2 token capture for connectors.

See ``docs/architecture/ADR-032-oauth2-connect-flow.md`` for the full
architectural contract. This package implements Phase 1: the core
abstractions (``protocols``, ``listener``, ``store/``, ``refresh``) plus
the Google OAuth2 flow as the canonical first instance.

Public surface — what other kairix subsystems import:

  * :mod:`kairix.connect.protocols` — ``OAuth2Flow``, ``CallbackListener``,
    ``TokenStore``, ``RefreshableToken`` Protocols + the frozen value
    objects (``ClientCredentials``, ``CapturedTokens``, ``CallbackResult``,
    ``WriteReport``).
  * :mod:`kairix.connect.refresh` — ``GoogleRefreshableToken`` wrapper.
    Connectors (Gmail / Drive / Calendar) import this so a 401 from the
    upstream API triggers a transparent token refresh instead of a hard
    ``CredentialExpiredError``.
  * :mod:`kairix.connect.cli` — entry point for ``kairix connect
    google-gmail | google-drive | google-calendar`` (Phase 1 covers
    Google; Slack + GitHub App land in follow-up agent dispatches).

Layering (F26 / F35 alignment): this package imports from
``kairix.core.protocols``, ``kairix.secrets``, stdlib, and the
third-party libs (``google-auth``, ``google-auth-oauthlib``,
``azure-identity``, ``azure-keyvault-secrets``). It MUST NOT import
from any ``kairix.connectors.<x>/`` plugin tree. The dependency points
the other way: ``kairix.connectors.google_drive.auth`` (and the gmail /
calendar siblings) imports from :mod:`kairix.connect.refresh`.
"""

from __future__ import annotations
