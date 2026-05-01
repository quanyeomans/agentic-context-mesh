## Identifying and providing default settings

Previously, we mentioned that the `interval` setting for the `tailtracer`
receiver would be optional. You will need to provide a default value for it so
it can be used as part of the default settings.

Go ahead and add the following code to your `factory.go` file:

```go
var (
	typeStr         = component.MustNewType("tailtracer")
)

const (
	defaultInterval = 1 * time.Minute
)
```

As for default settings, you just need to add a function that returns a
`component.Config` holding the default configurations for the `tailtracer`
receiver.

To accomplish that, go ahead and add the following code to your `factory.go`
file:

```go
func createDefaultConfig() component.Config {
	return &Config{
		Interval: string(defaultInterval),
	}
}
```

After these two changes you will notice a few imports are missing, so here is
what your `factory.go` file should look like with the proper imports:

> tailtracer/factory.go

```go
package tailtracer

import (
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/receiver"
)

var (
	typeStr         = component.MustNewType("tailtracer")
)

const (
	defaultInterval = 1 * time.Minute
)

func createDefaultConfig() component.Config {
	return &Config{
		Interval: string(defaultInterval),
	}
}

// NewFactory creates a factory for tailtracer receiver.
func NewFactory() receiver.Factory {
	return nil
}
```

> [!NOTE] Check your work
>
> - Imported the `time` package to support the time.Duration type for the
>   defaultInterval.
> - Imported the `go.opentelemetry.io/collector/component` package, which is
>   where `component.Config` is declared.
> - Imported the `go.opentelemetry.io/collector/receiver` package, which is
>   where `receiver.Factory` is declared.
> - Added a `time.Duration` constant called `defaultInterval` to represent the
>   default value for our receiver's `Interval` setting. We will be setting the
>   default value for 1 minute, hence the assignment of `1 * time.Minute` as its
>   value.
> - Added a function named `createDefaultConfig`, which is responsible for
>   returning a `component.Config` implementation, which in this case is going
>   to be an instance of our `tailtracer.Config` struct.
> - The `tailtracer.Config.Interval` field was initialized with the
>   `defaultInterval` constant.