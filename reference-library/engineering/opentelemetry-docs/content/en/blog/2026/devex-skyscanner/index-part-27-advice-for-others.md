## Advice for others

Based on their production experience, the Skyscanner team offers this advice:

- **Start simple**: Begin with just the memory limiter, batch processor, and
  basic exporters. Add complexity only as needs arise.
- **Memory limiter from day one**: Set this up immediately to prevent memory
  issues as you scale.
- **Consider filter processors early**: Understand your application's status
  code semantics and filter out high-volume "false positives" to control costs.
- **Don't over-engineer resiliency**: For telemetry data, simple in-memory
  batching is often sufficient.
- **Gradual rollouts catch issues**: Progressive promotion across environment
  tiers provides valuable validation.