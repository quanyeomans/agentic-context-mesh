#!/usr/bin/env bash
# F46: BDD step impls call factory-composed production code.
#
# Step impls under tests/bdd/steps/*.py must, somewhere in their call
# graph (depth ≤ 2), invoke one of: a CLI entry point (kairix.cli.main
# or per-subcommand main() under kairix/**/cli.py or kairix/<x>_cli.py);
# an MCP tool function (the callable wrapped by @server.tool() in
# kairix/agents/mcp/server.py); a factory constructor
# (kairix.core.factory.build_* — search / embed / connector / ingest).
#
# Direct construction of SearchPipeline(...) / EmbedPipeline(...) /
# ConnectorPipeline(...) / IngestPipeline(...) inside a step file is
# disallowed unless one of the sanctioned entry points is also reached.
#
# Pre-existing violations are grandfathered in
# .architecture/baseline/f46-files.txt. F49 forces this baseline to
# shrink each release; new files cannot be added to the list.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

cd "${SCRIPT_DIR}/../.." || exit 2

REMEDIATION="F46: tests/bdd/steps/<file>.py constructs a pipeline directly instead of going through the factory.

fix: use factory.build_search_pipeline(paths=FakePaths(...)) — see
tests/integration/test_vec_index_lifecycle.py for the canonical pattern,
and docs/architecture/test-discipline-hardening.md §4.1.
next: replace direct *Pipeline(...) construction with
factory.build_<pipeline>(paths=FakePaths(...)) and a
registry=FakeProviderRegistry(...) where embed is in scope.
run: bash scripts/checks/check-f46-bdd-step-composition.sh

Pass example:
  from kairix.core import factory
  from tests.fakes import FakePaths, FakeProviderRegistry

  @when('I run a search')
  def run_search() -> None:
      pipe = factory.build_search_pipeline(
          paths=FakePaths(),
          registry=FakeProviderRegistry(),
      )
      _state['result'] = pipe.search('query')

Forbidden example:
  from kairix.core.search.pipeline import SearchPipeline

  @when('I run a search')
  def run_search() -> None:
      pipe = SearchPipeline(...)              # F46 — direct construction
      _state['result'] = pipe.search('query')"

# The Python detector emits its own gate() output (matching the F26
# idiom in the tc_fitness package). The shell wrapper here exists so the gate is
# invocable via the same naming convention as F1/F2/F4 and to carry the
# F21-required ``fix:`` / ``next:`` / ``run:`` markers in the shell
# entry point itself.
python3 "${SCRIPT_DIR}/check_f46_bdd_step_composition.py"
rc=$?
if [[ $rc -ne 0 ]]; then
    printf '\n%s\n' "$REMEDIATION"
fi
exit "$rc"
