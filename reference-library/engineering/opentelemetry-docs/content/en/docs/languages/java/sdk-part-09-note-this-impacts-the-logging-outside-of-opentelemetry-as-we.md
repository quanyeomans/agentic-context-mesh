## Note this impacts the logging outside of OpenTelemetry as well
java.util.logging.ConsoleHandler.level = FINE
```

For more fine-grained control and special case handling, custom handlers and
filters can be specified with code.

```java
// Custom filter which does not log errors which come from the export
public class IgnoreExportErrorsFilter implements java.util.logging.Filter {

 public boolean isLoggable(LogRecord record) {
    return !record.getMessage().contains("Exception thrown by the export");
 }
}
```

```properties