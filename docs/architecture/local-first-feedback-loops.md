# Local-first feedback loops

CI is the **confirmation** gate. Discovery happens locally. Every blocking
signal must be reproducible from a developer (or agent) checkout in under
60 seconds; if it isn't, fix that first.

This doc is the canonical pattern for agents and humans landing changes
that touch lint / type / Sonar / coverage gates.

## How — the one-shot local loop

1. **Query the full failing set once.**
   ```bash
   python3 scripts/checks/check_sonar_new_code.py --all
   ```
   Prints every file whose current Sonar open-issue (or hotspot) count
   exceeds its committed baseline. Use the JSON output (`--all --json`)
   to feed an agent batch. Drop `--all` to scope the check to the files
   changed in this change (the default safe-commit behaviour).

2. **Map each finding to a local rule** (see §"Rule map" below).
   If a finding has no local mapping, add one to the script and to the
   rule map in this doc in the same commit.

3. **Fix the batch in one local pass.** Run `bash scripts/safe-commit.sh
   "<message>"`; the step `sonar per-file ratchet` re-runs the script and
   blocks on any file over its committed baseline.

4. **Push once.** The next CI run is *confirming* a green local state,
   not *teaching* you about issues.

## Rule map — Sonar key → local detector → positive-pattern recipe

Each recipe shows the **shape to write** — copy-paste-adapt directly.
Canonical examples reference real code in this repo where available.

### `python:S3776` cognitive complexity > 15
- Local detector: `scripts/checks/check_cognitive_complexity.py` (F16)
- Recipe: hoist the inner-most loop / branch block into a `_helper(...)` function and call it from the outer loop. Canonical example: `_mark_existing_vec_hit` in `kairix/core/search/rrf.py` replaced a 3-deep nested `for / if / if`.
- Pattern:
  ```python
  def _mark_existing_vec_hit(results, path_lower, rank, result):
      for fr in results:
          if fr.path.lower() == path_lower:
              fr.in_vec = True
              fr.vec_rank = rank
              return

  def _bm25_primary_impl(bm25, vec):
      for rank, result in enumerate(vec, start=1):
          if path_lower in seen:
              _mark_existing_vec_hit(results, path_lower, rank, result)
              continue
          ...
  ```
- Note: the F16 baseline allow-list grandfathers existing offenders. Sonar gates *increases* in baselined files; the local script now flags those too.

### `pythonsecurity:S2083` path constructed from user-controlled data
- Local detector: none (taint analysis is server-side only) — this is why the parity check exists.
- Recipe: when the path is the operator's own trust boundary (a CLI flag, `KAIRIX_SECRETS_FILE`, a config value), confine instead of reject: canonicalise with `Path.expanduser().resolve()` and verify the result sits under an explicit allow-list of roots, raising an F21-shaped `ValueError` before any filesystem call. Then add a `sonar.issue.ignore.multicriteria` entry citing the guard + its sabotage-proven tests — Sonar's taint engine does not recognise allow-list sanitisers. Canonical examples: `_confine_to_allowed_root` in `kairix/connect/store/file_store.py` and `kairix/secrets/store.py`.
- Pattern:
  ```python
  def _confine_to_allowed_root(path: Path) -> Path:
      resolved = path.expanduser().resolve()
      for root in (Path.home().resolve(), Path("/etc/kairix"), Path("/run/secrets")):
          if resolved == root or root in resolved.parents:
              return resolved
      raise ValueError(f"Refusing to write to {resolved} — outside allowed roots. fix: ...")
  ```
- Note: the gate reads the project's *current* per-file open-issue counts and compares them to the committed baseline. A main-branch BLOCKER on an existing file only fails the ratchet if it pushes that file *above* its grandfathered baseline count; once the fix lands and SonarCloud re-scans, regenerate the baseline with `--capture` so the lowered count becomes the new floor. There is no skip flag — the ratchet is deterministic. CI's quality gate remains the authoritative confirmation.

### `python:S5886` / `python:S5890` DataclassInstance return / assign
- Local detector: mypy strict + this script
- Recipe: construct the dataclass explicitly with field-by-field copy. Canonical example: `_replace_document_root` in `kairix/knowledge/wikilinks/cli.py`.
- Pattern:
  ```python
  def _replace_document_root(paths: KairixPaths, document_root: Path) -> KairixPaths:
      return KairixPaths(
          document_root=document_root,
          db_path=paths.db_path,
          log_dir=paths.log_dir,
          workspace_root=paths.workspace_root,
      )
  ```

### `python:S7504` unnecessary `list()`
- Local detector: ruff `UP / RUF`
- Pattern:
  ```python
  for x in seq:           # not: for x in list(seq):
      ...
  ```

### `python:S5869` / `python:S6353` regex character class
- Local detector: ruff `RUF055` family
- Pattern:
  ```python
  re.compile(r"[a-z]+")   # not: r"[a-z][a-z]*"; deduplicate members.
  ```

### `python:S6792` / `python:S6796` generics syntax
- Local detector: ruff `UP040` / `UP046`
- Pattern (PEP 695):
  ```python
  class Cache[T]: ...
  def head[T](xs: list[T]) -> T: ...
  ```

