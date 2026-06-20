# Skills connector

Indexes the host's locally-installed Claude Code **skills, slash-commands,
and sub-agents** into the `skills` collection. This is the external half of
the capability recommender's corpus (Feeder 2). The connector framework
routes a connector's output to a collection named after the connector, so
the skills connector lands in `skills` (not `capabilities`). The
recommender ranks over **both** capability-bearing collections —
`capabilities` (kairix's own tools, Feeder 1) **and** `skills` (this
connector's output) — so `kairix recommend "<task>"` surfaces the agent's
installed skills alongside kairix's own tools.

## What it ingests

It walks the host's `~/.claude` tree:

- `~/.claude/plugins/cache/**/skills/<name>/SKILL.md` → kind `skill`
- `~/.claude/plugins/cache/**/commands/<cmd>.md` → kind `command`
- `~/.claude/plugins/cache/**/agents/<agent>.md` → kind `agent`
- `~/.claude/skills/*.md` (flat files) → kind `skill`

For each artefact it parses the `---`-delimited YAML frontmatter (`name`,
`description`), takes the body as the payload, and emits one capability
document with a stable `capability://<kind>/<name>` source URI.

## How it behaves

- **No credentials.** The source is the local filesystem — no auth, no
  network, no secret resolution.
- **Dedup by name, higher version wins.** The plugin cache holds multiple
  versions of the same plugin (e.g. `4.0.0` and `6.0.3`); the connector
  keeps one entry per name, preferring the higher version segment from the
  `plugins/cache/<mkt>/<plugin>/<version>/` path.
- **Internal sensitivity.** Locally-installed dev tooling metadata is
  company-internal, not secret. Override per deployment with
  `default_sensitivity` in the connector config.

## Failure modes

- **No `~/.claude` (e.g. the production VM).** The connector finds nothing
  and the corpus stays kairix-caps-only. This is a warn-and-continue, never
  an error.
- **Malformed frontmatter / missing `name`.** That one artefact is logged
  and skipped; the rest of the walk still lands (per-item isolation).

## Configuration

Enable it by listing `skills` under `connectors:` — the `name: skills`
entry is what routes its output to the `skills` collection (the recommender
reads that collection alongside `capabilities`):

```yaml
connectors:
  - name: skills                    # routes output to the `skills` collection
    claude_root: ~/.claude          # optional; defaults to the host's ~/.claude
    default_sensitivity: internal   # optional; one of the F39 tiers
    per_tick_max_items: 500         # optional; F66 per-tick budget
```

Gated by the `connector_skills` feature flag (default OFF). When OFF the
connector slot is a no-op; when ON the worker drains the tree on each sync
tick. See `docs/architecture/capability-recommender/recommender-mvp-design.md`
§3.4 for the design.
