"""kairix.platform.onboard.startup_preflight — boot-time credential preflight.

#449 — the docker quick-start ships ``.env.example`` with placeholder
credential values (``your-api-key-here`` / ``PASTE-YOUR-LLM-KEY-HERE``).
A first-time operator who copies ``.env.example`` to ``.env`` without
editing it gets a deployment that *boots* (BM25 keyword search works
without an LLM key) but whose vector search silently returns 0 hits —
because the placeholder string reads as truthy everywhere a real key
would.

This module makes that degraded state HONEST and ACTIONABLE:

  - ``preflight_startup_credentials(env)`` returns a list of
    :class:`StartupCredentialFailure`, each carrying a ``severity``
    (``"warn"`` or ``"fatal"``), the offending VAR NAME (never its
    value — F15), and an operator-actionable ``fix`` / ``next`` / ``run``
    message.

  - A missing or placeholder LLM api-key / endpoint is ``"warn"``:
    BM25 search still works; vector search will not until the operator
    pastes a real key. The container still boots (warn-and-degrade to
    search-only).

  - An EMPTY neo4j password *when a neo4j URI is configured* is
    ``"fatal"``: the bundled neo4j container refuses to start with an
    empty password, so the graph layer genuinely cannot run. ``serve``
    hard-exits in this case.

The canonical LLM var-name vocabulary (``_CANONICAL_SECRETS`` /
``_LEGACY_SECRETS``) is imported from :mod:`kairix.platform.onboard.check`
— it is the single source of truth (F85). Placeholder detection reuses
the one ``_PLACEHOLDER_VALUES`` set defined here so the boot warning and
the ``/healthz/ready`` capability probe agree on what "placeholder" means.

F15: every emitted string names only VAR NAMES, never a credential
value. F21-shaped: each message carries ``fix:`` / ``next:`` / ``run:``
action markers so an operator (or agent) reading ``docker logs`` knows
the exact next step.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from kairix.platform.onboard.check import _CANONICAL_SECRETS, _LEGACY_SECRETS

Severity = Literal["warn", "fatal"]

# Known placeholder / sentinel credential values. A value that equals
# (case-insensitively) any of these — or the empty string — is treated as
# "not a real credential". This is the single source of truth shared by the
# boot-time warning (``preflight_startup_credentials``) and the
# ``/healthz/ready`` capability probe, so both agree on what counts as a
# placeholder.
#
# Covers the historical ``.env.example`` soft placeholders
# (``your-api-key-here`` / ``https://your-resource...``) AND the #449
# obvious-broken sentinels (``PASTE-YOUR-LLM-KEY-HERE`` /
# ``PASTE-YOUR-LLM-ENDPOINT-HERE``). Matching is done on a normalised
# (lower-cased, stripped) value so casing variants don't slip through.
_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "your-api-key-here",
        "your-api-key",
        "your-resource",
        "https://your-resource.openai.azure.com",
        "paste-your-llm-key-here",
        "paste-your-llm-endpoint-here",
        "paste-your-key-here",
        "changeme",
        "change-me",
        "set-a-strong-password-here",
    }
)

# Neo4j password variable — read from check.py's canonical vocab would be
# ideal, but the neo4j pair is not part of the LLM secrets vocab. The
# password var name is single-sourced here (the one boot-time consumer);
# the resolver lives in kairix.secrets._legacy.neo4j_password.
_NEO4J_PASSWORD_VAR = "KAIRIX_NEO4J_PASSWORD"  # noqa: S105 — env-var name  # pragma: allowlist secret — env-var name
_NEO4J_URI_VAR = "KAIRIX_NEO4J_URI"


@dataclass(frozen=True)
class StartupCredentialFailure:
    """A single credential problem found at boot.

    Fields:
      var_name — the env-var NAME at fault (never its value — F15).
      severity — ``"warn"`` (degrade to search-only; still boots) or
                 ``"fatal"`` (refuse to start).
      reason   — one-line explanation naming the var, never its value.
      fix      — operator-actionable remediation (``fix:`` / ``next:`` /
                 ``run:`` markers).

    The whole dataclass is frozen so a failure can't be mutated after the
    preflight produced it — callers log it verbatim.
    """

    var_name: str
    severity: Severity
    reason: str
    fix: str


def is_placeholder(value: str | None) -> bool:
    """True when *value* is unset, empty, or a known placeholder/sentinel.

    Normalises to a lower-cased, stripped form before matching so casing
    and surrounding whitespace variants of the sentinels are all caught.
    A ``None`` (var unset) counts as a placeholder.
    """
    if value is None:
        return True
    return value.strip().lower() in _PLACEHOLDER_VALUES


def _llm_credential_warning() -> StartupCredentialFailure:
    """Build the warn-severity failure for a missing/placeholder LLM key.

    Names both the api-key and endpoint vars (whichever is the problem)
    so the operator sets the right thing. Never echoes the value.
    """
    api_key_var = _CANONICAL_SECRETS[0]
    endpoint_var = _CANONICAL_SECRETS[1]
    return StartupCredentialFailure(
        var_name=api_key_var,
        severity="warn",
        reason=(
            f"{api_key_var} / {endpoint_var} is unset or still the placeholder value — "
            "vector (semantic) search will return 0 hits. BM25 keyword search still works."
        ),
        fix=(
            f"fix: set {api_key_var} and {endpoint_var} to your real LLM provider key + "
            "endpoint in .env (or your secrets file). "
            "next: re-start the container — vector search activates on the next boot. "
            f"run: kairix secrets set {api_key_var} <your-key> "
            "(or edit .env and `docker compose up -d`)."
        ),
    )


def _neo4j_fatal_failure() -> StartupCredentialFailure:
    """Build the fatal-severity failure for an empty neo4j password + URI set."""
    return StartupCredentialFailure(
        var_name=_NEO4J_PASSWORD_VAR,
        severity="fatal",
        reason=(
            f"{_NEO4J_PASSWORD_VAR} is empty but {_NEO4J_URI_VAR} is configured — "
            "the bundled neo4j container refuses to start with an empty password, so the "
            "graph layer cannot run."
        ),
        fix=(
            f"fix: set {_NEO4J_PASSWORD_VAR} to a non-empty value in .env "
            "(the bundled compose default `kairix-local-dev` is fine on a laptop; "
            "use a strong password for any shared host). "
            "next: re-run `docker compose up -d` once the password is set. "
            f"run: edit .env so `{_NEO4J_PASSWORD_VAR}=<a-strong-password>`."
        ),
    )


def _llm_credentials_present(env: Mapping[str, str]) -> bool:
    """True when EITHER the canonical OR legacy LLM pair has real (non-placeholder) values.

    A pair counts as present only when both its api-key and endpoint
    resolve to non-placeholder values — a real key with a placeholder
    endpoint (or vice versa) is still degraded.
    """
    for pair in (_CANONICAL_SECRETS, _LEGACY_SECRETS):
        api_key = env.get(pair[0])
        endpoint = env.get(pair[1])
        if not is_placeholder(api_key) and not is_placeholder(endpoint):
            return True
    return False


def preflight_startup_credentials(env: Mapping[str, str]) -> list[StartupCredentialFailure]:
    """Return the credential problems found in *env*, severity-tagged.

    ``env`` is the resolved process environment (after
    ``bootstrap_secrets()`` has hydrated any secrets file). Passed
    explicitly so callers drive the preflight without monkey-patching
    ``os.environ`` (F2).

    Rules:
      - LLM api-key/endpoint unset OR placeholder (neither the canonical
        ``KAIRIX_PROVIDER_LLM_*`` nor the legacy ``KAIRIX_LLM_*`` pair has
        a real value) → ONE ``"warn"`` failure (degrade to search-only).
      - neo4j password empty AND a neo4j URI is configured → ONE
        ``"fatal"`` failure (graph layer can't run).
      - Everything resolves → empty list.
    """
    failures: list[StartupCredentialFailure] = []

    if not _llm_credentials_present(env):
        failures.append(_llm_credential_warning())

    neo4j_uri = env.get(_NEO4J_URI_VAR, "")
    neo4j_password = env.get(_NEO4J_PASSWORD_VAR, "")
    if neo4j_uri.strip() and not neo4j_password.strip():
        failures.append(_neo4j_fatal_failure())

    return failures
