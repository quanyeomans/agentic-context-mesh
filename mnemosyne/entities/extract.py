"""
Mnemosyne entity extractor — NER pipeline for vault content.

Rules-first approach: vault structure matching + known entity lookup +
Title Case heuristic. LLM (gpt-4o-mini) used only for ambiguous cases
when rules find fewer than 3 entities.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mnemosyne.entities.stop_entities import is_stop_entity

VAULT_ROOT = Path("/data/obsidian-vault")
AGENT_ROOTS: list[str] = [
    "/data/workspaces/builder/memory",
    "/data/workspaces/shape/memory",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ExtractedEntity:
    """An entity candidate extracted from a vault document."""

    name: str
    entity_type: str  # person, org, project, framework, product, place
    confidence: float  # 0.0-1.0
    context: str  # surrounding sentence for disambiguation
    source_path: str  # vault file path


# ---------------------------------------------------------------------------
# Vault structure index (cached per process)
# ---------------------------------------------------------------------------

_vault_names_cache: set[str] | None = None


def _get_vault_names(vault_root: Path = VAULT_ROOT) -> set[str]:
    """
    Return the set of folder names and file stems under the vault root.

    Cached in-process for performance. Cache is invalidated if vault_root changes
    (only matters in tests that monkeypatch VAULT_ROOT).
    """
    global _vault_names_cache
    # Always recompute in tests (no persistent cache between test runs)
    if _vault_names_cache is not None and vault_root == VAULT_ROOT:
        return _vault_names_cache

    names: set[str] = set()
    try:
        for path in vault_root.rglob("*"):
            if path.name.startswith("."):
                continue
            if path.is_dir():
                names.add(path.name)
            elif path.suffix == ".md":
                names.add(path.stem)
    except (PermissionError, OSError):
        pass

    if vault_root == VAULT_ROOT:
        _vault_names_cache = names
    return names


def _invalidate_vault_cache() -> None:
    """Force re-scan of vault names on next call (useful in tests)."""
    global _vault_names_cache
    _vault_names_cache = None


# ---------------------------------------------------------------------------
# Entity stub frontmatter helpers
# ---------------------------------------------------------------------------


def read_stub_aliases(path: str | Path) -> list[str]:
    """
    Read the ``aliases:`` YAML list from an entity stub file's frontmatter.

    Supports YAML front matter delimited by ``---`` at the top of the file.
    Returns an empty list if the file is missing, has no frontmatter, or has
    no ``aliases`` key.  Never raises — callers may safely ignore errors.

    Example frontmatter::

        ---
        type: concept
        name: SMEC
        entity-id: smec
        aliases: [SME-C, SME&C]
        ---

    Args:
        path: Absolute or relative path to a ``.md`` stub file.

    Returns:
        List of alias strings (may be empty).
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return []

    if not text.startswith("---"):
        return []

    end = text.find("\n---", 3)
    if end == -1:
        return []

    frontmatter = text[3:end].strip()

    # Parse only the `aliases:` line — avoid a full YAML dependency.
    # Handles two forms:
    #   aliases: [SME-C, SME&C]          (inline list)
    #   aliases:                          (empty, possibly followed by
    #     - SME-C                          block-style items on next lines)
    #     - SME&C
    aliases: list[str] = []
    lines = frontmatter.splitlines()
    in_aliases_block = False

    for line in lines:
        stripped = line.strip()

        # Inline list form: `aliases: [a, b, c]`
        m_inline = re.match(r"^aliases\s*:\s*\[([^\]]*)\]", stripped)
        if m_inline:
            items = m_inline.group(1)
            aliases = [item.strip().strip("'\"") for item in items.split(",") if item.strip()]
            in_aliases_block = False
            continue

        # Block-list header: `aliases:` (nothing after colon)
        m_block_header = re.match(r"^aliases\s*:\s*$", stripped)
        if m_block_header:
            in_aliases_block = True
            continue

        # Continuation of block list: `  - value`
        if in_aliases_block:
            m_item = re.match(r"^-\s+(.+)$", stripped)
            if m_item:
                aliases.append(m_item.group(1).strip().strip("'\""))
                continue
            # Any non-list line ends the aliases block
            if stripped and not stripped.startswith("-"):
                in_aliases_block = False

    return [a for a in aliases if a]


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def _get_context(text: str, start: int, end: int, window: int = 150) -> str:
    """Return a context snippet around a match position."""
    ctx_start = max(0, start - window)
    ctx_end = min(len(text), end + window)
    return text[ctx_start:ctx_end].strip()


