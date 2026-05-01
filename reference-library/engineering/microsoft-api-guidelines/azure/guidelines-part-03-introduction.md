## Introduction

These guidelines apply to Azure service teams implementing _data plane_ APIs. They offer prescriptive guidance that Azure service teams MUST follow ensuring that customers have a great experience by designing APIs meeting these goals:
- Developer friendly via consistent patterns & web standards (HTTP, REST, JSON)
- Efficient & cost-effective
- Work well with SDKs in many programming languages
- Customers can create fault-tolerant apps by supporting retries/idempotency/optimistic concurrency
- Sustainable & versionable via clear API contracts with 2 requirements:
  1. Customer workloads must never break due to a service change
  2. Customers can adopt a version without requiring code changes

Technology and software is constantly changing and evolving, and as such, this is intended to be a living document. [Open an issue](https://github.com/microsoft/api-guidelines/issues/new/choose) to suggest a change or propose a new idea. Please read the [Considerations for Service Design](./ConsiderationsForServiceDesign.md) for an introduction to the topic of API design for Azure services. *For an existing GA'd service, don't change/break its existing API; instead, leverage these concepts for future APIs while prioritizing consistency within your existing service.*

*Note: If you are creating a management plane (ARM) API, please refer to the [Azure Resource Manager Resource Provider Contract](https://github.com/cloud-and-ai-microsoft/resource-provider-contract).*

### Prescriptive Guidance
This document offers prescriptive guidance labeled as follows:

:white_check_mark: **DO** adopt this pattern. If you feel you need an exception, contact the Azure HTTP/REST Stewardship Board **prior** to implementation.

:ballot_box_with_check: **YOU SHOULD** adopt this pattern. If not following this advice, you MUST disclose your reason during the Azure HTTP/REST Stewardship Board review.

:heavy_check_mark: **YOU MAY** consider this pattern if appropriate to your situation. No notification to the Azure HTTP/REST Stewardship Board is required.

:warning: **YOU SHOULD NOT** adopt this pattern. If not following this advice, you MUST disclose your reason during the Azure HTTP/REST Stewardship Board review.

:no_entry: **DO NOT** adopt this pattern. If you feel you need an exception, contact the Azure HTTP/REST Stewardship Board **prior** to implementation.

*If you feel you need an exception, or need clarity based on your situation, please contact the Azure HTTP/REST Stewardship Board **prior** to release of your API.*