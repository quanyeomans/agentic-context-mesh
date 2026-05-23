# Local-first feedback loops

CI is the **confirmation** gate. Discovery happens locally. Every blocking
signal must be reproducible from a developer (or agent) checkout in under
60 seconds; if it isn't, fix that first.

This doc is the canonical pattern for agents and humans landing changes
that touch lint / type / Sonar / coverage gates.

## How — the one-shot local loop

1. **Query the full failing set once.**
   ```bash
   python3 scripts/checks/check_sonar_new_code.py
   ```
   Prints every Sonar issue Sonar would gate on, with rule key, file,
   line, and a one-line "fix:" hint. Use the JSON output (`--json`) to
   feed an agent batch.

2. **Map each finding to a local rule** (see §"Rule map" below).
   If a finding has no local mapping, add one to the script and to the
   rule map in this doc in the same commit.

3. **Fix the batch in one local pass.** Run `bash scripts/safe-commit.sh
   "<message>"`; the step `sonar new-code parity` re-runs the script and
   blocks on any remaining net-new issue.

4. **Push once.** The next CI run is *confirming* a green local state,
   not *teaching* you about issues.

## Rule map — Sonar key → local detector / fix recipe

| Sonar rule | Local detector | Fix recipe |
|---|---|---|
| `python:S3776` cognitive complexity > 15 | `scripts/checks/check_cognitive_complexity.py` (F16) | Extract the deepest nested construct (loop body → helper). Sabotage-prove the helper. The baseline allow-list grandfathers existing offenders but does **not** cover *increases* in baselined files — those still gate on Sonar. The local check now flags delta-in-baseline too. |
| `python:S5886` / `python:S5890` DataclassInstance return/assign | mypy strict | Don't use `dataclasses.replace` on a function with an annotated `-> SomeDataclass` return — Sonar's type-tracker sees `DataclassInstance`. Construct the dataclass explicitly (`SomeDataclass(field=...)`). |
| `python:S7504` unnecessary `list()` | ruff `UP / RUF` | Drop the `list()` wrapper when the upstream is already iterable for the consumer. |
| `python:S5869` / `python:S6353` regex char-class | ruff `RUF055` family | Use `[a-z]+` not `[a-z][a-z]*`; remove duplicate class members. |
| `python:S6792` / `python:S6796` generics syntax | ruff `UP040` / `UP046` | Use PEP 695 `type` parameter syntax instead of `TypeVar` for new declarations. |
| `python:S5727` identity-always-true | mypy `comparison-overlap` | Remove the dead identity check; the type is already narrowed. |
| `python:S5754` broad except + reraise | ruff `B904` / `BLE001` | Re-raise with `raise ... from <cause>` or narrow the except. |
| `python:S1186` empty function body | F20 (`check_empty_body_intent.py`) | Add a one-line `# Intentionally empty — <why>` or a docstring. |
| `python:S5655` argument type mismatch | mypy | Fix the call site; the type checker is right. |
| `docker:S7031` consecutive RUN | hadolint via pre-commit | Merge consecutive `RUN` instructions with `&&`. |

When a new Sonar rule appears, add a row here and a detector in
`check_sonar_new_code.py` in the same commit.

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

`scripts/safe-commit.sh` step `sonar new-code parity` (see §6 of the
script) runs `check_sonar_new_code.py` and exits 1 on any net-new Sonar
issue in the configured leak period. The script reads SonarCloud's
anonymous API (no token; the project is publicly analyzable).

Skipping the gate: set `KAIRIX_SKIP_SONAR_PARITY=1` for a focused
refactor between commits in a series. CI will still enforce it.

## Related

- `scripts/safe-commit.sh` — local gate composition
- `scripts/checks/check_sonar_new_code.py` — the parity script
- `docs/architecture/fitness-functions.md` — F-rule canon (F16 cognitive complexity)
- `feedback_quality_gate_no_overrides.md` (memory) — strict-gate policy this implements
