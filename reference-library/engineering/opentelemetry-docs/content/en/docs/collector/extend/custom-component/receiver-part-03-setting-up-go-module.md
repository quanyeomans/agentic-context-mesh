## Setting up Go module

Every Collector component should be created as a Go module. Let's create a
`tailtracer` folder to host our receiver project and initialize it as Go module.

```sh
mkdir tailtracer
cd tailtracer
go mod init github.com/open-telemetry/opentelemetry-tutorials/trace-receiver/tailtracer
```

> [!NOTE]
>
> The module path above is a mock path, which can be your desired private or
> public path. See the
> [initial trace-receiver code](https://github.com/rquedas/otel4devs/tree/main/collector/receiver/trace-receiver).

It is recommended to enable Go
[Workspaces](https://go.dev/doc/tutorial/workspaces) since we're going to manage
multiple Go modules: the `otelcol-dev` and `tailtracer`, and possibly more
components over time.

```sh
cd ..
go work init
go work use otelcol-dev
go work use tailtracer
```