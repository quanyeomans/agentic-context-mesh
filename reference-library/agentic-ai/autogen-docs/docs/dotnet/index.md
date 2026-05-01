---
title: "optionally - for distributed agent systems:"
source: Microsoft AutoGen
source_url: https://github.com/microsoft/autogen
licence: CC-BY-4.0
domain: agentic-ai
subdomain: autogen-docs
date_added: 2026-04-25
---

<h1>AutoGen .NET</h1>
    <p class="subheader">
    A .NET framework for building AI agents and applications
    </p>


  
    
      
        <h5 class="card-title">Core</h5>
<p>


</p>
        <p class="card-text">An event-driven programming framework for building scalable multi-agent AI systems.</p>

- Deterministic and dynamic agentic workflows for business processes
- Research on multi-agent collaboration
- Distributed agents for multi-language applications
- integration with event-driven, cloud native applications

*Start here if you are building workflows or distributed agent systems*

<p>

<pre id="codecell0" tabindex="0">

```bash
dotnet add package Microsoft.AutoGen.Contracts
dotnet add package Microsoft.AutoGen.Core

# optionally - for distributed agent systems:
dotnet add package Microsoft.AutoGen.RuntimeGateway.Grpc
dotnet add package Microsoft.AutoGen.AgentHost

# other optional packages
dotnet add package Microsoft.AutoGen.Agents
dotnet add package Microsoft.AutoGen.Extensions.Aspire
dotnet add package Microsoft.AutoGen.Extensions.MEAI
dotnet add package Microsoft.AutoGen.Extensions.SemanticKernel
```

</pre></p>
<p>
        [Get started](core/index.md)
      
    
  
  
    
      
        <h5 class="card-title">AgentChat</h5>
        <p class="card-text">A programming framework for building conversational single and multi-agent applications. Built on Core.</p>
        [Coming soon](#)
