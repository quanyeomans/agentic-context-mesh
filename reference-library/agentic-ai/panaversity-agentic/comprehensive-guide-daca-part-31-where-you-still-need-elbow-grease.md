## Where you still need elbow-grease

* **Prompt & context evaluation pipelines** – DACA tells you to version prompts but not *how* to run automatic regression tests. Borrow a lightweight eval harness (e.g., zeno-eval) and run it in CI.
* **Observability** – Factor 9 assumes error summaries, but you’ll want full OpenTelemetry traces across actor calls. Add a side-car collector and push to Grafana.
* **Security & policy** – A2A/MCP give you message integrity, not zero-trust. Layer Istio mTLS, OPA policies, and secret rotation to stay enterprise-ready.
* **Cost transparency** – DACA’s free-tier focus hides Factor 11’s need for FinOps. Install Kubecost early so scale-up surprises don’t hit your CFO later.

---

### Next steps

1. **Create a repo structure**: `/prompts`, `/workflows`, `/tests`, `/charts`.
2. **Wrap the human dashboard as a formal MCP tool** so the LLM always outputs a consistent schema.
3. **Automate replay-from-events** in unit tests to prove your reducer purity (Factor 12).
4. **Turn on distributed tracing** in Dapr with `--enable-metrics --enable-tracing` flags and export to Tempo or Jaeger.

Following these tweaks, DACA doesn’t just *align* with 12-Factor Agents—it becomes a production-grade, planet-scale implementation of them. 🚀

[1]: https://github.com/humanlayer/12-factor-agents/blob/main/content/factor-1-natural-language-to-tool-calls.md "factor-1-natural-language-to-tool-calls.md - GitHub"
[2]: https://github.com/humanlayer/12-factor-agents "12-Factor Agents - Principles for building reliable LLM applications"