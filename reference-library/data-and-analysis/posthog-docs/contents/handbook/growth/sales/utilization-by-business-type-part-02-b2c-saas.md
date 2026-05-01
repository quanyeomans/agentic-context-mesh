## B2C SaaS

### Common business problems & personas

#### Key business problems
- **High user churn** – Consumers canceling subscriptions after initial excitement
- **Low activation rates** – Users not completing key onboarding steps
- **Poor user engagement** – Users not returning to use the product regularly
- **High customer acquisition costs** – Expensive to acquire individual consumers
- **Low viral coefficient** – Users not referring friends and family
- **Poor mobile experience** – Mobile users having difficulty with the product
- **Seasonal usage patterns** – Inconsistent usage throughout the year
- **Difficulty scaling support** – High volume of individual user support requests

#### Primary personas & their pain points

**Product Managers**
- **Pain points:** High user churn, low activation rates, poor user engagement, difficulty understanding consumer behavior
- **PostHog solutions:** User behavior analysis, activation funnel optimization, engagement tracking, consumer journey mapping

**Growth Teams**
- **Pain Points:** High CAC, low viral coefficient, poor user acquisition, difficulty scaling growth
- **PostHog Solutions:** CAC analysis, viral coefficient tracking, user acquisition optimization, growth loop identification

**Customer Success Teams**
- **Pain Points:** High support volume, poor user satisfaction, difficulty scaling support, low user retention
- **PostHog Solutions:** Support ticket analysis, user satisfaction tracking, automated support optimization, retention analytics

**Marketing Teams**
- **Pain Points:** Poor campaign attribution, high CAC, ineffective user acquisition, seasonal usage challenges
- **PostHog Solutions:** Campaign attribution tracking, CAC optimization, user acquisition analysis, seasonal trend identification

**Mobile Teams**
- **Pain Points:** Poor mobile experience, low mobile engagement, mobile-specific bugs, app store optimization
- **PostHog Solutions:** Mobile experience analysis, mobile engagement tracking, mobile bug monitoring, app store performance analytics

### Key metrics & PostHog
#### User Activation Rate
**Importance:** Measures the percentage of new users who complete key onboarding steps and experience the product's core value. High activation is crucial for retention and indicates a successful onboarding experience.

**PostHog approach:** Track activation events (`account_created`, `onboarding_completed`) with properties like activation step and acquisition source. Use funnel analysis to optimize time-to-value, and cohort analysis to track activation rates on dashboards. Session recordings can help identify activation friction points, and alerts can be set for activation rate drops. Non-technical users can use autocapture for onboarding page visits and tutorial interactions to create no-code funnels and analyze user behavior.

#### Daily/Monthly Active Users (DAU/MAU)
**Importance:** Measures user engagement and product stickiness by tracking the number of unique users who interact with the product on a daily or monthly basis. A high DAU/MAU ratio indicates strong, consistent user value.

**PostHog approach:** Track user activity events like `session_started` and `feature_used` with properties such as user segment and session duration. Create dashboards for real-time DAU/MAU tracking and trend analysis. Calculate stickiness (DAU/MAU ratio) and use cohort analysis to track engagement over time. Alerts can be configured for significant engagement drops. Autocapture can track page visits and feature interactions, enabling non-technical users to analyze engagement patterns and identify popular features.

#### Customer Lifetime Value (CLV)
**Importance:** Represents the total revenue a business can expect from a single customer account throughout their relationship. CLV is a key indicator of long-term profitability and customer loyalty.

**PostHog approach:** Track all revenue events (`subscription_started`, `purchase_made`) with properties like purchase amount and acquisition source. Use cohort analysis to analyze CLV by acquisition month and correlation analysis to identify high-value behaviors. PostHog's predictive analytics can be used for CLV forecasting. For non-technical users, autocapture on purchase pages and upgrade buttons helps track the user journey to purchase and identify which features drive upgrades, with session recordings providing insights into purchase behavior.

#### Viral Coefficient
**Importance:** Measures the number of new users an existing user generates, indicating the effectiveness of viral loops and word-of-mouth growth. A coefficient greater than one signifies exponential growth.

**PostHog approach:** Track viral events like `referral_sent` and `invitation_accepted` with properties such as referral type and conversion rate. Use funnel analysis to optimize referral flows and A/B test referral incentives and messaging. Dashboards can show viral coefficient trends. Non-technical users can use autocapture to track share button clicks and referral page visits, using session recordings to understand and optimize referral behavior.

#### User Retention Rate
**Importance:** The percentage of users who continue to use the product over a given period. It's a critical metric for sustainable growth, reflecting long-term product value and user satisfaction.

**PostHog approach:** Track retention events like `user_returned` and `session_started`. Create retention dashboards with cohort analysis by acquisition source to track trends over time. Use session recordings to understand the behavior of retained users and correlation analysis to identify key retention-driving features. Set up automated alerts for retention drops. Autocapture allows non-technical users to track user return patterns and feature usage that correlates with retention.

#### Mobile App Performance
**Importance:** Measures the responsiveness, stability, and overall user experience of a mobile application. Good performance is essential for user satisfaction and retention on mobile devices.

**PostHog approach:** Track mobile-specific events like `app_opened` and `app_crashed` with properties such as app version and device type. Use PostHog's real user monitoring for performance and Core Web Vitals tracking. Create mobile performance dashboards, set up crash monitoring with alerts, and use session recordings to identify mobile-specific UX issues. Non-technical users can leverage autocapture to track mobile interactions and compare mobile vs. desktop usage patterns.