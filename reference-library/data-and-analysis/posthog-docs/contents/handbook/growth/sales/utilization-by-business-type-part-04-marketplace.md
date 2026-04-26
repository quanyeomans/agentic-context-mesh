## Marketplace

### Common business problems & personas

#### Key business problems
- **Supply-demand imbalance** – Too many buyers/sellers on one side of the marketplace
- **Low trust and safety** – Users concerned about fraud or poor quality
- **Poor matching algorithms** – Buyers and sellers not connecting effectively
- **High customer acquisition costs** – Expensive to acquire both buyers and sellers
- **Network effects challenges** – Difficulty achieving critical mass
- **Payment and escrow issues** – Complex payment flows and trust concerns
- **Quality control problems** – Inconsistent service/product quality
- **Geographic expansion challenges** – Difficulty scaling to new markets

#### Primary personas & their pain points

**Marketplace Operations Managers**
- **Pain points:** Can't balance supply and demand, struggle with quality control, lack visibility into marketplace health
- **PostHog solutions:** Supply-demand analytics, quality metrics tracking, marketplace health dashboards, operational insights

**Trust & Safety Teams**
- **Pain Points:** High fraud rates, poor user verification, difficulty identifying bad actors
- **PostHog Solutions:** Fraud detection patterns, user behavior analysis, trust score tracking, automated risk alerts

**Product Managers**
- **Pain points:** Poor matching algorithms, low user engagement, ineffective search and discovery
- **PostHog solutions:** User behavior analysis, search optimization, matching algorithm improvement, engagement tracking

**Growth Teams**
- **Pain Points:** High CAC for both sides, poor network effects, slow marketplace growth
- **PostHog Solutions:** Network effects measurement, growth loop optimization, viral coefficient tracking, user acquisition analysis

**Customer Success Teams**
- **Pain Points:** High support volume, poor user satisfaction, difficulty resolving disputes
- **PostHog Solutions:** User journey analysis, satisfaction tracking, dispute resolution insights, support optimization

### Key metrics & PostHog

#### GMV (Gross Merchandise Value)
**Importance:** Represents the total value of all transactions between buyers and sellers on the platform over a specific period. It is the primary indicator of a marketplace's scale, liquidity, and overall health, reflecting its ability to facilitate transactions and generate value for its users.

**PostHog approach:** Track marketplace transaction events like `listing_viewed`, `booking_requested`, and `transaction_completed` with properties such as category, price, seller_id, and buyer_id. Integrate with payment processors for comprehensive data. Use PostHog to create real-time GMV dashboards with breakdowns by category, set up seller and buyer performance tracking, conduct cohort analysis to monitor marketplace growth, and create alerts for unusual transaction patterns. Non-technical users can use autocapture to track listing views and booking requests, creating funnels to analyze the path to a completed transaction and using session recordings to optimize the user journey.

#### Take Rate
**Importance:** The percentage of GMV that the marketplace captures as revenue (commission or fees). It is a crucial metric for understanding the marketplace's business model effectiveness and profitability. Optimizing the take rate is key to sustainable growth.

**PostHog approach:** Track commission events like `commission_earned` from `transaction_completed` events, with properties for transaction amount, commission percentage, and category. Analyze revenue and profitability by category on dashboards. This allows for identifying opportunities to optimize the take rate, for example by analyzing its drivers with correlation analysis and setting up alerts for significant changes. Non-technical users can build dashboards to monitor take rate trends across different product categories or seller tiers, helping to inform pricing strategy without writing any code.

#### Supply/Demand Balance
**Importance:** Measures the equilibrium between the number of sellers (supply) and buyers (demand) on the platform. A balanced marketplace ensures a good user experience for both sides, preventing situations like too few products for buyers or too few customers for sellers, which can lead to churn.

**PostHog approach:** Track supply-side events (`listing_created`, `service_offered`) and demand-side events (`search_performed`, `booking_requested`). Use properties like category, location, and search terms to analyze supply-demand gaps on dashboards. Funnel analysis can reveal booking conversion rates, while alerts can notify of imbalances, helping to identify and act on new market opportunities. Non-technical users can create dashboards that visualize searches with no results, providing a simple way to spot unmet demand and guide supply-side growth efforts.

#### Network Effects
**Importance:** Measures how the value of the platform increases for users as more people use it. Strong network effects create a powerful competitive advantage (a "moat") and are the engine of sustainable, viral growth for marketplaces. It's what makes a marketplace more valuable as it scales.

**PostHog approach:** Track network interaction events like `user_referred`, `invitation_accepted`, and `cross_side_activity` (e.g., a user being both a buyer and seller). Use properties to distinguish user types. Dashboards can visualize network growth and viral coefficients. Cohort analysis is key to measuring how network effects develop over time for different user groups, and alerts can highlight opportunities for growth. Non-technical users can use autocapture on referral pages and share buttons to analyze the effectiveness of viral loops and optimize the user flow with session recordings.

#### Trust & Safety Metrics
**Importance:** Trust is the currency of a marketplace. These metrics, such as user ratings, review rates, fraud reports, and dispute rates, measure the level of safety and reliability on the platform. High trust is essential for encouraging transactions, retaining users, and building a strong brand reputation.

**PostHog approach:** Track trust-related events like `review_submitted`, `dispute_filed`, and `fraud_detected`, enriched with properties on user reputation and transaction history. Dashboards can monitor trust scores and fraud rates. Session recordings are invaluable for investigating suspicious user behavior and understanding how trust is built (or broken) in user flows. Set up alerts for fraud signals and use correlation analysis to identify key indicators of trust. Non-technical users can create surveys to collect user feedback on trust and use session recordings to review the user journey for those who file disputes.