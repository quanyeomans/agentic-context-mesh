---
title: "Naming Branches"
source: Microsoft Code-With Engineering Playbook
source_url: https://github.com/microsoft/code-with-engineering-playbook
licence: CC-BY-4.0
domain: engineering
subdomain: microsoft-code-with-playbook
date_added: 2026-04-25
---

# Naming Branches

When contributing to existing projects, look for and stick with the agreed branch naming convention. In open source projects this information is typically found in the contributing instructions, often in a file named `CONTRIBUTING.md`.

In the beginning of a new project the team agrees on the project conventions including the branch naming strategy.

Here's an example of a branch naming convention:

```sh
/[feature/bug/hotfix]/<work item ID>_<title>
```

Which could translate to something as follows:

```sh
dickinson/feature/271_add_more_cowbell
```

The example above is just that - an example. The team can choose to omit or add parts. Choosing a branch convention can depend on the development model (e.g. [trunk-based development](https://trunkbaseddevelopment.com/)), [versioning](component-versioning.md) model, tools used in managing source control, matter of taste etc. Focus on simplicity and reducing ambiguity; a good branch naming strategy allows the team to understand the purpose and ownership of each branch in the repository.
