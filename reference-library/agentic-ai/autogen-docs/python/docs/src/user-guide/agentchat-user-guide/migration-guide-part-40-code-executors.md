## Code Executors

The code executors in `v0.2` and `v0.4` are nearly identical except
the `v0.4` executors support async API. You can also use
{py:class}`~autogen_core.CancellationToken` to cancel a code execution if it takes too long.
See [Command Line Code Executors Tutorial](../core-user-guide/components/command-line-code-executors.ipynb)
in the Core API documentation.

We also added `ACADynamicSessionsCodeExecutor` that can use Azure Container Apps (ACA)
dynamic sessions for code execution.
See [ACA Dynamic Sessions Code Executor Docs](../extensions-user-guide/azure-container-code-executor.ipynb).