def _split_sentences(text: str) -> list[tuple[str, int]]:
    """
    Split text into (sentence, start_offset) pairs.
    Simple heuristic: split on '. ', '? ', '! ', newlines.
    """
    pattern = re.compile(r"(?<=[.!?])\s+|(?<=\n)\s*")
    sentences: list[tuple[str, int]] = []
    last = 0
    for m in pattern.finditer(text):
        sentence = text[last : m.start()].strip()
        if sentence:
            sentences.append((sentence, last))
        last = m.end()
    remainder = text[last:].strip()
    if remainder:
        sentences.append((remainder, last))
    return sentences


def _is_sentence_start(text: str, match_start: int) -> bool:
    """
    Return True if the match starts at the beginning of a sentence.

    A match is at sentence start if the only content before it on the
    current "line" (after last newline or sentence-ending punctuation)
    is whitespace/punctuation.
    """
    preceding = text[:match_start]
    # Find the last sentence boundary
    boundary = max(
        preceding.rfind("\n"),
        preceding.rfind(". "),
        preceding.rfind("! "),
        preceding.rfind("? "),
    )
    between = preceding[boundary + 1 :].strip() if boundary >= 0 else preceding.strip()
    return len(between) == 0


# ---------------------------------------------------------------------------
# Rule-based extraction
# ---------------------------------------------------------------------------


def extract_rules_based(text: str, source_path: str, vault_root: Path = VAULT_ROOT) -> list[ExtractedEntity]:
    """
    Fast rule-based NER pass.

    Rules (in priority order):
    1. Vault structure match: scan vault_root folder names and file titles.
       If a phrase in text matches a vault name (e.g. "Acme-Corp", "Mnemosyne"),
       extract it with high confidence.
    2. Known entity lookup: check against entities.db names. Requires caller to
       pass db separately — use extract_rules_based_with_db() for this.
    3. Capitalised proper noun heuristic: 2+ consecutive Title Case words not at
       sentence start → extract as candidate (low confidence 0.4).

    Returns high-confidence (rules 1+2) and low-confidence (rule 3) candidates.
    Note: rule 2 requires a db; use extract_rules_based_with_db() for full pipeline.
    """
    results: list[ExtractedEntity] = []
    seen_names: set[str] = set()

    # Rule 1: Vault structure match (confidence 0.9)
    vault_names = _get_vault_names(vault_root)
    # Build a sorted list (longer names first to avoid substring conflicts)
    sorted_names = sorted(vault_names, key=len, reverse=True)
    for name in sorted_names:
        if len(name) < 2:
            continue
        # Word-boundary match, case-insensitive
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        for m in pattern.finditer(text):
            # Use original casing from vault (preserves proper noun form)
            canonical = name  # vault name is authoritative
            if canonical.lower() in (s.lower() for s in seen_names):
                continue
            seen_names.add(canonical.lower())
            context = _get_context(text, m.start(), m.end())
            results.append(
                ExtractedEntity(
                    name=canonical,
                    entity_type=_guess_type_from_vault(canonical, vault_root),
                    confidence=0.9,
                    context=context,
                    source_path=source_path,
                )
            )
            break  # one result per vault name

    return results


