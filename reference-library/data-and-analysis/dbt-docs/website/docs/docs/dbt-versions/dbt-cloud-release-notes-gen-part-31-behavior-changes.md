## Behavior Changes

### Orchestration and Run Status

- **Model timing unavailable for dbt Fusion runs:** You now see an informational notice instead of the Model timing chart for dbt Fusion runs because dbt Fusion handles threading differently.

### APIs, Identity, and Administration

- **System for Cross-domain Identity Management (SCIM) `id` fields are now strings:** SCIM schema discovery now reports `id` fields as strings for users and groups.