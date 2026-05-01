## Lessons and pain points

Skyscanner still uses older, unstable HTTP semantic conventions in some
pipelines. Upgrading requires updating multiple transform processor rules that
map Istio attributes to semantic convention names, which involves manually
cross-referencing documentation and filling out configuration strings.

The team is aware of [Weaver](https://github.com/open-telemetry/weaver) for
semantic convention management but hasn't yet integrated it into their workflow.

Upgrading every six months means encountering multiple breaking changes at once.
While the release notes are well-written and clearly document changes, reviewing
six months of updates at once adds friction compared to keeping pace with
releases.