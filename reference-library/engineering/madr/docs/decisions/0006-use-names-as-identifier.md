---
title: "Use Names as Identifier"
source: Markdown ADR (MADR)
source_url: https://github.com/adr/madr
licence: MIT
domain: engineering
subdomain: madr
date_added: 2026-04-25
---

# Use Names as Identifier

## Context and Problem Statement

An option is listed at "Considered Options" and repeated at "Pros and Cons of the Options". Finally, the chosen option is stated at "Decision Outcome".

## Decision Drivers

* Easy to read
* Easy to write
* Avoid copy and paste errors

## Considered Options

* Repeat all option names if they occur
* Assign an identifier to an option, e.g., `[A] Use gradle as build tool`

## Decision Outcome

Chosen option: "Repeat all option names if they occur", because 1) there is no markdown standard for identifiers, 2) the document is harder to read if there are multiple options which must be remembered.
