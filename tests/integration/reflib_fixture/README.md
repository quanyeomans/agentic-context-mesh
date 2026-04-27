# Reference Library Integration Test Fixture

30 documents sampled from `reference-library/` for fast, deterministic
integration testing. Each file has YAML frontmatter with title, source,
and licence fields.

## Coverage

| Collection              | Count | Example terms                         |
|-------------------------|------:|---------------------------------------|
| agentic-ai              |     3 | chain of thought, prompting, zero-shot|
| engineering             |     6 | twelve factor, ADR, code review       |
| philosophy              |     4 | Stoic, Meditations, Marcus Aurelius   |
| data-and-analysis       |     3 | feature flags, dbt, trunk-based       |
| economics-and-strategy  |     2 | business model canvas, startup        |
| leadership-and-culture  |     2 | open leadership, Mozilla              |
| operating-models        |     1 | platform engineering, CNCF            |
| personal-effectiveness  |     2 | OKR, mindful programming              |
| product-and-design      |     3 | USDS, modern technology stack         |
| security                |     2 | CycloneDX, SBOM                       |
| foundations             |     1 | open logic, formal methods            |

## Selection criteria

- Good frontmatter (title, source, licence fields present)
- Substantive content (>500 characters)
- Known searchable terms for BDD scenarios
- Mix of licence tiers (Public-Domain, MIT, Apache-2.0, CC0, CC-BY)

## Usage

```python
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent
```

BDD step definitions index this fixture into a temporary SQLite database
for search integration tests.
