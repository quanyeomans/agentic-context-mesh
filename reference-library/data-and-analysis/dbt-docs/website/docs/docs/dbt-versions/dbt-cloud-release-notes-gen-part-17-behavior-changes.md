## Behavior changes

### APIs, Identity, and Administration

- **Fusion migration gated by API availability**: The Fusion migration checklist, the Enable Fusion Environments page, and the "Enable Fusion" button in Studio IDE now use the `is_migration_available` field from the Fusion status API instead of the legacy `orc2609ShowFusionToggle` feature flag. Fusion migration UI is shown only when the backend has marked the project as ready for migration.