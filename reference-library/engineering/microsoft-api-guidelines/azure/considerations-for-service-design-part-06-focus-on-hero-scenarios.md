## Focus on Hero Scenarios
It is important to realize that writing an API is, in many cases, the easiest part of providing a delightful developer experience. There are a large number of downstream activities for each API, e.g. testing, documentation, client libraries, examples, blog posts, videos, and supporting customers in perpetuity. In fact, implementing an API is of miniscule cost compared to all the other downstream activities.

_For this reason, it is **much better** to ship with fewer features and only add new features over time as required by customers._

Focusing on hero scenarios reduces development, support, and maintenance costs; enables teams to align and reach consensus faster; and accelerates the time to delivery. A telltale sign of a service that has not focused on hero scenarios is "API drift," where endpoints are inconsistent, incomplete, or juxtaposed to one another.

[:white_check_mark:](#hero-scenarios-design) **DO** define "hero scenarios" first including abstractions, naming, relationships, and then define the API describing the operations required.

[:white_check_mark:](#hero-scenarios-examples) **DO** provide example code demonstrating the "Hero Scenarios".

[:white_check_mark:](#hero-scenarios-high-level-languages) **DO** consider how your abstractions will be represented in different high-level languages.

[:white_check_mark:](#hero-scenarios-hll-examples) **DO** develop code examples in at least one dynamically typed language (for example, Python or JavaScript) and one statically typed language (for example, Java or C#) to illustrate your abstractions and high-level language representations.

[:no_entry:](#hero-scenarios-yagni) **DO NOT** proactively add APIs for speculative features customers might want.

### Start with your API Definition
Understanding how your service is used and defining its model and interaction patterns--its API--should be one of the earliest activities a service team undertakes. It reflects the abstractions & naming decisions and makes it easy for developers to implement the hero scenarios.

[:white_check_mark:](#openapi-description) **DO** create an [OpenAPI description](https://github.com/OAI/OpenAPI-Specification/blob/main/versions/2.0.md) (with [autorest extensions](https://github.com/Azure/autorest/blob/master/docs/extensions/readme.md)) for the service API. The OpenAPI description is a key element of the Azure SDK plan and is essential for documentation, usability and discoverability of service APIs.