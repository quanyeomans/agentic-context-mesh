---
title: "QMD schema compatibility check failed"
labels: ["compatibility", "needs-review"]
---

The weekly QMD compatibility check has failed. QMD may have updated its SQLite schema.

**Action required:**
1. Check the latest QMD changelog: https://github.com/tobil4sk/qmd/blob/main/CHANGELOG.md
2. Review `qmd_azure_embed/schema.py` — specifically `EXPECTED_CONTENT_VECTORS_COLS` and `EXPECTED_CONTENT_COLS`
3. Update `QMD_TESTED_VERSION` in `schema.py` after verifying compatibility
4. Update `tool.qmd-azure-embed.qmd-tested-version` in `pyproject.toml`
5. Add an entry to `QMD_COMPAT.md`

See the [CI run](${{ env.GITHUB_SERVER_URL }}/${{ env.GITHUB_REPOSITORY }}/actions) for details.
