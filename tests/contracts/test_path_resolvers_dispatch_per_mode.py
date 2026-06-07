"""Contract C2 (Plan 1) — every path resolver dispatches per Mode.

Pins the invariant that every Plan-1-listed path resolver in
``kairix.paths`` consults :class:`kairix.paths.Mode` and returns
distinct paths for ``system`` vs ``user`` mode. ``container`` mode may
share its path with ``system`` mode (intentional: the container image
owns the root tree and uses the FHS layout).

The dispatch happens through an explicit ``mode=`` arg — the resolver
short-circuits any ``KAIRIX_*`` env override when called with an
explicit mode, so the contract holds regardless of the test runner's
ambient environment.

Sabotage proof per resolver lives in the commit body.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLAN_1_RESOLVERS = [
    "config_dir",
    "data_dir",
    "cache_dir",
    "runtime_secrets_dir",
    "embedding_cache_path",
    "warm_flag_path",
    "index_path",
    "vec_index_path",
    "document_root",
]


@pytest.mark.contract
@pytest.mark.parametrize("resolver_name", _PLAN_1_RESOLVERS)
def test_resolver_dispatches_per_mode(resolver_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Plan-1 path resolver returns distinct paths per Mode.

    Pin: ``resolver(Mode.system) != resolver(Mode.user)``. Container may
    equal system (the container image uses the system FHS layout).

    The monkeypatch pins XDG base dirs to deterministic paths so the
    test passes on any host (including the test runner's $HOME where
    XDG defaults would otherwise expand). XDG_* env vars are a POSIX
    spec, not kairix internals — F1/F2-clean.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-config")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-cache")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/xdg-runtime")

    import kairix.paths as p

    resolver = getattr(p, resolver_name)
    system_path = resolver(p.Mode.system)
    user_path = resolver(p.Mode.user)
    container_path = resolver(p.Mode.container)

    # Every resolver returns a concrete pathlib.Path — never None or str.
    for label, value in (
        ("system", system_path),
        ("user", user_path),
        ("container", container_path),
    ):
        assert isinstance(value, Path), f"{resolver_name}({label}) returned {type(value).__name__}, expected Path"

    # The hard invariant: system + user MUST differ. Container may match
    # either (system in particular — that's the documented design).
    assert system_path != user_path, (
        f"{resolver_name} returns same path for system + user ({system_path}); fix: have the resolver dispatch on Mode."
    )
