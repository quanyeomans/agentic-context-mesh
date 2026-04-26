## Appendix

### Internal logging

SDK components log a variety of information to
[java.util.logging](https://docs.oracle.com/javase/7/docs/api/java/util/logging/package-summary.html),
at different log levels and using logger names based on the fully qualified
class name of the relevant component.

By default, log messages are handled by the root handler in your application. If
you have not installed a custom root handler for your application, logs of level
`INFO` or higher are sent to the console by default.

You may want to change the behavior of the logger for OpenTelemetry. For
example, you can reduce the logging level to output additional information when
debugging, increase the level for a particular class to ignore errors coming
from the class, or install a custom handler or filter to run custom code
whenever OpenTelemetry logs a particular message. No detailed list of logger
names and log information is maintained. However, all OpenTelemetry API, SDK,
contrib and instrumentation components share the same `io.opentelemetry.*`
package prefix. It can be useful to enable finer grain logs for all
`io.opentelemetry.*`, inspect the output, and narrow down to packages or FQCNs
of interest.

For example:

```properties