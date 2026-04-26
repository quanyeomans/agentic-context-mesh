## E-commerce

### Common business problems & personas

#### Key business problems
- **High cart abandonment rates** – Customers adding items but not completing purchases
- **Low conversion rates** – Visitors not converting to customers
- **Poor product discovery** – Customers unable to find products they want
- **High return rates** – Products being returned frequently
- **Seasonal inventory issues** – Over/under stocking during peak periods
- **Poor mobile experience** – Mobile users having difficulty shopping
- **Low customer lifetime value** – Customers making only one purchase
- **Ineffective marketing attribution** – Difficulty tracking which campaigns drive sales

#### Primary personas & their pain points

**E-commerce Managers**
- **Pain points:** Can't identify why customers abandon carts, struggle to optimize product pages, lack visibility into customer journey
- **PostHog solutions:** Cart abandonment funnels, session recordings for UX insights, conversion rate optimization, customer journey mapping

**Marketing Teams**
- **Pain Points:** Poor campaign attribution, difficulty measuring marketing ROI, ineffective retargeting campaigns
- **PostHog Solutions:** UTM tracking, conversion funnel analysis, cohort analysis by traffic source, retargeting audience creation

**Product Teams**
- **Pain Points:** Poor product page performance, low product discovery, ineffective search functionality
- **PostHog Solutions:** Product page heatmaps, search behavior tracking, product recommendation optimization, A/B testing for product pages

**Customer Service Teams**
- **Pain Points:** High support ticket volume, difficulty understanding customer issues, poor customer satisfaction
- **PostHog Solutions:** Customer journey analysis, session recordings for issue identification, customer satisfaction tracking, support ticket correlation

**Inventory Managers**
- **Pain Points:** Poor demand forecasting, seasonal inventory issues, over/under stocking
- **PostHog Solutions:** Product performance tracking, demand pattern analysis, seasonal trend identification, inventory optimization insights

### Key metrics & PostHog

#### GMV (Gross Merchandise Value)
**Importance:** Represents the total value of all goods sold over a specific period. GMV is the primary measure of an e-commerce platform's scale and is essential for understanding top-line growth and market share.

**PostHog approach:** Track all purchase events (`product_viewed`, `add_to_cart`, `purchase_completed`) with properties like product category, price, and quantity. Connect to your e-commerce platform for comprehensive data. Create dashboards for real-time GMV tracking and product performance analysis by category. Use cohort analysis to track customer value over time and set up alerts for unusual GMV patterns. For non-technical users, autocapture on product pages and "add to cart" buttons can track the conversion journey and identify popular products, with session recordings helping to optimize product pages.

#### AOV (Average Order Value)
**Importance:** The average amount spent each time a customer places an order. Increasing AOV is a key strategy for maximizing revenue without increasing the number of customers, directly impacting profitability.

**PostHog approach:** Track cart and purchase events (`cart_updated`, `purchase_completed`) with properties like cart value and discount applied. Use funnel analysis to optimize the cart and identify abandonment points. A/B test pricing and product recommendations to find effective upselling strategies. Use correlation analysis to identify behaviors of customers with high AOV. Non-technical users can use autocapture to track interactions on the cart page, analyze abandonment patterns, and use session recordings to optimize the checkout flow.

#### Conversion Rate
**Importance:** The percentage of visitors who complete a purchase. This is a critical metric for gauging the effectiveness of the entire customer journey, from landing page to checkout, and is a primary indicator of site performance and user experience.

**PostHog approach:** Track all steps in the conversion funnel (`page_viewed`, `product_viewed`, `add_to_cart`, `checkout_started`, `purchase_completed`) with properties like traffic source and device type. Create comprehensive conversion funnels to identify drop-off points and use session recordings to understand checkout friction. A/B test checkout flows and product pages to optimize the user path. Non-technical users can use autocapture to track all funnel page visits and interactions, creating funnels and using session recordings to optimize conversion paths with no code.

#### Cart Abandonment
**Importance:** The rate at which users add items to their cart but leave without completing the purchase. A high cart abandonment rate often indicates friction in the checkout process, unexpected costs, or a poor user experience.

**PostHog approach:** Track cart interactions like `add_to_cart` and `remove_from_cart`. Use session recordings to understand the behavior of users who abandon their carts, and implement exit-intent surveys to gather direct feedback on abandonment reasons. Create funnels that specifically track the checkout process to pinpoint exact drop-off points. This data can inform cart abandonment recovery strategies. Non-technical users can use autocapture to track all cart page interactions and build abandonment funnels to analyze user behavior.

#### Customer Lifetime Value
**Importance:** The total revenue a business can expect from a single customer throughout their relationship. CLV is vital for making strategic decisions about marketing spend, customer acquisition, and retention efforts, ensuring long-term profitability.

**PostHog approach:** Track all customer interactions, including `purchase_completed`, `return_requested`, and `support_contacted`, with properties like purchase history and acquisition source. Create cohort analyses by acquisition month to understand how customer value evolves. Use correlation analysis to identify behaviors of high-value customers and PostHog's predictive analytics for CLV forecasting. Non-technical users can use autocapture on account and order history pages to track engagement patterns and use session recordings to understand high-value customer behavior.