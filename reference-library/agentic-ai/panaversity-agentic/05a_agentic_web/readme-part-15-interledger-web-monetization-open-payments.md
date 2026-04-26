## Interledger / Web Monetization / Open Payments

* **What it is:** An open, ledger-agnostic **payment interop layer** for *streaming micro-payments* across providers; **Web Monetization** is a browser/agent API that sends tiny real-time payments to a payment pointer; **Open Payments** defines standardized APIs for wallets and receipts.
* **Why it fits agents:** Pull-based streams per session/URL match per-invocation billing; you can meter and stop anytime; no blockchain lock-in.
* **Where to start:**

  * Interledger “Web Monetization” explainer/spec shows pay-as-you-use streaming via wallet/payment pointer. ([Interledger Foundation][1]) ([webmonetization.org][2])
  * Open Payments is the API layer Web Monetization uses under the hood. ([Interledger Foundation][3])
  * Recent updates show **Open Payments** being used by the Web Monetization extension in 2025. ([The Interledger Community][4])

**Agent pattern:** Give your agent a wallet/payment pointer; when it invokes a tool, it streams a few cents/second while the tool is in use; stop on completion or on policy breach; attach receipts to the billing trace.