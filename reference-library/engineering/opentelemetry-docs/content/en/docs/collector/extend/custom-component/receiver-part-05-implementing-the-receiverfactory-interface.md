## Implementing the receiver.Factory interface

The `tailtracer` receiver must provide a `receiver.Factory` implementation.
Although the `receiver.Factory` interface is defined in the
[receiver/receiver.go](<https://github.com/open-telemetry/opentelemetry-collector/blob/v{{% param vers %}}/receiver/receiver.go#L58>)
file within the Collector project, the right way to implement it is by using the
functions available in the `go.opentelemetry.io/collector/receiver` package.

Create a file named `factory.go`:

```sh
touch tailtracer/factory.go
```

Now, let's follow the convention and add a function named `NewFactory()` that
will be responsible for instantiating the `tailtracer` factory. Go ahead and add
the following code to your `factory.go` file:

```go
package tailtracer

import (
	"go.opentelemetry.io/collector/receiver"
)

// NewFactory creates a factory for tailtracer receiver.
func NewFactory() receiver.Factory {
	return nil
}
```

To instantiate your `tailtracer` receiver factory, you will use the following
function from the `receiver` package:

```go
func NewFactory(cfgType component.Type, createDefaultConfig component.CreateDefaultConfigFunc, options ...FactoryOption) Factory
```

The `receiver.NewFactory()` instantiates and returns a `receiver.Factory` and it
requires the following parameters:

- `component.Type`: a unique string identifier for your receiver across all
  Collector components.

- `component.CreateDefaultConfigFunc`: a reference to a function that returns
  the `component.Config` instance for your receiver.

- `...FactoryOption`: the slice of `receiver.FactoryOption`s that will determine
  what type of signal your receiver is capable of processing.

Let's now implement the code to support all the parameters required by
`receiver.NewFactory()`.