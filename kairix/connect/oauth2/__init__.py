"""Per-service OAuth2 flow implementations for ``kairix connect``.

Each module here is a thin wrapper around the official client library
for one service. Per ADR-032 §"Library choices":

  * :mod:`kairix.connect.oauth2.google` — Google OAuth2 via
    ``google-auth-oauthlib`` (Phase 1).
  * ``slack`` — TBD by Agent B (Slack ``WebClient.oauth_v2_access``).
  * ``github_app`` — TBD by Agent C (PyJWT + raw httpx).
"""

from __future__ import annotations
