"""
Mnemosyne entity resolver — canonical entity lookup.

Provides resolve_canonical() to normalise alias names to their canonical
entity row.  Used by the extraction pipeline (to prevent duplicate entity
rows) and the wikilink injector (to normalise alias → canonical link text).
"""

from __future__ import annotations

import sqlite3


def resolve_canonical(name: str, db: sqlite3.Connection) -> dict | None:
    """
    Given a name (may be alias or canonical), return the canonical entity row dict.
    Returns None if no entity found.

    Logic:
    1. Look up exact name match in entities table
    2. If row found and canonical_id is NULL → it IS the canonical, return it
    3. If row found and canonical_id is set → fetch and return the canonical row
    4. If no exact match → try case-insensitive match
    5. If still no match → return None

    Args:
        name: Entity name string — may be a canonical name or alias.
        db:   Open sqlite3.Connection (row_factory should be sqlite3.Row).

    Returns:
        A dict of the canonical entity row fields, or None if not found.
    """
    # Preserve row_factory for results; we'll cast to dict manually
    row_factory_orig = db.row_factory
    db.row_factory = sqlite3.Row

    try:
        # Step 1 & 2: Exact name match
        row = db.execute(
            "SELECT * FROM entities WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()

        if row is not None:
            if row["canonical_id"] is None:
                # This IS the canonical entity
                return dict(row)
            else:
                # It's an alias — follow canonical_id
                canonical = db.execute(
                    "SELECT * FROM entities WHERE id = ? LIMIT 1",
                    (row["canonical_id"],),
                ).fetchone()
                if canonical is not None:
                    return dict(canonical)
                # Dangling canonical_id — return the row itself as best effort
                return dict(row)

        # Step 4: Case-insensitive match
        ci_row = db.execute(
            "SELECT * FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (name,),
        ).fetchone()

        if ci_row is not None:
            if ci_row["canonical_id"] is None:
                return dict(ci_row)
            else:
                canonical = db.execute(
                    "SELECT * FROM entities WHERE id = ? LIMIT 1",
                    (ci_row["canonical_id"],),
                ).fetchone()
                if canonical is not None:
                    return dict(canonical)
                return dict(ci_row)

        # Step 5: No match
        return None

    finally:
        db.row_factory = row_factory_orig
