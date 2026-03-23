# QMD Compatibility

This tool writes directly to QMD's internal SQLite schema. Compatibility is pinned explicitly
and tested weekly via CI to prevent silent breakage on QMD updates.

## Tested versions

| qmd version | Status | Tested | Notes |
|---|---|---|---|
| 1.1.2 | ✅ Supported | 2026-03-22 | Initial release; schema verified against live index |

## Schema we depend on

### `content` table
```sql
CREATE TABLE content (
    hash       TEXT PRIMARY KEY,
    doc        TEXT NOT NULL,        -- full document text (NOT 'body' — common gotcha)
    created_at TEXT NOT NULL
);
```

### `documents` table
```sql
CREATE TABLE documents (
    id          INTEGER PRIMARY KEY,
    collection  TEXT NOT NULL,
    path        TEXT NOT NULL,
    title       TEXT,
    hash        TEXT NOT NULL,
    created_at  TEXT,
    modified_at TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);
```

### `content_vectors` table (we write this)
```sql
CREATE TABLE content_vectors (
    hash        TEXT NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    pos         INTEGER NOT NULL DEFAULT 0,
    model       TEXT NOT NULL,
    embedded_at TEXT NOT NULL,
    PRIMARY KEY (hash, seq)
);
```

### `vectors_vec` virtual table (we write this)
```sql
CREATE VIRTUAL TABLE vectors_vec USING vec0(
    hash_seq  TEXT PRIMARY KEY,          -- format: "{hash}_{seq}" e.g. "abc123_0"
    embedding float[1536] distance_metric=cosine
);
```

Vector encoding: **little-endian packed float32 binary** — `struct.pack('<Nf', *vector)`. 
sqlite-vec does NOT accept JSON arrays.

## Common gotcha: `content.doc` not `content.body`

QMD stores document text in `content.doc`. Early versions of this tool incorrectly assumed
`content.body` — this was caught by the startup schema validation. If you see a `SchemaVersionError`
mentioning `body`, you are running old code — update to the latest version.

## Upgrading QMD

When QMD releases a new version:

1. Check the QMD changelog for schema changes
2. Run the compatibility test suite:
   ```bash
   pytest tests/ -k "schema" -v
   ```
3. If tests pass with the new version: bump `QMD_TESTED_VERSION` in `schema.py` and `pyproject.toml`
4. Add a row to the tested versions table above
5. Open a PR with the version bump

The weekly CI workflow (`.github/workflows/qmd-compat.yml`) checks for QMD updates automatically
and opens an issue if the latest version differs from the pinned version.

## What breaks if QMD changes its schema

The schema validation in `schema.py` runs on every startup and checks `PRAGMA table_info()`
against the expected column sets. If columns are missing, the tool exits with code 2 and a
clear error message before touching any data. No silent corruption.

Dimension mismatches (e.g. someone ran `qmd embed` and created a 768-dim `vectors_vec` table)
are also caught and the tool will drop + recreate the table at the correct 1536 dims before writing.
