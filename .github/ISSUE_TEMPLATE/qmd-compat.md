---
name: QMD compatibility issue
about: QMD schema has changed or a new QMD version needs verification
labels: compatibility, qmd
---

## QMD version

- **New QMD version:**
- **Previously tested version:** 1.1.2

## Change type

- [ ] Schema change (new/removed columns in `content`, `documents`, `content_vectors`, `vectors_vec`)
- [ ] New QMD version to verify (no schema change detected)
- [ ] Behaviour change (same schema, different runtime behaviour)

## Schema diff (if applicable)

<!-- Output of: `sqlite3 ~/.cache/qmd/index.sqlite ".schema"` — redact any vault content -->

```sql
```

## Impact

<!-- Which Mnemosyne modules are affected? embed / search / entities / all -->

## Steps to verify

1. Run: `pytest tests/ -k "schema" -v`
2. Run: `mnemosyne embed --limit 5` against new QMD version
3. Run: `mnemosyne search "test query" --agent <agent>` — verify results returned

See [QMD_COMPAT.md](../../QMD_COMPAT.md) for the full compatibility verification procedure.
