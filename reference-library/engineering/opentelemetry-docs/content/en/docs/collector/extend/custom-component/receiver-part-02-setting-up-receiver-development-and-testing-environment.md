## Setting up receiver development and testing environment

First, use the [Building a Custom Collector](/docs/collector/extend/ocb/)
tutorial to create a Collector instance named `otelcol-dev`; all you need is to
copy the `builder-config.yaml` described in
[Configure the OpenTelemetry Collector Builder](/docs/collector/extend/ocb/#configure-the-opentelemetry-collector-builder)
and run the builder. As an outcome, you should now have a folder structure like
this:

```text
.
├── builder-config.yaml
├── ocb
└── otelcol-dev
    ├── components.go
    ├── components_test.go
    ├── go.mod
    ├── go.sum
    ├── main.go
    ├── main_others.go
    ├── main_windows.go
    └── otelcol-dev
```

To properly test your trace receiver, you may need a distributed tracing backend
so the Collector can send the telemetry to it. We will be using
[Jaeger](https://www.jaegertracing.io/docs/latest/getting-started/). If you
don't have a `Jaeger` instance running, you can easily start one using Docker
with the following command:

```sh
docker run -d --name jaeger \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  -p 14317:4317 \
  -p 14318:4318 \
  jaegertracing/all-in-one:1.41
```

Once the container is up and running, you can access Jaeger UI via this URL:
<http://localhost:16686/>

Now, create a Collector config file named `config.yaml` to set up the Collector
components and pipelines.

```sh
touch config.yaml
```

For now, you just need a basic traces pipeline with the `otlp` receiver and the
`otlp` and `debug` exporters. Here is what your `config.yaml` file should look
like:

> config.yaml

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

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
      receivers: [otlp]
      exporters: [otlp/jaeger, debug]
  telemetry:
    logs:
      level: debug
```

> [!NOTE]
>
> Here, we use the `insecure` flag in the `otlp` exporter config for simplicity;
> you should use TLS certificates for secure communication or mTLS for mutual
> authentication when running the Collector in production, by following this
> [guide](/docs/collector/configuration/#setting-up-certificates).

To verify that the Collector is properly set up, run this command:

```sh
./otelcol-dev/otelcol-dev --config config.yaml
```

The output may look like this:

```log
2023-11-08T18:38:37.183+0800	info	service@v0.88.0/telemetry.go:84	Setting up own telemetry...
2023-11-08T18:38:37.185+0800	info	service@v0.88.0/telemetry.go:201	Serving Prometheus metrics	{"address": ":8888", "level": "Basic"}
2023-11-08T18:38:37.185+0800	debug	exporter@v0.88.0/exporter.go:273	Stable component.	{"kind": "exporter", "data_type": "traces", "name": "otlp/jaeger"}
2023-11-08T18:38:37.186+0800	info	exporter@v0.88.0/exporter.go:275	Development component. May change in the future.	{"kind": "exporter", "data_type": "traces", "name": "debug"}
2023-11-08T18:38:37.186+0800	debug	receiver@v0.88.0/receiver.go:294	Stable component.	{"kind": "receiver", "name": "otlp", "data_type": "traces"}
2023-11-08T18:38:37.186+0800	info	service@v0.88.0/service.go:143	Starting otelcol-dev...	{"Version": "1.0.0", "NumCPU": 10}

<OMITTED>

2023-11-08T18:38:37.189+0800	info	service@v0.88.0/service.go:169	Everything is ready. Begin running and processing data.
2023-11-08T18:38:37.189+0800	info	zapgrpc/zapgrpc.go:178	[core] [Server #3 ListenSocket #4] ListenSocket created	{"grpc_log": true}
2023-11-08T18:38:37.195+0800	info	zapgrpc/zapgrpc.go:178	[core] [Channel #1 SubChannel #2] Subchannel Connectivity change to READY	{"grpc_log": true}
2023-11-08T18:38:37.195+0800	info	zapgrpc/zapgrpc.go:178	[core] [pick-first-lb 0x140005efdd0] Received SubConn state update: 0x140005eff80, {ConnectivityState:READY ConnectionError:<nil>}	{"grpc_log": true}
2023-11-08T18:38:37.195+0800	info	zapgrpc/zapgrpc.go:178	[core] [Channel #1] Channel Connectivity change to READY	{"grpc_log": true}
```

If everything went well, the Collector instance should be up and running.

You may use the
[telemetrygen](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/cmd/telemetrygen)
to further verify the setup. For example, open another console and run the
following commands:

```sh
go install github.com/open-telemetry/opentelemetry-collector-contrib/cmd/telemetrygen@latest

telemetrygen traces --otlp-insecure --traces 1
```

You should be able to see detailed logs in the console and the traces in Jaeger
UI via this URL: <http://localhost:16686/>.

Press <kbd>Ctrl + C</kbd> to stop the Collector instance in the Collector
console.