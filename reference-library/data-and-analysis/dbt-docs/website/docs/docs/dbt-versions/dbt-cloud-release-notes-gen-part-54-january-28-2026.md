## January 28, 2026

### New

- **Canvas**
  - **New two-step "upload source" API for more resilient uploads**: Use `POST /v1/workspaces/{workspace_id}/upload-source` to create an upload, then `PATCH /v1/workspaces/{workspace_id}/upload-source/{file_id}/process` to stream processing progress (SSE).  

### Enhancements

- **Catalog & Search**
  - **Improved search relevance and highlighting**: Ranking now boosts results by modeling layer, and highlighting is more consistent (including support for multiple highlight snippets per field).  
- **dbt platform**
  - **Private endpoints details page**: The dbt platform now includes a Private Endpoint details view with endpoint properties, connectivity status, and associated projects.  
  - **Fusion-aware default dbt version during setup**: Connection setup and environment creation can now default to `latest-fusion` for eligible projects.  
- **Studio IDE**
  - **Search and replace in files**: Adds a dedicated sidebar search experience. Please contact your account manager to enable.
  - **Autofix now includes package upgrades**: Upgrade flows can proceed from fixing deprecations into package upgrades in the same guided run.  
  - **Editor UI polish**: Fixed multiple layout/styling issues for a more consistent editor experience.  

### Fixes

- **dbt platform**
  - **Run logs render ANSI/structured output more reliably**: Improved rendering and cleanup of escape sequences in step logs.  
  - **More accurate source freshness status in multi-job environments**: Freshness status is preserved when a run lacks freshness results but freshness remains configured.  
  - **More robust seed artifact ingestion**: Ingestion now tolerates missing/null `schema` fields in the manifest to avoid failures.  

- **Studio IDE**
  - **CLI project sync no longer fails on broken symlinks**: Sync skips missing symlink targets instead of failing the whole sync.  
  - **IDE abort is clearer when a command is missing**: Aborting a command that no longer exists returns a specific "no-command-found" response.  
  - **More robust inline command results**: Malformed inline commands no longer break result processing; `show --inline` with an empty result returns an empty preview table.  

- **Canvas**
  - **Clearer errors for duplicate uploaded-source names**: Creating an uploaded-source model with a duplicate name now returns HTTP 409 with an actionable message.  
  - **Failed uploads are now visible via file state**: Uploaded-source processing records failure state instead of deleting the file record, improving retry/resume workflows.  
  - **Invocation status streaming reliability**: The invocation status SSE endpoint now correctly awaits the status stream.

### Behavior changes

- **Catalog & Search**
  - **Search highlight fields deprecated and highlights shape expanded**: `AccountSearchHit.highlight` and `AccountSearchHit.matchedField` are deprecated. `AccountSearchHit.highlights` now supports multiple highlight snippets per field (arrays).  

- **dbt platform**
  - **Deprecations**: The "Adaptive" job type is deprecated. `last_checked_at` is deprecated and no longer populated in run responses.  

- **Canvas**
  - **Existing CSV upload SSE endpoint deprecated**: Migrate to the new two-step [upload source](/docs/cloud/use-canvas#upload-data-to-canvas) flow.