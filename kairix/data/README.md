# kairix/data — bundled assets

This directory ships inside the kairix wheel as package-data so a plain
`pip install kairix-agentic-knowledge-mgt` resolves everything
`kairix benchmark` needs to run the lightweight suites. It closes #450:
before the move these files lived at the repo root, outside `kairix/`,
so the `packages.find` include (`kairix*`) excluded them from the wheel.

## What ships here

### `suites/` — benchmark suite YAMLs (≈86 KB)

The small, text-only benchmark suites. Resolved at runtime by
[`kairix.paths.bundled_suites_root`](../paths.py), whose candidate chain
checks (in order):

1. `$KAIRIX_SUITES_ROOT` — operator override (wins even if missing, so a
   typo surfaces as an explicit error rather than a silent fallback).
2. `<package>/data/suites/` — the in-wheel copy (this directory). Makes
   pip installs resolve suites with no source checkout present.
3. `<repo-root>/suites/` — preserved for legacy source-checkout dev UX.
4. `/opt/kairix/suites/` — the canonical Docker install path.
5. `./suites/` — final CWD fallback.

`kairix benchmark list` and `kairix benchmark run --suite <name>` read
this directory. `kairix benchmark run --suite reflib` additionally needs
the reference corpus below.

## What does NOT ship here

### The reference-library corpus (≈50 MB, mixed-license)

The full reference corpus is **not** bundled — it would more than
double the wheel and carries mixed upstream licences. Instead:

- `kairix benchmark install-corpus` downloads the corpus tarball from
  the GitHub release asset for the installed kairix version, verifies it
  against the published sha256 (fail-closed on mismatch), and extracts
  it under [`kairix.paths.reference_corpus_install_dir`](../paths.py)
  (the per-platform cache dir).
- [`kairix.paths.reference_library_root`](../paths.py) resolves the
  installed corpus from a candidate chain: `$KAIRIX_REFLIB_ROOT` →
  cache dir (where `install-corpus` lands it) → `/opt/kairix/reference-library`
  (Docker) → `<repo-root>/reference-library` (source checkout).
- When `--suite reflib` runs and the corpus is absent, the CLI emits an
  actionable affordance pointing the operator at `install-corpus`
  instead of a bare `FileNotFoundError`.
