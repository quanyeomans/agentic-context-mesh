## Summary

<!-- What does this PR do? 1-3 bullet points. -->

## Change type

- [ ] Bug fix
- [ ] New feature / enhancement
- [ ] Refactor (no behaviour change)
- [ ] Documentation only
- [ ] Dependency update

## Testing

- [ ] Unit tests pass: `pytest tests/ -m unit`
- [ ] Contract tests pass: `pytest tests/ -m contract`
- [ ] Integration tests pass: `pytest tests/ -m integration`
- [ ] New tests added for changed behaviour (if applicable)

## Retrieval quality

*Required if this PR changes search, embed, entity, or temporal logic.*

- [ ] Benchmark run completed: `kairix benchmark run --suite suites/<suite>.yaml`
- [ ] NDCG@10 result: **__.__** (baseline: 0.7756)
- [ ] No category regressed by more than 0.02

*Skip this section for documentation-only or ops-tooling PRs.*

## Security

- [ ] No secrets, API keys, or real vault content in this PR
- [ ] `detect-secrets scan` passes: no new detections
- [ ] `bandit` passes: zero HIGH/MEDIUM findings
- [ ] `pip-audit` passes (if dependencies changed)

## Checklist

- [ ] `ruff check kairix/ tests/` — zero errors
- [ ] `mypy kairix/` — zero errors (for changed modules)
- [ ] Coverage ≥ 80% maintained
- [ ] Documentation updated (if behaviour or CLI surface changed)
