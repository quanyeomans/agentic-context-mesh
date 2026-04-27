## Where DACA only *partly* helps or leaves a gap

| Limitation not fully solved | Why it remains | Notes / potential work-arounds |
| --- | --- | --- |
| **Advanced data-privacy & regulatory compliance** | DACA prescribes tech (Cockroach, Postgres, Redis) but not automatic GDPR/PCI/HIPAA controls or cross-region data-sovereignty guarantees. You must layer policy-as-code tools (OPA, Kyverno), DLP gateways, and audit pipelines yourself. |
| **24 × 7 “always-on” agents without paying VM prices** | Stateless pods can scale to zero, but long-lived actors still need a warm host. ACA/Kubernetes HPA can down-scale to one replica, not zero, and serverless containers charge for idle storage time. Innovative “activated-on-message” patterns or edge-cache co-location are still research areas. |
| **Extreme edge-latency (sub-10 ms)** | DACA’s event bus improves intra-cluster latency, yet physics on WAN hops still dominate. True autonomy for self-driving cars, robotic swarms, etc., requires edge-deployed mini-clusters or on-device inferencing. |
| **Built-in data-lifecycle tooling (retention, PII redaction)** | Dapr exposes state APIs but does not enforce retention windows or schema-based PII detection. External retention managers or database-native features must be configured. |
| **Cost transparency across clouds** | Free-tiers help prototypes, but once you burst onto GPUs or vector DBs, billing is opaque. FinOps dashboards or K8s cost-allocation operators (e.g., Kubecost) are still needed. |
| **Security hardening & zero-trust posture** | A2A/MCP inherit HTTP(S) and token auth, but DACA does not prescribe mTLS, workload identity federation, or policy-based admission. Those must be added via Istio, OPA, SPIRE, etc. |
| **Inter-vendor workflow portability** | While open protocols reduce lock-in, managed LLM APIs (OpenAI, Gemini) still differ in pricing, rate limits, and capability sets; re-testing and fallback routing remain your burden. |

---