## Introduction

Great APIs make your service usable to customers. They are intuitive, naturally reflecting and communicating the underlying model and its behavior. They lend themselves easily to client library implementations in multiple programming languages. And they don't "get in the way" of the developer, by remaining stable and predictable, _especially over time_.

This document provides Microsoft teams building Azure services with a set of guidelines that  help service teams build great APIs. The guidelines create APIs that are approachable, sustainable, and consistent across the Azure platform. We do this by applying a common set of patterns and web standards to the design and development of the API.
For developers, a well defined and constructed API enables them to build fault-tolerant applications that are easy to maintain, support, and grow. For Azure service teams, the API is often the source of code generation enabling a broad audience of developers across multiple languages.

Azure Service teams should engage the Azure HTTP/REST Stewardship Board early in the development lifecycle for guidance, discussion, and review of their API. In addition, it is good practice to perform a security review, especially if you are concerned about PII leakage, compliance with GDPR, or any other considerations relative to your situation.

It is critically important to design your service to avoid disrupting users as the API evolves:

[:white_check_mark:](#principles-api-versioning) **DO** implement API versioning starting with the very first release of the service.

[:white_check_mark:](#principles-compatibility) **DO** ensure that customer workloads never break

[:white_check_mark:](#principles-backward-compatibility) **DO** ensure that customers are able to adopt a new version of service or SDK client library **without requiring code changes**