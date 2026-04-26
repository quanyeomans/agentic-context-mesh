## Implementing the receiver component

All the receiver APIs are currently declared in the
[receiver/receiver.go](<https://github.com/open-telemetry/opentelemetry-collector/blob/v{{% param vers %}}/receiver/receiver.go>)
file in the Collector project. Open the file and take a minute to browse through
all the interfaces.

Notice that `receiver.Traces` (and its siblings `receiver.Metrics` and
`receiver.Logs`) at this point, doesn't describe any specific methods other than
the ones it "inherits" from `component.Component`.

It may feel weird, but remember, the Collector API was meant to be extensible.
The components and their signals may evolve in different ways, so the role of
those interfaces exists to help support that.

To create a `receiver.Traces`, you need to implement the following methods
described by `component.Component` interface:

```go
Start(ctx context.Context, host Host) error
Shutdown(ctx context.Context) error
```

Both methods act as event handlers used by the Collector to communicate with its
components as part of their lifecycle.

The `Start()` method represents a signal of the Collector telling the component
to start its processing. As part of the event, the Collector will pass the
following information:

- `context.Context`: Most of the time, a receiver will be processing a
  long-running operation, so the recommendation is to ignore this context and
  actually create a new one from context.Background().
- `Host`: The host is meant to enable the receiver to communicate with the
  Collector host once it is up and running.

The `Shutdown()` method represents a signal of the Collector telling the
component that the service is getting shutdown and as such, the component should
stop its processing and make all the necessary cleanup work required:

- `context.Context`: the context passed by the Collector as part of the shutdown
  operation.

You will start the implementation by creating a new file called
`trace-receiver.go` in `tailtracer` folder:

```sh
touch tailtracer/trace-receiver.go
```

And then add the declaration to a type called `tailtracerReceiver` as follows:

```go
type tailtracerReceiver struct{

}
```

Now that you have the `tailtracerReceiver` type, you can implement the `Start()`
and `Shutdown()` methods so the receiver type can be compliant with the
`receiver.Traces` interface.

> tailtracer/trace-receiver.go

```go
package tailtracer

import (
	"context"
	"go.opentelemetry.io/collector/component"
)

type tailtracerReceiver struct {
}

func (tailtracerRcvr *tailtracerReceiver) Start(ctx context.Context, host component.Host) error {
	return nil
}

func (tailtracerRcvr *tailtracerReceiver) Shutdown(ctx context.Context) error {
	return nil
}
```

> [!NOTE] Check your work
>
> - Imported the `context` package which is where the `Context` type and
>   functions are declared.
> - Imported the `go.opentelemetry.io/collector/component` package which is
>   where the `Host` type is declared.
> - Added a bootstrap implementation of the
>   `Start(ctx context.Context, host component.Host)` method to comply with the
>   `receiver.Traces` interface.
> - Added a bootstrap implementation of the `Shutdown(ctx context.Context)`
>   method to comply with the `receiver.Traces` interface.

The `Start()` method is passing 2 references (`context.Context` and
`component.Host`) that your receiver may need to keep so they can be used as
part of its processing operations.

The `context.Context` reference should be used for creating a new context to
support the receiver processing operations. You will need to decide the best way
to handle context cancellation so you can finalize it properly as part of the
component's shutdown in the `Shutdown()` method.

The `component.Host` can be useful during the whole lifecycle of the receiver so
keep that reference in the `tailtracerReceiver` type.

Here is what the `tailtracerReceiver` type declaration will look like after you
include the fields for keeping the references suggested above:

```go
type tailtracerReceiver struct {
	host   component.Host
	cancel context.CancelFunc
}
```

Now you need to update the `Start()` method so the receiver can properly
initialize its own processing context, keep the cancellation function in the
`cancel` field, and initialize its `host` field value. You will also update the
`Stop()` method to finalize the context by calling the `cancel` function.

Here is what the `trace-receiver.go` file looks like after making the changes:

> tailtracer/trace-receiver.go

```go
package tailtracer

import (
	"context"
	"go.opentelemetry.io/collector/component"
)

type tailtracerReceiver struct {
	host   component.Host
	cancel context.CancelFunc
}

func (tailtracerRcvr *tailtracerReceiver) Start(ctx context.Context, host component.Host) error {
	tailtracerRcvr.host = host
	ctx = context.Background()
	ctx, tailtracerRcvr.cancel = context.WithCancel(ctx)

	return nil
}

func (tailtracerRcvr *tailtracerReceiver) Shutdown(ctx context.Context) error {
	if tailtracerRcvr.cancel != nil {
		tailtracerRcvr.cancel()
	}
	return nil
}
```

> [!NOTE] Check your work
>
> Updated the `Start()` method by adding the initialization to the `host` field
> with the `component.Host` reference passed by the Collector.
>
> - Set the `cancel` function field with the cancellation based on a new context
>   created with `context.Background()` (according to the Collector API
>   documentation suggestions).
> - Updated the `Shutdown()` method by adding a call to the `cancel()` context
>   cancellation function.