### `python:S5727` identity-always-true
- Local detector: mypy `comparison-overlap`
- Pattern: use the variable directly; the type is already narrowed. If the value really might be `None` the type should reflect it (`X | None`).

### `python:S5754` broad except + reraise
- Local detector: ruff `B904` / `BLE001`
- Pattern:
  ```python
  except SpecificError as exc:
      raise DomainError("…") from exc
  ```

### `python:S1186` empty function body
- Local detector: `scripts/checks/check_empty_body_intent.py` (F20)
- Pattern:
  ```python
  def hook() -> None:
      """One-line docstring stating the intent."""
      # or: # Intentionally empty — <why>
  ```

### `python:S5655` argument type mismatch
- Local detector: mypy
- Recipe: convert the argument at the call site to the declared type. If the conversion isn't possible the signature is wrong — narrow / overload the parameter type.

### `python:S1192` duplicated string literal (≥10 chars, ≥3 occurrences)
- Local detector: `scripts/checks/check_no_duplicate_string.py` (F17) — but Sonar also flags shorter literals (≥5 chars) that F17 ignores.
- Recipe: extract the literal into a module-level constant OR, when the literal is always wrapped in the same operation, extract a small helper that hides the literal. Helpers beat bare constants when the *operation* (not just the value) is what's repeated.
- Pattern:
  ```python
  def _iso_z(dt: datetime) -> str:
      return dt.isoformat().replace("+00:00", "Z")

  def _now_iso() -> str:
      return _iso_z(datetime.now(timezone.utc))

  modified_at = _iso_z(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))
  ```

### `python:S3358` nested conditional expression
- Local detector: ruff (configurable)
- Pattern:
  ```python
  tmp = a if cond else b
  result = f(tmp)
  ```

### `docker:S7031` consecutive RUN
- Local detector: hadolint via pre-commit
- Pattern:
  ```dockerfile
  RUN apt-get update \
   && apt-get install -y --no-install-recommends pkg-a pkg-b \
   && rm -rf /var/lib/apt/lists/*
  ```

When a new Sonar rule appears, add a section here AND a row in
`scripts/checks/check_sonar_new_code.py:FIX_HINTS` in the same commit.

## Why — the loop economics

- Sonar's gate runs once per push, ~5–10 min per cycle. Local detectors
  run in <30s. Per-push iteration costs 10–20× the wall-clock and
  burns CI minutes, agent context tokens, and reviewer attention.
- Batched local fixes converge in one commit; per-push fixes serialise
  one finding per cycle and risk introducing new findings mid-stream
  (the new finding only appears on the *next* push).
- When two static analysers disagree on an idiom (e.g. mypy
  `redundant-cast` vs Sonar `DataclassInstance`), refactor the
  construct away. Don't try to satisfy both with annotations.

## What the gate enforces

`scripts/safe-commit.sh` step `sonar per-file ratchet` runs
`check_sonar_new_code.py`. The script reads SonarCloud's anonymous API
(no token; the project is publicly analyzable) for the project's
**current** per-file open-issue counts, then compares them to a
**committed baseline** and exits 1 for any file whose count exceeds its
baseline.

Two committed baselines, two policies:

- `.architecture/baseline/sonar-per-file.json` — code smells / bugs /
  vulnerabilities, keyed by repo-relative path → open-issue count.
  Grandfathers main's existing debt.
- `.architecture/baseline/sonar-per-file-hotspots.json` — security
  hotspots, split out so they ratchet **independently** (a smell
  regression must never mask a hotspot regression).

A file absent from a baseline defaults to `0`, so any open issue or
hotspot on a net-new (or previously-clean) file fails the gate.

Why a committed ratchet instead of the live "leak period": main's
new-code leak period **mutates** as commits land, so the old gate was
non-deterministic — which made a routine skip flag attractive. The
committed baseline makes the verdict depend on a stable snapshot, not a
moving target.

Scope: the default run focuses on the **working set** (files changed in
this change, mirroring `safe-commit.sh`). Pass `--all` for the full-repo
view, `--json` for an agent batch.

Regenerating the baseline: after a fix lands on main and SonarCloud
re-scans, run `python3 scripts/checks/check_sonar_new_code.py --capture`
to re-grandfather the current (lowered) per-file counts into both JSON
files.

No skip flag: the ratchet is deterministic, so there is nothing flaky to
skip — the `KAIRIX_SKIP_SONAR_PARITY` escape hatch was retired in #499
Phase 2. The only non-failure path is "SonarCloud unreachable → warn +
exit 0", which fires only when SonarCloud is genuinely down (offline
pre-commit), never as a routine bypass. CI's quality gate remains
authoritative.

## Related

- `scripts/safe-commit.sh` — local gate composition
- `scripts/checks/check_sonar_new_code.py` — the parity script
- `docs/architecture/fitness-functions.md` — F-rule canon (F16 cognitive complexity)
- `feedback_quality_gate_no_overrides.md` (memory) — strict-gate policy this implements
