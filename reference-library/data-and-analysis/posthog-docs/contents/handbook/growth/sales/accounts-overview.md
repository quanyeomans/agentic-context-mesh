---
title: "Accounts overview"
source: PostHog Documentation
source_url: https://github.com/PostHog/posthog.com
licence: MIT
domain: data-and-analysis
subdomain: posthog-docs
date_added: 2026-04-25
---

This is a high level overview of where leads and customer accounts go at different stages of their interactions with us. We use various criteria to figure out where the best place is for a customer to go. You find further details in this section of the handbook. 

As we grow, this will keep changing!

```mermaid

flowchart TB
    A@{ label: "New business leads- Booked a demo (organic, paid ads)- Emailed sales@- Used &gt;50% startup credits + invoice &gt;$5k- 'Cool company' in ocean.io- Using PostHog, $0 spend, trigger for hiring increase, web/social increase, fundraise" } --> C["TECHNICAL ACCOUNT EXECUTIVE"]
    B["Expansion leads-MRR $500-1,667, &gt; 50 employees, &gt; 7 users, ICP country, paying &gt; 3 months- High ICP score + Scale plan- Off startup plan in next 2 months + last invoice &gt;$1500- &gt;$1k MRR + &gt;50% change"] --> D["TECHNICAL ACCOUNT MANAGER"]
    n1["Manual leads- Anyone can create - use your discretion"] --> C
    n2@{ label: "Onboarding leads- First bill of $100 + business email- Not otherwise a lead" } --> n8["ONBOARDING SPECIALIST"]
    C --> n4["$20k+ potential? (TAE decides)"]
    n4 -- No --> n3["SELF SERVE"]
    n4 -- Yes --> n5["Close then nurture for 12 months"]
    n5 --> n7["Using replay + flags + error tracking AND expanded to full potential? (Simon decides)"]
    n7 -- No --> D
    n7 -- Yes --> n6["CUSTOMER SUCCESS MANAGER"]
    n8 --> n9["$20k+ potential? (Onboarding/BDR decides)"]
    n9 -- Yes --> C
    n9 -- No --> n3
    n3 -- "Organic growth to$2k+ MRR" --> n14["Account review(Simon decides)"]
    n14 -- "TAM criteria met" --> D
    n14 -- "Not yet" --> n3
    
    D --> n10["Expanded to full potential? (TAM proposes, Simon signs off)"]
    n10 -- Yes --> n6
    n11["BDR"] --> n9
    n3 -- "Some become..." --> B
    n12["Manual leads- Anyone can create - use your discretion"] --> D
    n6 -- If drops <$20k --> n3

    A@{ shape: rounded}
    B@{ shape: rounded}
    n1@{ shape: rounded}
    n2@{ shape: rounded}
    n8@{ shape: rect}
    n4@{ shape: diam}
    n3@{ shape: rect}
    n5@{ shape: rounded}
    n7@{ shape: diam}
    n6@{ shape: rect}
    n9@{ shape: diam}
    n10@{ shape: diam}
    n11@{ shape: rect}
    n12@{ shape: rounded}

```
