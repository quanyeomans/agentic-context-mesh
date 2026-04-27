## Bitcoin Lightning Network (plus Taproot Assets for stablecoins)

* **What it is:** A layer-2 network of payment channels enabling **instant, low-fee micro-transactions**; now increasingly used for **machine-to-machine** payments (IoT, pay-per-minute services). ([trustmachines.co][5]) ([yellow.com][6])
* **Stable value:** Taproot Assets enables **USD-like stablecoin** rails over Lightning, reducing BTC volatility exposure; 2025 brought notable adoption signals. ([Aurpay][7])
* **Agent pattern:** Maintain a Lightning wallet; open channels to popular services; pay per call or stream ppm/pps; settle off-chain, reconcile periodically on-chain. Good for low-latency, high-frequency calls. ([lightspark.com][8]) ([SimpleSwap][9])

> Other candidates you’ll see: account-to-account **Open Banking** for larger, batched settlements; **in-app credits** (centralized ledgers) for closed ecosystems. They’re less “micro,” but can backstop retries and refunds.