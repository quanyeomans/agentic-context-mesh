## Behavior Changes

### dbt platform APIs

- **Removed credential configuration fields from responses**: Profiles API responses no longer include credential configuration and extended attributes; use the appropriate credentials and configuration endpoints instead.

- **Filter connections by Private Endpoint**: Account Connections list supports filtering by Private Endpoint identifier for easier management.

- **Additional ordering options**: Private Endpoints list now supports ordering by endpoint state and connection count.

- **Private Link: Updated license permission defaults**: User licenses now include read access for Private Link resources, which may change who can view Private Link related settings.

### Studio IDE

- **Metric generation writes directly to active file**: Generated metrics are now written directly into the active model file instead of using an accept and reject diff flow.