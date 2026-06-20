# Dependency rationale

This connector introduces **no new third-party dependency**.

The source is the local filesystem (`~/.claude`), so the connector needs
no HTTP client, no SDK, and no secret backend. It reads files with the
standard library and parses YAML frontmatter with `pyyaml` — both already
in the kairix dependency set.

## Current dependencies

_None beyond the existing kairix dependency set._ The connector imports
only:

- `pyyaml` — already a core kairix dependency (frontmatter parse; the
  Obsidian connector uses it the same way).
- the Python standard library (`logging`, `pathlib`, `datetime`,
  `dataclasses`).
- `kairix.core.*` — the Protocol surface.

## Adding a new dependency

1. Justify against "could stdlib + the existing deps do this?" first —
   for a local-filesystem walk the answer is almost always yes.
2. If a dependency is genuinely needed, add a one-line rationale here
   (`package — reason`) and confirm it does not break F35 (no
   cross-connector / extractor imports).
