# but continue to drop http.client.* metrics
- selector:
    instrument_name: http.server.request.duration
  view:
    # renamed because we already have Istio metrics named http.server.request.duration,
    # so don't want to clash and double count
    name: app.http.server.request.duration
    attribute_keys:
      - http.request.method
      - http.route
      - http.response.status_code
```

This approach allows Skyscanner to keep high-value distributed traces, avoid
metric duplication, control cardinality, and reduce ingestion costs—all without
requiring service owners to deeply understand OpenTelemetry internals.

Overall, the strategy reflects a strong platform mindset: provide sensible
defaults that work at scale, minimize noise, and make the "right thing" the easy
thing, while still leaving room for teams with advanced needs to go further.