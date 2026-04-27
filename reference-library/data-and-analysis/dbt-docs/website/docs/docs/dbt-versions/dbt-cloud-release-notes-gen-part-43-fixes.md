## Fixes

### Studio IDE and Catalog

- **More reliable search and replace**: Ensures bulk edits stay in sync after server-side edits to prevent stale content from overwriting changes.

- **Correct search preview highlighting**: Fixes preview and match highlighting assembly so match ranges align correctly in multi-line previews.

- **Improved startup failure experience**: Shows a proper error layout and notification on unrecoverable initialization failures.

### Canvas

- **Fewer Add Sources UI interruptions**: Prevents incorrect tab closing after uploads complete and avoids showing the floating node panel when not on a file tab.


### Catalog

- **Public model lineage across environments**: Fixes lineage resolution for public model parents when the producer model lives in a non-default environment.

### dbt Copilot And Agents

- **Reduced resource growth under load**: Fixes an OpenAI connection pool leak that could lead to out-of-memory (OOM) conditions under sustained load. Users should see fewer slowdowns during high-traffic periods.

- **Fewer related models timeouts**: Reduces intermittent failures when attaching related models by increasing internal timeouts for related-model fetching. Users should experience fewer timeout errors when working with related models.