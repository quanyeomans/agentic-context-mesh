---
title: "Write Own TOC Tool"
source: Markdown ADR (MADR)
source_url: https://github.com/adr/madr
licence: MIT
domain: engineering
subdomain: madr
date_added: 2026-04-25
---

# Write Own TOC Tool

## Context and Problem Statement

ADRs have to be indexed somehow. E.g., for offering a website showing all ADRs.

## Considered Options

* Write own tool `adr-log`
* Use `adr-tools`' TOC functionality

## Decision Outcome

Chosen option: "Write own tool `adr-log`", because

* we want to have the format `ADR-0001 - Title` in the TOC.
* `adr-tools` offers `title` only.

We accept that changing `adr-tools` would also be possible.
It is prepared to included header and footer: <https://github.com/npryce/adr-tools/blob/master/tests/generate-contents-with-header-and-footer.sh>.

### Consequences

* Good, because `adr-log` is installable using `npm install -g adr-log`, which is easier than installing `adr-tools`.
* Bad, because another tool has to be maintained
