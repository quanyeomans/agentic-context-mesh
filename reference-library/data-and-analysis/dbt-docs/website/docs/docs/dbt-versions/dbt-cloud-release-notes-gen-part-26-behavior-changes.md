## Behavior Changes

### Studio IDE

- **Updated file search and command shortcuts:** Studio IDE now uses VS Code Quick Open for file search (`Cmd+P` or `Ctrl+P`) and the VS Code Command Palette (`Cmd+Shift+P` or `Ctrl+Shift+P`) instead of the legacy Studio dialogs.

### Integrations

- **Disallowed `MIN()` and `MAX()` for metrics and dimensions:** Tableau and Power BI queries can no longer request `MIN()` or `MAX()` for a metric or dimension (except time min-max queries), and you now receive a clear error if you attempt it.