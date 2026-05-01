## 3. Introduction
Developers access most Microsoft Cloud Platform resources via HTTP interfaces.
Although each service typically provides language-specific frameworks to wrap their APIs, all of their operations eventually boil down to HTTP requests.
Microsoft must support a wide range of clients and services and cannot rely on rich frameworks being available for every development environment.
Thus, a goal of these guidelines is to ensure Microsoft REST APIs can be easily and consistently consumed by any client with basic HTTP support.

To provide the smoothest possible experience for developers, it's important to have these APIs follow consistent design guidelines, thus making using them easy and intuitive.
This document establishes the guidelines to be followed by Microsoft REST API developers for developing such APIs consistently.

The benefits of consistency accrue in aggregate as well; consistency allows teams to leverage common code, patterns, documentation and design decisions.

These guidelines aim to achieve the following:
- Define consistent practices and patterns for all API endpoints across Microsoft.
- Adhere as closely as possible to accepted REST/HTTP best practices in the industry at-large. [\*]
- Make accessing Microsoft Services via REST interfaces easy for all application developers.
- Allow service developers to leverage the prior work of other services to implement, test and document REST endpoints defined consistently.
- Allow for partners (e.g., non-Microsoft entities) to use these guidelines for their own REST endpoint design.

[\*] Note: The guidelines are designed to align with building services which comply with the REST architectural style, though they do not address or require building services that follow the REST constraints.
The term "REST" is used throughout this document to mean services that are in the spirit of REST rather than adhering to REST by the book.*

### 3.1. Recommended reading
Understanding the philosophy behind the REST Architectural Style is recommended for developing good HTTP-based services.
If you are new to RESTful design, here are some good resources:

[REST on Wikipedia][rest-on-wikipedia] -- Overview of common definitions and core ideas behind REST.

[REST Dissertation][fielding] -- The chapter on REST in Roy Fielding's dissertation on Network Architecture, "Architectural Styles and the Design of Network-based Software Architectures"

[RFC 7231][rfc-7231] -- Defines the specification for HTTP/1.1 semantics, and is considered the authoritative resource.

[REST in Practice][rest-in-practice] -- Book on the fundamentals of REST.