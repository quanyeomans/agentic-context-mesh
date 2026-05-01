## Fixes

### Catalog

- **More reliable file tree loading**: Catalog no longer gets stuck loading the file tree on initial page load.
- **Clearer trust signals**: Trust signals now suppress less-severe upstream-source issues when a more severe issue is present, so badges and messages are easier to interpret.

### Integrations

- **Clearer deploy key decryption errors**: When dbt platform cannot decrypt a deploy key, you now get a clearer failure instead of a generic git credentials error.

### Studio IDE

- **Cleaner LSP disconnects**: If authentication fails when you connect to the Language Server Protocol (LSP) WebSocket, the connection now closes cleanly instead of failing with an internal server error, so you should see fewer unexpected disconnects.
- **Improved timeout handling and authentication stability**: Reduced environment setup timeouts and resolved intermittent authentication failures during busy periods.
- **Clearer invalid credentials error**: If your development connection credentials are invalid, you now see a clearer error message to help you diagnose the issue faster.