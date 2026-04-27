## Running and debugging the receiver

Ensure that the Collector `config.yaml` has been updated properly with the
`tailtracer` receiver configured as one of the receivers used in the
pipeline(s).

> config.yaml

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
  tailtracer: # this line represents the ID of your receiver
    interval: 1m
    number_of_traces: 1

exporters:
  debug:
    verbosity: detailed
  otlp/jaeger:
    endpoint: localhost:14317
    tls:
      insecure: true
    sending_queue:
      batch:

service:
  pipelines:
    traces:
      receivers: [otlp, tailtracer]
      exporters: [otlp/jaeger, debug]
  telemetry:
    logs:
      level: debug
```

Let's use the `go run` command instead of the previously generated
`./otelcol-dev/otelcol-dev` binary file, to start the updated Collector as we
have had code changes in the `otelcol-dev/components.go` file.

```sh
go run ./otelcol-dev --config config.yaml
```

The output should look like this:

```log
2023-11-08T21:38:36.621+0800	info	service@v0.88.0/telemetry.go:84	Setting up own telemetry...
2023-11-08T21:38:36.621+0800	info	service@v0.88.0/telemetry.go:201	Serving Prometheus metrics	{"address": ":8888", "level": "Basic"}
2023-11-08T21:38:36.621+0800	info	exporter@v0.88.0/exporter.go:275	Development component. May change in the future.	{"kind": "exporter", "data_type": "traces", "name": "debug"}
2023-11-08T21:38:36.621+0800	debug	exporter@v0.88.0/exporter.go:273	Stable component.	{"kind": "exporter", "data_type": "traces", "name": "otlp/jaeger"}
2023-11-08T21:38:36.621+0800	debug	receiver@v0.88.0/receiver.go:294	Stable component.	{"kind": "receiver", "name": "otlp", "data_type": "traces"}
2023-11-08T21:38:36.621+0800	debug	receiver@v0.88.0/receiver.go:294	Alpha component. May change in the future.	{"kind": "receiver", "name": "tailtracer", "data_type": "traces"}
2023-11-08T21:38:36.622+0800	info	service@v0.88.0/service.go:143	Starting otelcol-dev...	{"Version": "1.0.0", "NumCPU": 10}
2023-11-08T21:38:36.622+0800	info	extensions/extensions.go:33	Starting extensions...

<OMITTED>

2023-11-08T21:38:36.636+0800	info	zapgrpc/zapgrpc.go:178	[core] [Channel #1] Channel Connectivity change to READY	{"grpc_log": true}
2023-11-08T21:39:36.626+0800	info	tailtracer/trace-receiver.go:33	I should start processing traces now!	{"kind": "receiver", "name": "tailtracer", "data_type": "traces"}
2023-11-08T21:40:36.626+0800	info	tailtracer/trace-receiver.go:33	I should start processing traces now!	{"kind": "receiver", "name": "tailtracer", "data_type": "traces"}
...
```

As you can see from the logs, the `tailtracer` has been initialized
successfully. Every minute, there will be a message that reads,
`I should start processing traces now!`, triggered by the dummy ticker in
`tailtracer/trace-receiver.go`.

> [!TIP]
>
> To stop the process press <kbd>Ctrl + C</kbd> in your Collector terminal.

Additionally, you may use your IDE of choice to debug the receiver, just as you
would normally debug a Go project. Here is a simple `launch.json` file for
[Visual Studio Code](https://code.visualstudio.com/) for your reference:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Launch otelcol-dev",
      "type": "go",
      "request": "launch",
      "mode": "auto",
      "program": "${workspaceFolder}/otelcol-dev",
      "args": ["--config", "${workspaceFolder}/config.yaml"]
    }
  ]
}
```

As a big milestone, let's take a look at how the folder structure looks like
now:

```console
.
├── builder-config.yaml
├── config.yaml
├── go.work
├── go.work.sum
├── ocb
├── otelcol-dev
│   ├── components.go
│   ├── components_test.go
│   ├── go.mod
│   ├── go.sum
│   ├── main.go
│   ├── main_others.go
│   ├── main_windows.go
│   └── otelcol-dev
└── tailtracer
    ├── config.go
    ├── factory.go
    ├── go.mod
    └── trace-receiver.go
```

In the next section, you will learn more about the OpenTelemetry Trace data
model so the `tailtracer` receiver can finally generate meaningful traces!