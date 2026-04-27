---
title: "All-In-One"
source: Openlane GRC Platform
source_url: https://github.com/theopenlane/core
licence: Apache-2.0
domain: security
subdomain: openlane-grc
date_added: 2026-04-25
---

# All-In-One

This dockerfile builds an image that contains necessary prereqs for the entire stack. Currently it will run the following:
- OpenFGA with in-memory data store
- Redis
- Core API connected to local FGA instance
