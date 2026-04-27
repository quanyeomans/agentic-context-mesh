## Content/Media

### Common business problems & personas

#### Key business problems
- **Low content engagement** – Users not consuming or interacting with content
- **Poor content discovery** – Users unable to find relevant content
- **Low subscription conversion** – Free users not converting to paid subscribers
- **Poor ad performance** – Low click-through rates and ad revenue
- **High content production costs** – Expensive to create quality content
- **Poor user retention** – Users not returning to consume more content
- **Ineffective content recommendations** – Poor personalization algorithms
- **Seasonal content challenges** – Difficulty maintaining engagement year-round

#### Primary personas & their pain points

**Content Teams**
- **Pain points:** Low content engagement, poor content discovery, high production costs
- **PostHog solutions:** Content performance analytics, engagement tracking, content discovery optimization, ROI measurement

**Product Managers**
- **Pain points:** Poor user retention, ineffective recommendations, low subscription conversion
- **PostHog solutions:** Retention analysis, recommendation algorithm optimization, conversion funnel analysis, user journey mapping

**Marketing Teams**
- **Pain points:** Poor ad performance, low subscription conversion, ineffective content marketing
- **PostHog solutions:** Ad performance tracking, conversion optimization, content marketing ROI, audience segmentation

**Editorial Teams**
- **Pain points:** Poor content performance, difficulty understanding audience preferences, seasonal engagement challenges
- **PostHog solutions:** Content performance analytics, audience preference analysis, seasonal trend identification, editorial optimization

**Revenue Teams**
- **Pain points:** Low subscription revenue, poor ad performance, difficulty monetizing content
- **PostHog solutions:** Revenue analytics, subscription optimization, ad performance tracking, monetization strategy insights

### Key metrics & PostHog

#### Engagement Rate
**Importance:** Measures how actively users are interacting with content beyond just viewing it (e.g., likes, shares, comments, time spent). It's a key indicator of content quality and audience resonance. High engagement suggests that the content is valuable, which is crucial for building a loyal audience and driving retention.

**PostHog approach:** Track engagement events like `content_viewed`, `time_spent_on_page`, `video_played_to_75%`, and `article_shared`. Use properties to segment by content type and user segment. A custom "engagement score" can be created using formulas in PostHog to weigh different interactions. Cohort analysis can track how engagement evolves for different user groups. Non-technical editors can use dashboards to see which articles are most engaging to inform their content strategy.

#### Content Performance
**Importance:** Provides a holistic view of how individual pieces of content contribute to business goals, from views to conversions. Understanding what content performs well is essential for optimizing content strategy, allocating production resources effectively, and maximizing the ROI of content creation.

**PostHog approach:** Track the content lifecycle with events like `content_published`, `content_viewed`, and `content_shared`, enriched with properties like category, author, and format. Use correlation analysis to identify the attributes of successful content (e.g., "how-to" articles over 1500 words drive the most shares). Dashboards can rank content by performance, and alerts can notify teams when a piece of content starts trending. Non-technical content teams can use these insights to double down on what works.

#### User Retention
**Importance:** Measures the percentage of users who return to the platform over time. For media companies, retention is the lifeblood of the business, as it's far more cost-effective than acquisition. High retention indicates that users find ongoing value in the content, which is key for long-term growth and subscription revenue.

**PostHog approach:** Track retention by monitoring `user_returned` or `session_started` events. Use PostHog's retention cohorts to analyze how retention differs by acquisition source or first content consumed. Correlation analysis can identify behaviors (e.g., subscribing to a newsletter) that are leading indicators of retention. Churn prediction models can help proactively identify at-risk users. Non-technical marketers can use cohorts to understand the long-term value of users from different campaigns.

#### Ad Revenue
**Importance:** For ad-supported media companies, this metric directly measures financial performance. Optimizing ad revenue involves balancing user experience with monetization, making it crucial to track metrics like impressions, click-through rates (CTR), and revenue per user.

**PostHog approach:** Track ad-related events like `ad_impression`, `ad_click`, and `ad_revenue_generated`. Use properties to segment by ad type, placement, and user segment. A/B test different ad placements and formats to see what generates the most revenue without harming engagement. Dashboards can monitor ad performance in real-time, and alerts can flag underperforming ad units. Non-technical revenue teams can use these dashboards to track progress against revenue goals.

#### Subscription Metrics
**Importance:** For subscription-based media companies, metrics like conversion rate, subscriber LTV, and churn are the ultimate measure of business health. They track the ability to convert casual readers into paying subscribers and retain them, directly reflecting the perceived value of the premium offering.

**PostHog approach:** Track the entire subscription funnel with events like `paywall_hit`, `subscription_started`, `subscription_renewed`, and `subscription_cancelled`. Use funnel analysis to identify drop-off points in the conversion process and properties like plan type to segment subscribers. Cohort analysis is essential for tracking subscriber LTV and churn over time. Non-technical product managers can use funnels to optimize the checkout flow and A/B test different paywall strategies.