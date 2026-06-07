"""Agent operating scope — config-driven replacement for the legacy
``{document_root}/04-Agent-Knowledge/<agent>/memory`` convention (PR 1.1 / #420).

The production vault uses a flat layout (files live directly under
``04-Agent-Knowledge/<agent>/``) but the legacy resolver hardcoded a
``/memory`` subdirectory. PR 1.1 introduces the abstraction + config loader
so PR 1.2 can swap the four hardcoded callsites onto it without behavioural
guesswork at the boundary.

Config shape::

    agents:
      shape:
        harness: claude-code
        surfaces:
          - { path: /data/obsidian-vault/04-Agent-Knowledge/shape, label: memory }
          - { path: /data/workspaces/shape, label: workspace }
    agent_defaults:
      memory_root: /data/obsidian-vault/04-Agent-Knowledge
      workspace_root: /data/workspaces
      glob: "**/*.md"

Resolution order, applied per agent name:

  1. Explicit ``agents.<name>`` entry — returned verbatim.
  2. ``agent_defaults`` synthesis — build a scope with two surfaces
     (``memory_root/<name>`` labelled ``memory`` and, when the directory
     exists, ``workspace_root/<name>`` labelled ``workspace``). A
     ``logger.warning`` records the drift so operators see it and can run
     ``kairix onboard agent --name <name>`` to commit explicit config.
  3. Neither present → ``ValueError`` with the agent name in the message so
     the operator knows which scope is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default glob applied to every AgentSurface when the config (or default
# synthesis) doesn't override it. Centralised here so the four call sites
# (dataclass default, loader fallback, defaults synthesis, document-root
# fallback) stay in lockstep.
_DEFAULT_GLOB = "**/*.md"


@dataclass(frozen=True)
class AgentSurface:
    """One configured directory in an agent's operating scope.

    ``path`` is the absolute (or document-root-relative) directory to read or
    write. ``glob`` is the pattern that selects files within the surface;
    default ``**/*.md`` matches any markdown anywhere under ``path``.
    ``label`` is a human/operator tag — typically ``"memory"`` or
    ``"workspace"`` — used by :meth:`AgentScope.writable_path` to pick the
    right surface for writes.
    """

    path: Path
    glob: str = _DEFAULT_GLOB
    label: str = ""


@dataclass(frozen=True)
class AgentScope:
    """An agent's complete operating surface — every directory kairix should
    read or write for that agent.

    ``surfaces`` is the ordered tuple of configured directories.
    :meth:`memory_paths` returns every surface's path. :meth:`writable_path`
    returns the path of the first surface labelled ``"memory"`` (or the first
    surface if none labelled). ``harness`` is informational — e.g.
    ``"claude-code"`` or ``"codex"`` — for operator visibility only; runtime
    behaviour does not depend on it.
    """

    name: str
    surfaces: tuple[AgentSurface, ...]
    harness: str = ""

    def memory_paths(self) -> tuple[Path, ...]:
        """Return the path of every surface in declared order."""
        return tuple(s.path for s in self.surfaces)

    def writable_path(self) -> Path:
        """Return the path of the surface labelled ``"memory"``, falling back
        to the first surface when none is labelled. Raises ``ValueError`` when
        the scope has no surfaces — that shape cannot satisfy a write."""
        for s in self.surfaces:
            if s.label == "memory":
                return s.path
        if self.surfaces:
            return self.surfaces[0].path
        raise ValueError(f"AgentScope {self.name!r} has no surfaces")


def _build_surface(entry: object, agent_name: str) -> AgentSurface:
    """Parse one yaml surface mapping into an :class:`AgentSurface`.

    Raises ``ValueError`` with the offending agent name in the message when
    the entry is structurally invalid so operators see which block to fix.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"agents.{agent_name}.surfaces[*] must be a mapping; got {type(entry).__name__}")
    raw_path = entry.get("path")
    if not raw_path:
        raise ValueError(f"agents.{agent_name}.surfaces[*] is missing required 'path' field")
    return AgentSurface(
        path=Path(str(raw_path)),
        glob=str(entry.get("glob") or _DEFAULT_GLOB),
        label=str(entry.get("label") or ""),
    )


def _build_scope(name: str, entry: object) -> AgentScope:
    """Parse one ``agents.<name>`` block into an :class:`AgentScope`.

    Validates the surfaces field is a list (yaml typos like a bare scalar
    are caught here) and delegates per-surface validation to
    :func:`_build_surface`.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"agents.{name} must be a mapping; got {type(entry).__name__}")
    surfaces_raw = entry.get("surfaces")
    if not isinstance(surfaces_raw, list):
        raise ValueError(f"agents.{name}.surfaces must be a list; got {type(surfaces_raw).__name__}")
    surfaces = tuple(_build_surface(s, name) for s in surfaces_raw)
    harness = str(entry.get("harness") or "")
    return AgentScope(name=name, surfaces=surfaces, harness=harness)


def load_agent_scopes(config: dict[str, object] | None = None) -> dict[str, AgentScope]:
    """Parse ``kairix.config.yaml``'s ``agents:`` block into a name → scope map.

    Returns an empty dict when ``config`` is None or carries no ``agents:``
    key — callers fall through to :func:`get_agent_scope`'s synthesis path.

    Raises ``ValueError`` when an agent entry has malformed surfaces (missing
    ``path``, surfaces not a list, etc.) — fail-fast at load time beats a
    silent fallback that hides the bad config.
    """
    if config is None:
        return {}
    agents_raw = config.get("agents")
    if not agents_raw:
        return {}
    if not isinstance(agents_raw, dict):
        raise ValueError(f"agents must be a mapping of name → config; got {type(agents_raw).__name__}")
    return {name: _build_scope(name, entry) for name, entry in agents_raw.items()}


def _synthesise_from_defaults(name: str, defaults: dict[str, object]) -> AgentScope:
    """Build a fallback scope from the ``agent_defaults`` block.

    Two surfaces: ``memory_root/<name>`` labelled ``"memory"`` (always
    included) and ``workspace_root/<name>`` labelled ``"workspace"`` (only
    when the directory exists on disk — operators with no workspace tree
    shouldn't see a phantom surface).
    """
    glob = str(defaults.get("glob") or _DEFAULT_GLOB)
    memory_root = defaults.get("memory_root")
    workspace_root = defaults.get("workspace_root")

    surfaces: list[AgentSurface] = []
    if memory_root:
        surfaces.append(AgentSurface(path=Path(str(memory_root)) / name, glob=glob, label="memory"))
    if workspace_root:
        workspace_path = Path(str(workspace_root)) / name
        if workspace_path.is_dir():
            surfaces.append(AgentSurface(path=workspace_path, glob=glob, label="workspace"))
    return AgentScope(name=name, surfaces=tuple(surfaces))


def get_agent_scope(
    name: str,
    *,
    config: dict[str, object] | None = None,
    document_root: Path | None = None,
) -> AgentScope:
    """Return the :class:`AgentScope` for ``name``.

    Resolution order:

      1. Explicit ``agents.<name>`` config entry → returned verbatim.
      2. ``agent_defaults`` block present → synthesise a scope and emit a
         one-line ``logger.warning`` so operators see the drift and can
         commit explicit config via ``kairix onboard agent --name <name>``.
      3. Neither present → fall back to ``{document_root}/04-Agent-Knowledge/<name>``
         (the conventional layout) so kairix works out-of-the-box on
         fresh deployments without explicit config.

    ``config`` and ``document_root`` are test seams; production callers
    leave them ``None`` and the resolver reads ``kairix.config.yaml``
    and ``kairix.paths.document_root()`` respectively.
    """
    scopes = load_agent_scopes(config) if config is not None else {}
    if name in scopes:
        return scopes[name]

    defaults = (config or {}).get("agent_defaults") if config is not None else None
    if isinstance(defaults, dict) and defaults:
        logger.warning(
            "No explicit agents.%s entry — synthesising scope from agent_defaults; "
            "run `kairix onboard agent --name %s` to commit explicit config",
            name,
            name,
        )
        return _synthesise_from_defaults(name, defaults)

    if document_root is None:
        from kairix.paths import document_root as _doc_root

        document_root = _doc_root()
    logger.warning(
        "No explicit agents.%s entry and no agent_defaults block — using built-in "
        "default at %s/04-Agent-Knowledge/%s. Run `kairix onboard agent --name %s` "
        "to commit explicit config.",
        name,
        document_root,
        name,
        name,
    )
    return AgentScope(
        name=name,
        surfaces=(
            AgentSurface(
                path=Path(document_root) / "04-Agent-Knowledge" / name,
                glob=_DEFAULT_GLOB,
                label="memory",
            ),
        ),
    )
