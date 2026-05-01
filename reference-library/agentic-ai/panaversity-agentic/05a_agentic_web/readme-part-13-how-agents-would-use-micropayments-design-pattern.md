# How agents would *use* micropayments (design pattern)

1. **Budgeting & policy.** A principal agent receives a user goal and a **spend policy** (max budget, per-call caps, approved counterparties).
2. **Discovery & ranking.** The agent queries a registry; candidate services expose price signals (fixed fee, dynamic auction, rev-share). Ranking may include *price × quality × reliability*.&#x20;
3. **Just-in-time payment.** On selection, the agent streams or posts a small payment per invocation (see solutions below).
4. **Attribution trail.** Every sub-call appends to a tamper-evident **billing trace**, enabling end-to-end cost breakdown to the originating user.&#x20;
5. **Reconciliation.** The CABL tallies micro-fees across agents/tools and closes channels/settles periodically.&#x20;