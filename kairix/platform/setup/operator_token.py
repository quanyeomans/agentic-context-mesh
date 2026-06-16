"""First-boot operator-token provisioning for the web setup wizard (#500).

On stock Docker the wizard's :class:`OperatorTokenGuard` sees every
published-port peer as the bridge gateway IP (never loopback) and a
browser cannot send the ``X-Kairix-Operator-Token`` header — so a browser
needs the tokened-URL → signed-cookie grant. That grant only works when an
operator token is configured. Operators who set
``KAIRIX_INFRA_OPERATOR_TOKEN`` themselves keep full control; for everyone
else, this module mints one at first boot (the Jupyter pattern) and prints
the tokened URL to the container log.

Invoked from the s6 ``cont-init`` step as
``python -m kairix.platform.setup.operator_token`` — a kairix entrypoint
rather than raw shell, so ``secrets.token_urlsafe`` generates the value and
``kairix.secrets.set_secret`` does the leak-safe 0600 write (F83 keeps the
shell side trivial). Idempotent: a token already present (env OR bundle) is
left untouched, so a container restart never rotates the token and
invalidates an operator's bookmarked tokened URL.

The minted token is a live credential — its ONLY print surface is the
onboarding URL emitted here (and from ``kairix mcp serve``); it is never
written to an application log (F15).
"""

from __future__ import annotations

import secrets as _secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from kairix.secrets.naming import canonical_env_var, canonical_secret_name

# The canonical operator-token identity the guard resolves.
_SCOPE: Literal["infra"] = "infra"
_AREA = "operator"
_LEAF = "token"
_TOKEN_BYTES = 32


def _existing_token(env: Mapping[str, str], *, bundle_path: Path | None) -> str | None:
    """The operator token already in scope (env first, then the bundle).

    The env var wins so an operator-supplied
    ``KAIRIX_INFRA_OPERATOR_TOKEN`` is always honoured. Falls back to the
    persisted bundle so a token minted on a previous boot is reused.
    ``bundle_path`` is the test seam — production resolves the bundle via
    the loader's default search path.
    """
    env_var = canonical_env_var(_SCOPE, _AREA, None, _LEAF)
    from_env = env.get(env_var)
    if from_env:
        return from_env
    if bundle_path is not None:
        from kairix.secrets._legacy import load_secrets_file

        if not bundle_path.exists():
            return None
        return load_secrets_file(bundle_path).get(env_var) or None
    from kairix.secrets.loader import SecretsLoader

    return SecretsLoader().get(_SCOPE, _AREA, None, _LEAF)


def ensure_operator_token(
    *,
    env: Mapping[str, str] | None = None,
    bundle_path: Path | None = None,
) -> tuple[str, bool]:
    """Return ``(token, minted)`` — minting + persisting one only if absent.

    ``minted`` is True only when this call generated a fresh token (via
    ``secrets.token_urlsafe(32)``); an already-configured token (env or
    bundle) is returned with ``minted=False`` and nothing is written
    (idempotent — a restart never rotates the token).

    Args:
        env: Environment mapping (test seam). Defaults to ``os.environ``.
        bundle_path: Explicit secrets-bundle file (test seam). ``None``
            lets :func:`kairix.secrets.set_secret` /
            :class:`SecretsLoader` resolve the production bundle path.

    Returns:
        ``(token, minted)``.
    """
    import os

    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    existing = _existing_token(resolved_env, bundle_path=bundle_path)
    if existing:
        return existing, False
    token = _secrets.token_urlsafe(_TOKEN_BYTES)
    from kairix.secrets.store import set_secret

    set_secret(canonical_secret_name(_SCOPE, _AREA, None, _LEAF), token, bundle_path=bundle_path)
    return token, True


def onboarding_message(token: str, *, minted: bool, env: Mapping[str, str] | None = None) -> str:
    """The operator-facing onboarding line (the token's sanctioned surface).

    Public so first-boot callers (the s6 entrypoint here, and any future
    operator-help command) emit ONE consistent line. The tokened URL is a
    live credential; this string belongs only on the onboarding surface,
    never an application log (F15).
    """
    from kairix.paths import wizard_tokened_url

    url = wizard_tokened_url(token=token, environ=env)
    lead = (
        "kairix: generated a first-boot operator token for the setup wizard."
        if minted
        else "kairix: operator token already configured for the setup wizard."
    )
    return f"{lead}\nOpen this one-time URL in a browser to grant access:\n  {url}"


def main(
    *,
    env: Mapping[str, str] | None = None,
    bundle_path: Path | None = None,
    writer: object = None,
) -> int:
    """s6 cont-init entrypoint: ensure a token, print the tokened URL.

    Seams (``env`` / ``bundle_path`` / ``writer``) keep the entrypoint
    testable without touching the real bundle or process env (F1/F2-clean);
    production leaves them at their defaults.
    """
    import os

    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    emit = writer if callable(writer) else print
    token, minted = ensure_operator_token(env=resolved_env, bundle_path=bundle_path)
    emit(onboarding_message(token, minted=minted, env=resolved_env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
