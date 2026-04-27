---
title: "Develop with dbt Copilot"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

import CopilotResources from '/snippets/_use-copilot-resources.md';
import CopilotEditCode from '/snippets/_use-copilot-edit-code.md';

# Develop with dbt Copilot <Lifecycle status="self_service,managed,managed_plus" /> 


This page describes how to use <Constant name="copilot" /> in the <Constant name="studio_ide" /> to improve your development workflow.


Use [<Constant name="copilot" />](/docs/cloud/dbt-copilot) in the [<Constant name="studio_ide" />](/docs/cloud/studio-ide/develop-in-studio) to generate documentation, tests, semantic models, metrics, and SQL code from scratch &mdash; making it easier for you to build your dbt project, accelerate your development, and focus on high-level tasks. For information about using <Constant name="copilot" /> in the [<Constant name="canvas" />](/docs/cloud/canvas), see [Build with <Constant name="copilot" />](/docs/cloud/build-canvas-copilot).

## Developer agent <Lifecycle status="beta,self_service,managed,managed_plus" />

For autonomous model generation, refactoring, and multi-step workflows in the <Constant name="studio_ide" />, see the [<Constant name="dev_agent" />](/docs/dbt-ai/developer-agent). 

The <Constant name="dev_agent" /> is accessible from the Copilot panel. Switch to **Ask** or **Code** mode to activate the agent.


<video width="100%" controls autoPlay muted loop playsInline>
  <source src="/img/docs/dbt-cloud/dev-agent.mp4" type="video/mp4" />
  Your browser does not support the video tag.
</video>
Example of using the Developer agent to refactor a model in the Studio IDE.


## Generate resources

<CopilotResources/>

## Generate and edit code

<CopilotEditCode/>
