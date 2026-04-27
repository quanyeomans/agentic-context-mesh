"""Boilerplate detection and licence filtering for reference library normalisation."""

from __future__ import annotations

from pathlib import Path

from kairix.reflib.sources import SourceDef

# Exact filename matches (case-insensitive) — repo boilerplate, not content
BOILERPLATE_FILENAMES: frozenset[str] = frozenset(
    {
        "contributing.md",
        "code_of_conduct.md",
        "security.md",
        "changelog.md",
        "changes.md",
        "codeowners",
        "license",
        "license.md",
        "license.txt",
        "licence",
        "licence.md",
        "copying",
        "pull_request_template.md",
        "issue_template.md",
        "bug_report.md",
        "feature_request.md",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".prettierrc",
        ".eslintrc.md",
        "dependabot.yml",
        "renovate.json",
    }
)

# Path substring matches — directories to skip entirely
BOILERPLATE_PATH_PATTERNS: tuple[str, ...] = (
    ".github/",
    ".github\\",
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "vendor/",
    "dist/",
    "build/",
    "_build/",
    ".next/",
    ".docusaurus/",
    ".cache/",
    "site-packages/",
)

# Translation directories — large volume, not English content
TRANSLATION_PATTERNS: tuple[str, ...] = (
    "translations/",
    "i18n/",
    "/cn/",
    "/zh/",
    "/ja/",
    "/ko/",
    "/fr/",
    "/de/",
    "/es/",
    "/pt/",
    "/ru/",
    "/ar/",
    "/hi/",
    "/it/",
    "/tr/",
    "/pl/",
    "/vi/",
    "/th/",
    "/id/",
)


def is_boilerplate_filename(path: Path) -> bool:
    """Check if a file is repo boilerplate by filename."""
    return path.name.lower() in BOILERPLATE_FILENAMES


def is_boilerplate_path(path: Path) -> bool:
    """Check if a file is in a boilerplate directory."""
    path_str = str(path)
    return any(pattern in path_str for pattern in BOILERPLATE_PATH_PATTERNS)


def is_translation(path: Path) -> bool:
    """Check if a file is in a translation directory."""
    path_str = str(path).lower()
    return any(pattern in path_str for pattern in TRANSLATION_PATTERNS)


def matches_source_excludes(path: Path, source: SourceDef) -> bool:
    """Check if a file matches per-source exclude patterns."""
    if not source.exclude_patterns:
        return False
    path_str = str(path)
    return any(pattern in path_str for pattern in source.exclude_patterns)


def should_include(path: Path, source: SourceDef | None = None) -> bool:
    """Return True if a file should be included in the normalised output.

    Checks global boilerplate, translation, and per-source excludes.
    """
    if is_boilerplate_filename(path):
        return False
    if is_boilerplate_path(path):
        return False
    if is_translation(path):
        return False
    if source and matches_source_excludes(path, source):
        return False
    return True


def filter_collection(
    files: list[Path],
    source: SourceDef | None = None,
) -> list[Path]:
    """Filter a list of files, returning only those that pass all checks."""
    return [f for f in files if should_include(f, source)]
