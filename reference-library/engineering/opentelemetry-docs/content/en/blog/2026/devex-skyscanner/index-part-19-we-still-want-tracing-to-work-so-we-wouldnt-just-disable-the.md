# We still want tracing to work so we wouldn't just disable the instrumentation
- selector:
    instrument_name: http.*
  view:
    aggregation: drop
- selector:
    instrument_name: rpc.*
  view:
    aggregation: drop
```

The same file can be extended when a service needs to keep specific metrics. A
typical use case is breaking down requests by `http.route`:

```yaml