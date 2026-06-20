"""Filesystem walk + frontmatter parse + dedup for the skills connector.

Kept private to the plugin (``kairix.connectors.skills.*``) — these are
the implementation details that nudge the ``~/.claude`` walk, the
YAML-frontmatter parse, and the dedup-by-name into one place so the
connector body stays thin. Public ``__init__.py`` re-exports nothing from
this module.

The walk covers, on the host the kairix instance runs on:

  * ``<claude_root>/plugins/cache/**/skills/<name>/SKILL.md``
  * ``<claude_root>/plugins/cache/**/commands/<cmd>.md``
  * ``<claude_root>/plugins/cache/**/agents/<agent>.md``
  * ``<claude_root>/skills/*.md`` (flat files)

For each artefact it parses the ``---``-delimited YAML frontmatter
(``name`` + ``description``), takes the body as everything after the
frontmatter block, and **dedups by ``name``, preferring the higher
version** (the plugin cache holds multiple versions of the same plugin —
e.g. ``4.0.0`` and ``6.0.3`` — so naive ingest would surface stale
duplicates). Version is read from the ``plugins/cache/<mkt>/<plugin>/
<version>/`` path segment; flat ``~/.claude/skills`` files have no version
(treated as the lowest, so a cached version of the same name wins).

Graceful degrade: a missing ``claude_root`` yields an empty iterator,
never an error — matches the deployment reality where the production VM
has no ``~/.claude`` tree (design §3.4). Malformed frontmatter / missing
``name`` skips that one item (per-item isolation, design §7).

Per F35 this module imports only stdlib + ``yaml``; no reach into other
connectors or the extractor layer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory-name → capability kind. The three plugin-cache subdirs plus
# the flat ``~/.claude/skills`` tree all map onto one of these kinds.
_DIR_TO_KIND: dict[str, str] = {
    "skills": "skill",
    "commands": "command",
    "agents": "agent",
}

# The flat ``<claude_root>/skills/*.md`` tree is all skills.
_FLAT_SKILLS_DIRNAME = "skills"
_FLAT_KIND = "skill"

# Marker the SKILL.md convention uses for the per-skill directory shape.
_SKILL_FILENAME = "SKILL.md"

# Lowest-possible version sentinel for artefacts with no version segment
# (flat ``~/.claude/skills`` files) — any real cached version outranks it.
_NO_VERSION = ""


@dataclass(frozen=True)
class SkillArtefact:
    """One parsed capability artefact from the ``~/.claude`` tree.

    Frozen per F42 — the typed shape that crosses the boundary between
    the walk and the connector body. ``version`` is the plugin-cache
    version segment (``""`` for flat files); ``source_path`` is the
    absolute path the artefact was read from, used for ``fetch`` /
    ``metadata_for`` mtime.
    """

    name: str
    kind: str
    description: str
    body: str
    source_path: Path
    version: str


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split ``---`` YAML frontmatter from the body.

    Returns ``(frontmatter_dict, body)``. A file with no leading ``---``
    block returns ``({}, <whole-text>)``. Corrupt YAML returns
    ``({}, <body-after-block>)`` — best-effort, never raises. Tolerant of
    a leading BOM and surrounding whitespace.
    """
    stripped = text.lstrip("﻿").lstrip()
    if not stripped.startswith("---"):
        return {}, text
    after = stripped[3:]
    end_marker = after.find("\n---")
    if end_marker == -1:
        return {}, text
    block = after[:end_marker]
    body = after[end_marker + len("\n---") :].lstrip("\n")
    try:
        import yaml

        parsed = yaml.safe_load(block)
    except Exception:
        # Best-effort parse — corrupt YAML frontmatter is skipped, never fatal.
        return {}, body
    if isinstance(parsed, dict):
        return parsed, body
    return {}, body


def _str_field(front: dict[str, object], key: str) -> str:
    value = front.get(key)
    if isinstance(value, str):
        return value.strip()
    return ""


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    """Sort key that orders dotted numeric versions correctly.

    ``"6.0.3"`` outranks ``"4.0.0"`` AND ``"10.0.0"`` outranks
    ``"9.0.0"`` (pure lexical comparison gets the latter wrong). Falls
    back to a lexical tail for non-numeric suffixes so the comparison is
    total and deterministic.
    """
    numeric: list[int] = []
    for segment in version.split("."):
        if segment.isdigit():
            numeric.append(int(segment))
        else:
            break
    return tuple(numeric), version


