"""Integration: ``kairix secrets set`` writes the exact key the loader reads.

Regression lock for #473 (the secrets naming split-brain). The defect a
new operator hit: every onboarding doc taught the retired ``KAIRIX_LLM_*``
name, but :class:`kairix.secrets.loader.SecretsLoader` only resolves the
canonical ``KAIRIX_PROVIDER_LLM_*`` form — and there was no persistence
verb to write a value the loader would then read.

These tests prove the two halves agree end-to-end for the provider LLM
api-key that #473 is about:

* ``test_cli_set_then_verify_resolves_via_subprocess`` drives the real
  operator loop — ``kairix secrets set`` (value piped via stdin, the
  leak-safe path) then ``kairix secrets verify --json`` — both across a
  fresh process boundary against the same bundle. ``verify`` hydrates the
  bundle through :func:`kairix.secrets.bootstrap.bootstrap_secrets` and
  walks the production :class:`SecretsLoader`, so a ``present`` row proves
  the written key IS the key the loader resolves. (F30 subprocess outcome.)
* ``test_set_secret_writes_the_env_key_the_loader_requires`` pins the same
  invariant in-process and crisply: ``set_secret`` → hydrated
  ``SecretsLoader.require(...)`` returns the stored value. If ``set_secret``
  wrote any other env key than ``canonical_env_var(...)``, ``require``
  raises :class:`SecretNotFoundError`.

Sabotage-proof (executed): in ``kairix/secrets/store.py`` ``set_secret``,
replaced ``env_var = _validated_env_var(name)`` with a hard-coded retired
key (``env_var = "KAIRIX_LLM_API_KEY"``) — reproducing the #473 split-brain
where the write side names the retired var. Both tests failed:
``require`` raised ``SecretNotFoundError`` and the ``verify`` row read
``MISSING``. Restored; both pass.

Hermetic: the bundle lives under ``tmp_path``; the subprocess env sets
``KAIRIX_SECRETS_FILE`` at that bundle and strips every inherited
``KAIRIX_*`` provider var so the real operator store is never consulted.
F2-clean: the in-process test flows through the explicit ``bundle_path=``
and ``env=`` seams — no ``os.environ`` mutation. F15: the fixture value is
a generic string and nothing logs or prints it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.secrets import load_secrets_file
from kairix.secrets.loader import SecretsLoader
from kairix.secrets.naming import canonical_secret_name

pytestmark = pytest.mark.integration

# The provider LLM api-key identity #473 is about.
_LLM_SCOPE = "provider"
_LLM_AREA = "llm"
_LLM_LEAF = "api-key"
_LLM_NAME = canonical_secret_name(_LLM_SCOPE, _LLM_AREA, None, _LLM_LEAF)

# Generic fixture value — not a real credential.
_FIXTURE_VALUE = "round-trip-secret-value-alpha"  # pragma: allowlist secret — generic test fixture


def _hermetic_env(bundle: Path) -> dict[str, str]:
    """A subprocess env pinned to a tmp bundle with no inherited secrets."""
    env = dict(os.environ)
    env["HOME"] = str(bundle.parent)
    env["XDG_CONFIG_HOME"] = str(bundle.parent / "config")
    env["KAIRIX_SECRETS_FILE"] = str(bundle)
    for key in list(env):
        if key.startswith("KAIRIX_PROVIDER_") or key.startswith("KAIRIX_LLM_"):
            env.pop(key, None)
    return env


def _run_cli(args: list[str], *, env: dict[str, str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=60,
        check=False,
    )


def test_cli_set_then_verify_resolves_via_subprocess(tmp_path: Path) -> None:
    """set (stdin) then verify --json: the loader resolves the written key."""
    bundle = tmp_path / "kairix.env"
    env = _hermetic_env(bundle)

    set_result = _run_cli(["secrets", "set", _LLM_NAME], env=env, stdin=_FIXTURE_VALUE)
    assert set_result.returncode == 0, f"set failed: {set_result.stderr}\n{set_result.stdout}"
    # F15: the value never appears in the command output.
    assert _FIXTURE_VALUE not in (set_result.stdout + set_result.stderr)

    verify_result = _run_cli(["secrets", "verify", "--json"], env=env)
    payload = json.loads(verify_result.stdout)
    rows = {(r["scope"], r["area"], r["leaf"]): r["status"] for r in payload["secrets"]}
    assert rows[(_LLM_SCOPE, _LLM_AREA, _LLM_LEAF)] == "present", (
        f"loader did not resolve what `secrets set` wrote — split-brain regression (#473). "
        f"verify rows: {payload['secrets']}"
    )


def test_set_secret_writes_the_env_key_the_loader_requires(tmp_path: Path) -> None:
    """In-process: set_secret's written key is exactly the loader's read key."""
    from kairix.secrets.store import set_secret

    bundle = tmp_path / "kairix.env"
    set_secret(_LLM_NAME, _FIXTURE_VALUE, bundle_path=bundle)

    load_secrets_file.cache_clear()
    loader = SecretsLoader(env=dict(load_secrets_file(bundle)))
    resolved = loader.require(_LLM_SCOPE, _LLM_AREA, None, _LLM_LEAF)
    assert resolved == _FIXTURE_VALUE
