## Fixes

### APIs, Identity, and Administration

- **GitHub webhook secret null check before signature validation**: The GitHub webhook endpoint now correctly checks for a null webhook secret before attempting to validate the request signature, preventing a crash when a repository's webhook secret is not set.

- **`github_installation_id` and `github_webhook_id` support large values**: These repository fields have been promoted from 32-bit to 64-bit integers (`BigIntegerField`) to accommodate GitHub installation and webhook IDs that exceed the 32-bit integer range.