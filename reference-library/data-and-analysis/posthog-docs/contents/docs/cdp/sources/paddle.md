---
title: "Linking Paddle as a source"
source: PostHog Documentation
source_url: https://github.com/PostHog/posthog.com
licence: MIT
domain: data-and-analysis
subdomain: posthog-docs
date_added: 2026-04-25
---

The Paddle connector syncs customers, discounts, prices, products, subscriptions, transactions, and adjustments from your Paddle account into PostHog.

## Adding a data source

1. Go to the [sources tab](https://app.posthog.com/data-management/sources) of the data pipeline section in PostHog.

2. Click **+ New source** and then click **Link** next to Paddle.

3. Next, you need an API key from Paddle. In your Paddle dashboard, go to **Developer tools** > **Authentication** and click **New API key**. Give the key a name and grant it **Read** access to the following entities:

   - Customers
   - Discounts
   - Prices
   - Products
   - Subscriptions
   - Transactions
   - Adjustments

   Copy the generated API key (it starts with `pdl_live_...` for live keys or `pdl_sdbx_...` for sandbox keys).

4. Back in PostHog, paste the API key into the **API Key** field and click **Next**.

5. Select the tables you want to sync and modify the method and frequency as needed. Once done, click **Import**.

Once the syncs are complete, you can start using Paddle data in PostHog.

## Sync methods

Only the `transactions` table supports incremental and append-only syncing, using `billed_at` as the replication key. All other tables (customers, discounts, prices, products, subscriptions, and adjustments) are synced via full table refresh.
