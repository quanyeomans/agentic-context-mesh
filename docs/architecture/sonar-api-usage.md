# SonarCloud API usage — auth, status filters, paydown queries

Operational reference for talking to SonarCloud's REST API from scripts,
audits, and dashboards. Cross-pollinated from tc-agent-zone's
`feedback_sonar_api_status_filter.md` (saved 2026-05-22 cross-repo
audit) and kept in sync with kairix's existing SonarCloud workflow at
`.github/workflows/sonar-triage.yml`.

## Authentication

SonarCloud issues two token formats. The kairix CI workflows use the
**user-token / project-analysis token** format. Programmatic API access
from scripts uses the same.

### Token formats and their auth headers

| Token prefix | Type | Auth header |
|---|---|---|
| `sonar_user_…` or 40-char hex (legacy) | User token | HTTP Basic with token as user, blank password |
| `squ_…` | Squad / global analysis token | HTTP Basic, same as user tokens |
| `sqp_…` | Project-analysis token | HTTP Basic, same |
| `sqco_…` | SonarCloud organisation/quality-gate API token | **`Authorization: Bearer <token>`** — NOT HTTP Basic |

The `sqco_` Bearer pattern is the one that bites people: every other
SonarCloud token uses Basic auth, so reaching for `-u "$TOKEN:"` on an
`sqco_` token returns `401 Unauthorized` with no useful error body.

### Canonical curl shapes

```bash
# User / squad / project-analysis tokens (most common)
curl -sS -u "${SONAR_TOKEN}:" \
    "https://sonarcloud.io/api/measures/component?component=quanyeomans_kairix&metricKeys=bugs,code_smells,vulnerabilities"

# Organisation / quality-gate API tokens (sqco_…)
curl -sS -H "Authorization: Bearer ${SONAR_SQCO_TOKEN}" \
    "https://sonarcloud.io/api/qualitygates/list?organization=quanyeomans"
```

### CI secret discipline

- `SONAR_TOKEN` in GitHub Actions is the squad analysis token used by
  `sonar-scanner` for analysis upload. Used by `sonar-triage.yml`.
- Any `sqco_…` token for read-side API queries belongs in a separate
  secret (`SONAR_API_TOKEN` or similar), never in the same env var as
  the analysis token — different auth shape, different scope.

## Issue queries — always filter by status

SonarCloud's `/api/issues/search` returns **every state** by default,
including `CLOSED` and `RESOLVED` (legacy debt that's already been
addressed). Without a status filter, dashboards and audits doublecount
fixed issues and overestimate paydown work.

### Canonical status filter

```bash
curl -sS -u "${SONAR_TOKEN}:" \
    "https://sonarcloud.io/api/issues/search?\
componentKeys=quanyeomans_kairix&\
statuses=OPEN,CONFIRMED,REOPENED&\
ps=500"
```

`OPEN,CONFIRMED,REOPENED` is the set of states that mean "this is real,
unaddressed work." Specifically:

- **OPEN** — newly detected, not yet triaged.
- **CONFIRMED** — triaged as a true positive.
- **REOPENED** — was fixed, regression detected.

Excluded:

- **RESOLVED** — fixed; awaiting confirmation. Excluded because counting
  these inflates the active-debt number.
- **CLOSED** — fixed and confirmed, or deleted from scope.

### Severity filter (optional but useful for triage)

```bash
# Critical + blocker only — for release-gate triage queries
curl -sS -u "${SONAR_TOKEN}:" \
    "https://sonarcloud.io/api/issues/search?\
componentKeys=quanyeomans_kairix&\
statuses=OPEN,CONFIRMED,REOPENED&\
severities=BLOCKER,CRITICAL&\
ps=500"
```

## Hotspot queries

Security hotspots have their own status taxonomy (`TO_REVIEW`, `REVIEWED`)
distinct from issues. Filter accordingly:

```bash
curl -sS -u "${SONAR_TOKEN}:" \
    "https://sonarcloud.io/api/hotspots/search?\
projectKey=quanyeomans_kairix&\
status=TO_REVIEW&\
ps=500"
```

`TO_REVIEW` is the only status that means real work — `REVIEWED` covers
both "safe" (acknowledged not-an-issue) and "fixed" outcomes.

## Pagination

SonarCloud's API caps each page at `ps=500` (page size). For projects
with more than 500 issues, paginate via `p=<page-number>` (1-indexed)
and stop when the returned `paging.total` ≤ `p * ps`. Most kairix
queries fit in one page today; flag if a query starts paginating
silently (the script should error out, not truncate).

## Common metric keys

Useful subset for kairix audits:

| Metric key | What it returns |
|---|---|
| `bugs` | Open bug count |
| `code_smells` | Open code-smell count |
| `vulnerabilities` | Open security-issue count |
| `security_hotspots` | TO_REVIEW hotspots |
| `coverage` | Line coverage % (Codecov is the kairix source of truth; SonarCloud's number can drift) |
| `duplicated_lines_density` | % of duplicated lines |
| `cognitive_complexity` | Aggregate cognitive complexity |
| `ncloc` | Non-commented lines of code |
| `alert_status` | Quality gate result: `OK`, `WARN`, `ERROR` |

## Where this is wired today

- `sonar-project.properties` at repo root — analysis config, exclusions,
  ignore rules (F14 requires every ignore have a rationale comment).
- `.github/workflows/sonar-triage.yml` — manual workflow that pulls
  hotspots for review.
- The sonar-scanner step inside `ci.yml`'s security stage uploads
  analysis on every push to main.

## Common pitfalls

1. **`401 Unauthorized` with `sqco_` token + HTTP Basic** — switch to
   `Authorization: Bearer`.
2. **Inflated debt counts** — missing `statuses=OPEN,CONFIRMED,REOPENED`
   filter; defaulting to all statuses includes resolved/closed.
3. **Paginating silently** — script reads `ps=500` and silently truncates
   when there are 501 issues. Always check `paging.total` against
   returned count.
4. **Wrong component key** — kairix uses `quanyeomans_kairix` (organisation
   `quanyeomans` + project key `kairix`). Look at the `sonar-project.properties`
   file for the canonical value; don't guess.

## References

- SonarCloud API docs: https://sonarcloud.io/web_api
- Cross-repo audit (2026-05-22): tc-agent-zone's
  `feedback_sonar_api_status_filter.md`
- `sonar-project.properties` for the kairix analysis configuration
- F14 (sonar-ignore-rationale) for the discipline around ignore entries
