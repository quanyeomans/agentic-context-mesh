## Enhancements

### Catalog

- **Faster model graph rendering for large projects**: Improved model graph layout performance to reduce load time in larger projects.

- **Faster similar models results**: Similar Models lookup now uses an optimized vector search strategy to reduce timeouts on large projects.

### Studio IDE

- **Clearer project root in Catalog file tree**: When your dbt project is in a subdirectory, the project root is highlighted in the Catalog file tree.

- **More native rename and delete in Catalog file tree**: Rename and delete actions now use native editor behaviors when using the Catalog file tree.

- **More reliable in-browser formatting**: Formatting updates now apply directly to the active editor buffer to reduce prompts and inconsistent results.

- **Cleaner code generation workflow**: Code generation no longer creates a temporary file in your repository during generation.

### dbt platform

- **Fusion compatibility validation on environments**: Environment settings now prevent saving a Fusion dbt version with an incompatible connection and surface field level validation errors.

- **Smarter Fusion defaults during connection setup**: When setting up a new connection, Fusion eligible adapters now default to the latest Fusion version to reduce misconfiguration during setup.

- **Improved Private Link endpoint management**: Private Endpoints can be sorted by status and connections, and endpoint details now show associated connections and environments.


### Run Logs

- **More reliable invocation event streaming**: Invocation event streaming is more reliable for long running jobs by deriving totals from the latest stream event identifier.

- **Reduced Redis usage after log streams complete**: Log streaming now cleans up Redis keys after a stream completes, reducing stale keys and Redis memory pressure for high volume runs.