def _prefers(candidate: SkillArtefact, incumbent: SkillArtefact) -> bool:
    """True when ``candidate`` should replace ``incumbent`` for one name.

    Higher version wins. This is the dedup rule the connector's
    "prefer the higher version" contract pins; the sabotage-proof in
    ``test_fs.py`` flips ``>`` to ``<`` here and confirms the dedup test
    goes red.
    """
    return _version_key(candidate.version) > _version_key(incumbent.version)


def _iter_kind_files(plugins_cache: Path) -> Iterator[tuple[Path, str, str]]:
    """Yield ``(file_path, kind, version)`` for every artefact under ``plugins/cache``.

    Walks every ``skills`` / ``commands`` / ``agents`` directory anywhere
    in the cache tree. ``skills`` directories use the ``<name>/SKILL.md``
    shape; ``commands`` / ``agents`` use flat ``<name>.md`` files. The
    version is the cache layout's ``<version>`` segment — the parent of the
    kind directory (``plugins/cache/<mkt>/<plugin>/<version>/<kind>/...``).
    """
    if not plugins_cache.is_dir():
        return
    for kind_dir, kind in _DIR_TO_KIND.items():
        for kind_root in sorted(plugins_cache.rglob(kind_dir)):
            if not kind_root.is_dir():
                continue
            version = kind_root.parent.name
            if kind == _FLAT_KIND:
                files = sorted(kind_root.glob(f"*/{_SKILL_FILENAME}"))
            else:
                files = sorted(kind_root.glob("*.md"))
            yield from ((p, kind, version) for p in files)


def _iter_flat_skills(claude_root: Path) -> Iterator[tuple[Path, str, str]]:
    """Yield ``(file_path, "skill", "")`` for flat ``<claude_root>/skills/*.md``.

    Flat files carry no plugin-cache version segment, so their version is
    :data:`_NO_VERSION` — a cached version of the same name outranks them.
    """
    flat = claude_root / _FLAT_SKILLS_DIRNAME
    if not flat.is_dir():
        return
    yield from ((p, _FLAT_KIND, _NO_VERSION) for p in sorted(flat.glob("*.md")))


def _artefact_from_file(path: Path, kind: str, version: str) -> SkillArtefact | None:
    """Parse one file into a :class:`SkillArtefact`, or ``None`` to skip it.

    Skips files with unreadable bytes, no frontmatter, or no ``name`` —
    per-item isolation so one corrupt artefact never sinks the walk.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("skills connector: cannot read %s: %s", path, exc)
        return None
    front, body = _parse_frontmatter(text)
    name = _str_field(front, "name")
    if not name:
        logger.info("skills connector: skipping %s — no 'name' in frontmatter", path)
        return None
    return SkillArtefact(
        name=name,
        kind=kind,
        description=_str_field(front, "description"),
        body=body.strip(),
        source_path=path,
        version=version,
    )


def iter_skill_artefacts(*, claude_root: Path) -> Iterator[SkillArtefact]:
    """Walk ``claude_root`` and yield one deduped :class:`SkillArtefact` per name.

    Combines the plugin-cache walk (``plugins/cache/**/{skills,commands,
    agents}``) with the flat ``<claude_root>/skills/*.md`` tree, parses
    each artefact's frontmatter, and dedups by ``name`` keeping the higher
    version. A missing ``claude_root`` yields nothing (graceful degrade).
    Emission order is sorted by name for determinism.
    """
    if not claude_root.is_dir():
        return iter(())
    plugins_cache = claude_root / "plugins" / "cache"
    best: dict[str, SkillArtefact] = {}
    for path, kind, version in [*_iter_kind_files(plugins_cache), *_iter_flat_skills(claude_root)]:
        artefact = _artefact_from_file(path, kind, version)
        if artefact is None:
            continue
        incumbent = best.get(artefact.name)
        if incumbent is None or _prefers(artefact, incumbent):
            best[artefact.name] = artefact
    return iter(best[name] for name in sorted(best))
