---
title: "Basic Webauthn Testing"
source: Openlane GRC Platform
source_url: https://github.com/theopenlane/core
licence: Apache-2.0
domain: security
subdomain: openlane-grc
date_added: 2026-04-25
---

# Basic Webauthn Testing

1. Run from the root of the repository
    ```
    go run internal/testutils/login/webauthn/main.go
    ```
1. Go to [http://localhost:5500](http://localhost:5500) in your browser
1. Register a user
1. Login with user