def extract_rules_based_with_db(
    text: str,
    source_path: str,
    db: sqlite3.Connection,
    vault_root: Path = VAULT_ROOT,
) -> list[ExtractedEntity]:
    """
    Full rule-based NER pass including known-entity lookup from DB.

    Combines vault structure matching, DB entity name lookup, and
    Title Case heuristic.
    """
    results: list[ExtractedEntity] = []
    seen_names: set[str] = set()

    # Rule 1: Vault structure match (confidence 0.9)
    vault_names = _get_vault_names(vault_root)
    sorted_vault_names = sorted(vault_names, key=len, reverse=True)
    for name in sorted_vault_names:
        if len(name) < 2:
            continue
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        for m in pattern.finditer(text):
            canonical = name
            key = canonical.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            context = _get_context(text, m.start(), m.end())
            results.append(
                ExtractedEntity(
                    name=canonical,
                    entity_type=_guess_type_from_vault(canonical, vault_root),
                    confidence=0.9,
                    context=context,
                    source_path=source_path,
                )
            )
            break

    # Rule 2: Known entity lookup from DB (confidence 0.95)
    try:
        # Fetch entity names and any alias names (stored as entity names with canonical_id set)
        db_rows = db.execute(
            "SELECT name, type FROM entities WHERE status = 'active' ORDER BY LENGTH(name) DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        db_rows = []

    for row in db_rows:
        entity_name = row[0]
        entity_type = _normalise_entity_type(row[1])
        if len(entity_name) < 2:
            continue
        if entity_name.lower() in seen_names:
            continue
        pattern = re.compile(r"\b" + re.escape(entity_name) + r"\b", re.IGNORECASE)
        for m in pattern.finditer(text):
            seen_names.add(entity_name.lower())
            context = _get_context(text, m.start(), m.end())
            results.append(
                ExtractedEntity(
                    name=entity_name,
                    entity_type=entity_type,
                    confidence=0.95,
                    context=context,
                    source_path=source_path,
                )
            )
            break

    # Rule 3: Capitalised proper noun heuristic (confidence 0.4)
    # Match 2+ consecutive Title Case words, but not at sentence start
    # Pattern: two or more Title Case words (each 2+ chars), optionally hyphenated
    title_case_pattern = re.compile(r"\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})+)\b")
    for m in title_case_pattern.finditer(text):
        phrase = m.group(0)
        key = phrase.lower()
        if key in seen_names:
            continue
        # Skip if at sentence start
        if _is_sentence_start(text, m.start()):
            continue
        # Skip common English words and article phrases
        if _is_common_phrase(phrase):
            continue
        # Skip generic business/tech terms and document artefacts
        if is_stop_entity(phrase):
            continue
        seen_names.add(key)
        context = _get_context(text, m.start(), m.end())
        results.append(
            ExtractedEntity(
                name=phrase,
                entity_type="org",  # default guess; LLM will refine
                confidence=0.4,
                context=context,
                source_path=source_path,
            )
        )

    return results


# Common English multi-word phrases to skip in heuristic
_COMMON_PHRASES = {
    "New South Wales",
    "South Australia",
    "Western Australia",
    "United States",
    "United Kingdom",
    "Hong Kong",
    "New Zealand",
    "Monday Morning",
    "Tuesday Morning",
    "Last Week",
    "Next Week",
    "Thank You",
    "Please Note",
}

_COMMON_WORDS = {
    "the",
    "and",
    "but",
    "for",
    "nor",
    "yet",
    "so",
    "also",
    "then",
    "thus",
    "hence",
    "however",
    "in",
    "on",
    "at",
    "by",
    "to",
    "of",
    "from",
}


def _is_common_phrase(phrase: str) -> bool:
    """Return True if phrase is a common English expression not worth extracting."""
    if phrase in _COMMON_PHRASES:
        return True
    words = phrase.split()
    # If all words are common stop words, skip
    if all(w.lower() in _COMMON_WORDS for w in words):
        return True
    return False


def _normalise_entity_type(db_type: str) -> str:
    """Map DB entity types to extractor entity types."""
    mapping = {
        "person": "person",
        "organisation": "org",
        "organization": "org",
        "org": "org",
        "decision": "concept",
        "concept": "concept",
        "project": "project",
        "framework": "framework",
        "product": "product",
        "place": "place",
    }
    return mapping.get(db_type.lower(), "org")


def _guess_type_from_vault(name: str, vault_root: Path = VAULT_ROOT) -> str:
    """Guess entity type from vault folder structure."""
    # Check if name appears under a typed folder
    type_map = {
        "Clients": "org",
        "People": "person",
        "Projects": "project",
        "Frameworks": "framework",
        "Products": "product",
        "Places": "place",
    }
    try:
        for path in vault_root.rglob(name):
            for folder_name, entity_type in type_map.items():
                if folder_name in str(path):
                    return entity_type
    except (PermissionError, OSError):
        pass
    return "org"  # default


# ---------------------------------------------------------------------------
# LLM-based extraction
# ---------------------------------------------------------------------------


def extract_llm(
    text: str,
    source_path: str,
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
    existing_entities: list[str] | None = None,
) -> list[ExtractedEntity]:
    """
    LLM-based NER for ambiguous cases.

    Uses Azure OpenAI chat completions to extract named entities.
    Truncates input to first 1500 words to control cost.
    If existing_entities provided, checks against them first to reduce hallucination.

    Args:
        text:              Full document text.
        source_path:       Source vault file path.
        api_key:           Azure OpenAI API key.
        endpoint:          Azure OpenAI endpoint URL.
        deployment:        Model deployment name (default: gpt-4o-mini).
        existing_entities: Known entity names to check against first.

    Returns:
        List of ExtractedEntity with confidence 0.75 (LLM-extracted).
    """
    import json
    import urllib.request

    # Truncate to first 1500 words
    words = text.split()
    truncated = " ".join(words[:1500])

    existing_hint = ""
    if existing_entities:
        existing_hint = (
            f"\n\nKnown entities to check against first: {', '.join(existing_entities[:50])}."
            " Reuse these exact names where applicable instead of creating variants."
        )

    system_prompt = (
        "You are a named entity extractor. Extract named entities from the text provided.\n"
        "Entity types: person, org, project, framework, product, place.\n"
        "Return JSON array only, no explanation. Format:\n"
        '[{"name": "Entity Name", "type": "org", "context": "surrounding sentence"}]\n'
        "Rules:\n"
        "- Only extract proper nouns (specific people, organisations, products, places)\n"
        "- Skip generic concepts, common English words, and pronouns\n"
        "- Include 1-2 sentence context for each entity" + existing_hint
    )

    user_prompt = f"Extract named entities from this text:\n\n{truncated}"

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
    }

    # Build request
    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview"
    request_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310  # nosec B310
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise RuntimeError(f"LLM NER request failed: {exc}") from exc

    # Parse JSON response
    entities: list[ExtractedEntity] = []
    try:
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = re.sub(r"^```[a-z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
        items = json.loads(content)
        for item in items:
            name = item.get("name", "").strip()
            entity_type = item.get("type", "org").strip()
            context = item.get("context", "").strip()
            if not name:
                continue
            entities.append(
                ExtractedEntity(
                    name=name,
                    entity_type=_normalise_entity_type(entity_type),
                    confidence=0.75,
                    context=context,
                    source_path=source_path,
                )
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        # If LLM response is malformed, return empty list rather than crashing
        pass

    return entities


# ---------------------------------------------------------------------------
# File-level extraction
# ---------------------------------------------------------------------------


def extract_file(
    path: str,
    db: sqlite3.Connection,
    use_llm: bool = False,
    api_key: str | None = None,
    endpoint: str | None = None,
    vault_root: Path = VAULT_ROOT,
) -> list[ExtractedEntity]:
    """
    Extract entities from a single vault file.

    Strategy:
    - Always run rules-based extraction (vault structure + DB entities + Title Case).
    - Run LLM only if use_llm=True AND rules found < 3 entities.

    Args:
        path:      Absolute path to the vault markdown file.
        db:        Open entities DB connection.
        use_llm:   Enable LLM fallback for ambiguous cases.
        api_key:   Azure OpenAI API key (required if use_llm=True).
        endpoint:  Azure OpenAI endpoint (required if use_llm=True).
        vault_root: Vault root for vault structure matching (tests may override).

    Returns:
        Combined list of extracted entities, deduplicated by name.
    """
    file_path = Path(path)
    if not file_path.exists():
        return []

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError):
        return []

    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]

    # Rules-based pass
    rules_results = extract_rules_based_with_db(text, path, db, vault_root=vault_root)

    # LLM pass only if use_llm=True and rules found < 3 entities
    if use_llm and len(rules_results) < 3:
        if not api_key or not endpoint:
            # Can't run LLM without credentials — return rules results only
            return rules_results

        existing_names = [e.name for e in rules_results]
        # Fetch all known entity names for de-hallucination hint
        try:
            db_rows = db.execute("SELECT name FROM entities WHERE status = 'active' LIMIT 100").fetchall()
            existing_names += [r[0] for r in db_rows]
        except sqlite3.OperationalError:
            pass

        llm_results = extract_llm(
            text,
            path,
            api_key=api_key,
            endpoint=endpoint,
            existing_entities=existing_names,
        )

        # Merge: add LLM results not already in rules results
        rules_names = {e.name.lower() for e in rules_results}
        for entity in llm_results:
            if entity.name.lower() not in rules_names:
                rules_results.append(entity)
                rules_names.add(entity.name.lower())

    return rules_results
