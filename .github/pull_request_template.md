## Summary

<!-- What does this PR do? 1-3 bullet points. -->

## Change type

- [ ] Bug fix
- [ ] New feature / enhancement
- [ ] Architecture (protocols, pipelines, repositories)
- [ ] Documentation only
- [ ] Dependency update

## Quality gates

- [ ] `bash scripts/safe-commit.sh` passes (lint, format, mypy, tests, secrets)
- [ ] No `@patch` or monkey-patching in new test code — uses fakes from `tests/fakes.py`
- [ ] No private function imports in tests
- [ ] Every new test has a marker (`@pytest.mark.unit`, `contract`, `bdd`, or `integration`)
- [ ] Protocol compliance verified if new protocols/implementations added

## Retrieval quality

*Required if this PR changes search, embed, entity, or temporal logic.*

- [ ] Benchmark run completed: `kairix benchmark run --suite suites/<suite>.yaml`
- [ ] NDCG@10 result: **__.__**
- [ ] No category regressed by more than 0.02

## Security

- [ ] No secrets, API keys, or real agent/client names in this PR
- [ ] `detect-secrets scan` passes
- [ ] `bandit` passes: zero HIGH/MEDIUM findings

## Checklist

- [ ] Coverage ≥ 80% maintained
- [ ] Documentation updated (if behaviour or API surface changed)
- [ ] CONSTRAINTS.md respected
