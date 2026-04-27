## Designing and validating receiver settings

A receiver may have some configurable settings, which can be set via the
Collector config file.

The `tailtracer` receiver will have the following settings:

- `interval`: a string representing the time interval (in minutes) between
  telemetry pull operations.
- `number_of_traces`: the number of mock traces generated for each interval.

Here is what the `tailtracer` receiver settings will look like:

```yaml
receivers:
  tailtracer: # this line represents the ID of your receiver
    interval: 1m
    number_of_traces: 1
```

Create a file named `config.go` under the folder `tailtracer` where you will
write all the code to support your receiver settings.

```sh
touch tailtracer/config.go
```

To implement the configuration aspects of a receiver, you need to create a
`Config` struct. Add the following code to your `config.go` file:

```go
package tailtracer

type Config struct{

}
```

To be able to give your receiver access to its settings, the `Config` struct
must have a field for each of the receiver's settings.

Here is what the `config.go` file should look like after you implemented the
requirements above:

> tailtracer/config.go

```go
package tailtracer

// Config represents the receiver config settings in the Collector config.yaml
type Config struct {
   Interval    string `mapstructure:"interval"`
   NumberOfTraces int `mapstructure:"number_of_traces"`
}
```

> [!NOTE] Check your work
>
> - Added the `Interval` and the `NumberOfTraces` fields to properly have access
>   to their values from the config.yaml.

Now that you have access to the settings, you can provide any kind of validation
needed for those values by implementing the `Validate` method according to the
optional
[ConfigValidator](https://github.com/open-telemetry/opentelemetry-collector/blob/677b87e3ab5c615bc3f93b8f99bb1fa5be951751/component/config.go#L28)
interface.

In this case, the `interval` value will be optional (we will look at generating
default values later). But when defined, it should be at least 1 minute (1m) and
the `number_of_traces` will be a mandatory value. Here is what the config.go
looks like after implementing the `Validate` method:

> tailtracer/config.go

```go
package tailtracer

import (
	"fmt"
	"time"
)

// Config represents the receiver config settings in the Collector config.yaml
type Config struct {
	Interval       string `mapstructure:"interval"`
	NumberOfTraces int    `mapstructure:"number_of_traces"`
}

// Validate checks if the receiver configuration is valid
func (cfg *Config) Validate() error {
	interval, _ := time.ParseDuration(cfg.Interval)
	if interval.Minutes() < 1 {
		return fmt.Errorf("when defined, the interval has to be set to at least 1 minute (1m)")
	}

	if cfg.NumberOfTraces < 1 {
		return fmt.Errorf("number_of_traces must be greater or equal to 1")
	}
	return nil
}
```

> [!NOTE] Check your work
>
> - Imported the `fmt` package to properly format print error messages.
> - Added the `Validate` method to the Config struct to check if the `interval`
>   setting value is at least 1 minute (1m), and if the `number_of_traces`
>   setting value is greater or equal to 1. If that is not true, the Collector
>   will generate an error during its startup process and display the message
>   accordingly.

If you want to take a closer look at the structs and interfaces involved in the
configuration aspects of a component, refer to the
[component/config.go](<https://github.com/open-telemetry/opentelemetry-collector/blob/v{{% param vers %}}/component/config.go>)
file inside the Collector GitHub project.