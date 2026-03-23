"""
First-mention [[wikilink]] injection for Mnemosyne.

Injects [[wikilinks]] on the first meaningful mention of each known entity
in agent-written vault markdown files.

Rules:
1. First mention per file only — once linked, skip subsequent mentions
2. Skip if already a wikilink: [[Entity]] stays as-is
3. Skip if inside code block (``` or `) or frontmatter (--- block)
4. Skip if inside a wikilink already: [[some text with Entity inside]]
5. Case-sensitive match for proper nouns; case-insensitive for aliases
6. Whole-word matching only — "AcmeHealth" matches "AcmeHealth" but not "AcmeHealthPlus"
7. Don't inject entity on its own file (link on ENTITY'S OWN FILE excluded)
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from mnemosyne.wikilinks.resolver import WikiEntity

# Injection log path
_LOG_PATH = "/data/mnemosyne/wikilinks-log.jsonl"

# Eligible base paths for injection
_ELIGIBLE_PREFIXES = (
    "/data/workspaces/",
    "/data/obsidian-vault/04-Agent-Knowledge/",
    "/data/obsidian-vault/01-Projects/",
    "/data/obsidian-vault/02-Areas/",
    "/data/obsidian-vault/05-Knowledge/",
)

_INELIGIBLE_SUBSTRINGS = (
    "/archive/",
    "/archived/",
    "/home/openclaw/.cache/shape/",
)

_MAX_FILE_SIZE = 500 * 1024  # 500 KB


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------


def should_inject(path: str) -> bool:
    """
    Return True if this file is agent-written content eligible for injection.

    Eligible paths (agent-written):
    - /data/workspaces/*/memory/*.md
    - /data/obsidian-vault/04-Agent-Knowledge/**/*.md
    - /data/obsidian-vault/01-Projects/**/*.md  (but NOT imported/archived subfolders)
    - /data/obsidian-vault/02-Areas/**/*.md
    - /data/obsidian-vault/05-Knowledge/**/*.md

    NOT eligible (imported/future backlog):
    - /home/openclaw/.cache/shape/  (PDFs, raw imports)
    - Any path containing /archive/ or /archived/
    - Files > 500KB
    """
    if not path.endswith(".md"):
        return False

    # Check for ineligible substrings
    for substr in _INELIGIBLE_SUBSTRINGS:
        if substr in path:
            return False

    # Check file size if it exists
    try:
        if os.path.getsize(path) > _MAX_FILE_SIZE:
            return False
    except OSError:
        pass  # file may not exist yet; defer to caller

    # workspace memory files: /data/workspaces/*/memory/*.md
    if path.startswith("/data/workspaces/"):
        parts = path[len("/data/workspaces/") :].split("/")
        # parts[0] = workspace name, parts[1] = 'memory', parts[-1] = filename
        if len(parts) >= 3 and parts[1] == "memory":
            return True
        return False

    # Obsidian vault paths
    for prefix in _ELIGIBLE_PREFIXES[1:]:  # skip /data/workspaces/ already handled
        if path.startswith(prefix):
            return True

    return False


# ---------------------------------------------------------------------------
# Core injection logic
# ---------------------------------------------------------------------------


def inject_wikilinks(
    content: str,
    entities: list[WikiEntity],
    source_path: str = "",
) -> tuple[str, list[str]]:
    """
    Inject [[wikilinks]] on first meaningful mention of each entity.

    Returns:
        (modified_content, list_of_injected_entity_names)
    """
    injected_names: list[str] = []

    # Parse content into segments: frontmatter, code blocks, text
    segments = _parse_segments(content)

    # Track which entities have been linked (first-mention rule)
    linked_entities: set[str] = set()

    # Pre-scan content for already-linked entities to respect first-mention
    already_linked = _find_already_linked(content)
    linked_entities.update(already_linked)

    # Determine entities to skip (entity on its own page)
    skip_entities = _entities_for_own_page(source_path, entities)

    # Build sorted entity list: longer names first (avoids partial replacements)
    sorted_entities = sorted(entities, key=lambda e: -max(len(t) for t in e.all_triggers()))

    # Process each text segment
    result_segments: list[str] = []
    for seg_type, seg_text in segments:
        if seg_type != "text":
            result_segments.append(seg_text)
            continue

        # Process text segment: inject wikilinks
        modified, newly_linked = _inject_in_text(seg_text, sorted_entities, linked_entities, skip_entities)
        linked_entities.update(newly_linked)
        injected_names.extend(newly_linked)
        result_segments.append(modified)

    return "".join(result_segments), injected_names


def _entities_for_own_page(source_path: str, entities: list[WikiEntity]) -> set[str]:
    """
    Return names of entities whose vault file is source_path.
    These should not be linked on their own page.
    """
    if not source_path:
        return set()

    # Normalise source_path to a relative vault path for comparison
    vault_root = "/data/obsidian-vault/"
    if source_path.startswith(vault_root):
        rel = source_path[len(vault_root) :]
    else:
        rel = source_path

    skip: set[str] = set()
    for entity in entities:
        ep = entity.vault_path.rstrip("/")
        # Check if source_path is the entity's vault file or inside the entity's folder
        if rel == ep or rel.startswith(ep + "/") or rel.startswith(ep):
            for trigger in entity.all_triggers():
                skip.add(trigger)
    return skip


def _find_already_linked(content: str) -> set[str]:
    """Return set of display names that are already wikilinked in content."""
    linked: set[str] = set()
    # Match [[target]] or [[target|display]]
    for m in re.finditer(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", content):
        target = m.group(1)
        display = m.group(2)
        linked.add(target)
        if display:
            linked.add(display)
    return linked


def _parse_segments(content: str) -> list[tuple[str, str]]:
    """
    Split content into typed segments:
      - ("frontmatter", text) — YAML front matter between leading ---
      - ("fenced_code", text) — ``` ... ``` blocks
      - ("text", text) — regular markdown text

    Inline code (backtick spans) are handled in _inject_in_text.
    """
    segments: list[tuple[str, str]] = []
    pos = 0
    n = len(content)

    # Check for frontmatter (must be at very start of file)
    if content.startswith("---"):
        # Find closing ---
        end = content.find("\n---", 3)
        if end != -1:
            # Include the closing --- and trailing newline
            fm_end = end + 4  # len("\n---") = 4
            if fm_end < n and content[fm_end] == "\n":
                fm_end += 1
            segments.append(("frontmatter", content[pos:fm_end]))
            pos = fm_end

    # Process remaining content
    while pos < n:
        # Find next fenced code block
        fence_match = re.search(r"^```", content[pos:], re.MULTILINE)
        if fence_match is None:
            segments.append(("text", content[pos:]))
            break

        fence_start = pos + fence_match.start()
        if fence_start > pos:
            segments.append(("text", content[pos:fence_start]))

        # Find closing fence
        close_match = re.search(r"^```", content[fence_start + 3 :], re.MULTILINE)
        if close_match is None:
            # Unclosed fence — treat rest as code
            segments.append(("fenced_code", content[fence_start:]))
            pos = n
        else:
            fence_end = fence_start + 3 + close_match.end()
            segments.append(("fenced_code", content[fence_start:fence_end]))
            pos = fence_end

    return segments


def _inject_in_text(
    text: str,
    sorted_entities: list[WikiEntity],
    already_linked: set[str],
    skip_entities: set[str],
) -> tuple[str, list[str]]:
    """
    Process a plain-text segment and inject wikilinks for first-mention of each entity.

    Handles:
    - Inline code spans (backtick) — skip
    - Existing [[wikilinks]] — skip content inside
    - Whole-word matching
    - First mention only (tracked via already_linked set, mutated in place)

    Returns modified text and list of newly injected entity names.
    """
    newly_linked: list[str] = []
    result = text

    for entity in sorted_entities:
        triggers = entity.all_triggers()

        # Check if any trigger is already linked
        if any(t in already_linked for t in triggers):
            continue

        # Find first matchable trigger in text
        replacement_made = False
        for idx, trigger in enumerate(triggers):
            if trigger in skip_entities:
                continue

            # Case-sensitive for primary name (index 0), case-insensitive for aliases
            is_alias = idx > 0
            pattern_flags = re.IGNORECASE if is_alias else 0

            # Whole-word boundary pattern
            escaped = re.escape(trigger)
            pattern = rf"(?<!\w){escaped}(?!\w)"

            for m in re.finditer(pattern, result, flags=pattern_flags):
                match_start = m.start()
                match_end = m.end()

                # Skip if inside inline code or existing wikilink
                if _is_in_code_or_link(result, match_start):
                    continue

                # Replace only the first valid match
                result = result[:match_start] + entity.link + result[match_end:]
                newly_linked.append(entity.name)
                already_linked.update(entity.all_triggers())
                replacement_made = True
                break

            if replacement_made:
                break

    return result, newly_linked


def _is_in_code_or_link(text: str, pos: int) -> bool:
    """
    Return True if pos is inside an inline code span (`...`) or inside [[...]].
    Uses a simple scan from start of string — not perfect for pathological cases
    but reliable for standard markdown.
    """
    # Check inline code: count unescaped backticks before pos
    # We look for balanced backtick spans
    before = text[:pos]

    # Check inside [[...]]
    # Find last [[ before pos, check if there's a ]] after it but before pos
    last_open = before.rfind("[[")
    if last_open != -1:
        close_in_before = before.find("]]", last_open)
        if close_in_before == -1:
            # We're inside a [[...]]
            return True

    # Check inside backtick code span
    # Count backticks before pos; if odd number of single backticks, we're inside a span
    # Simple heuristic: count ` chars; if inside a span, count is odd
    backtick_count = before.count("`")
    if backtick_count % 2 == 1:
        return True

    return False


# ---------------------------------------------------------------------------
# File-level injection
# ---------------------------------------------------------------------------


def inject_file(path: str, entities: list[WikiEntity], dry_run: bool = False) -> list[str]:
    """
    Read file, inject wikilinks, write back (unless dry_run).
    Returns list of injected entity names.

    Skips:
    - Non-.md files
    - Files > 500KB
    - Binary files
    """
    p = Path(path)
    if p.suffix != ".md":
        return []

    try:
        size = p.stat().st_size
    except OSError:
        return []

    if size > _MAX_FILE_SIZE:
        return []

    try:
        content = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    modified, injected = inject_wikilinks(content, entities, source_path=path)

    if injected and not dry_run:
        p.write_text(modified, encoding="utf-8")
        _log_injection(path, injected, dry_run=False)
    elif injected and dry_run:
        _log_injection(path, injected, dry_run=True)

    return injected


def _log_injection(file_path: str, injected: list[str], dry_run: bool) -> None:
    """Append an entry to the injection log."""
    # Use relative vault path when possible
    vault_root = "/data/obsidian-vault/"
    rel_path = file_path
    if file_path.startswith(vault_root):
        rel_path = file_path[len(vault_root) :]

    entry = {
        "ts": int(time.time()),
        "file": rel_path,
        "injected": injected,
        "dry_run": dry_run,
    }
    try:
        log_path = Path(_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # log failure is non-fatal
