## 12. Versioning
**All APIs compliant with the Microsoft REST API Guidelines MUST support explicit versioning.** It's critical that clients can count on services to be stable over time, and it's critical that services can add features and make changes.

### 12.1. Versioning formats
Services are versioned using a Major.Minor versioning scheme.
Services MAY opt for a "Major" only version scheme in which case the ".0" is implied and all other rules in this section apply.
Two options for specifying the version of a REST API request are supported:
- Embedded in the path of the request URL, at the end of the service root: `https://api.contoso.com/v1.0/products/users`
- As a query string parameter of the URL: `https://api.contoso.com/products/users?api-version=1.0`

Guidance for choosing between the two options is as follows:

1. Services co-located behind a DNS endpoint MUST use the same versioning mechanism.
2. In this scenario, a consistent user experience across the endpoint is paramount. The Microsoft REST API Guidelines Working Group recommends that new top-level DNS endpoints are not created without explicit conversations with your organization's leadership team.
3. Services that guarantee the stability of their REST API's URL paths, even through future versions of the API, MAY adopt the query string parameter mechanism. This means the naming and structure of the relationships described in the API cannot evolve after the API ships, even across versions with breaking changes.
4. Services that cannot ensure URL path stability across future versions MUST embed the version in the URL path.

Certain bedrock services such as Microsoft's Azure Active Directory may be exposed behind multiple endpoints.
Such services MUST support the versioning mechanisms of each endpoint, even if that means supporting multiple versioning mechanisms.

#### 12.1.1. Group versioning
Group versioning is an OPTIONAL feature that MAY be offered on services using the query string parameter mechanism.
Group versions allow for logical grouping of API endpoints under a common versioning moniker.
This allows developers to look up a single version number and use it across multiple endpoints.
Group version numbers are well known, and services SHOULD reject any unrecognized values.

Internally, services will take a Group Version and map it to the appropriate Major.Minor version.

The Group Version format is defined as YYYY-MM-DD, for example 2012-12-07 for December 7, 2012. This Date versioning format applies only to Group Versions and SHOULD NOT be used as an alternative to Major.Minor versioning.

##### Examples of group versioning

| Group      | Major.Minor |
|:-----------|:------------|
| 2012-12-01 | 1.0         |
|            | 1.1         |
|            | 1.2         |
| 2013-03-21 | 1.0         |
|            | 2.0         |
|            | 3.0         |
|            | 3.1         |
|            | 3.2         |
|            | 3.3         |

Version Format                | Example                | Interpretation
----------------------------- | ---------------------- | ------------------------------------------
{groupVersion}                | 2013-03-21, 2012-12-01 | 3.3, 1.2
{majorVersion}                | 3                      | 3.0
{majorVersion}.{minorVersion} | 1.2                    | 1.2

Clients can specify either the group version or the Major.Minor version:

For example:

```http
GET http://api.contoso.com/acct1/c1/blob2?api-version=1.0
```

```http
PUT http://api.contoso.com/acct1/c1/b2?api-version=2011-12-07
```

### 12.2. When to version
Services MUST increment their version number in response to any breaking API change.
See the following section for a detailed discussion of what constitutes a breaking change.
Services MAY increment their version number for nonbreaking changes as well, if desired.

Use a new major version number to signal that support for existing clients will be deprecated in the future.
When introducing a new major version, services MUST provide a clear upgrade path for existing clients and develop a plan for deprecation that is consistent with their business group's policies.
Services SHOULD use a new minor version number for all other changes.

Online documentation of versioned services MUST indicate the current support status of each previous API version and provide a path to the latest version.

### 12.3. Definition of a breaking change
Changes to the contract of an API are considered a breaking change.
Changes that impact the backwards compatibility of an API are a breaking change.

Teams MAY define backwards compatibility as their business needs require.
For example, Azure defines the addition of a new JSON field in a response to be not backwards compatible.
Office 365 has a looser definition of backwards compatibility and allows JSON fields to be added to responses.

Clear examples of breaking changes:

1. Removing or renaming APIs or API parameters
2. Changes in behavior for an existing API
3. Changes in Error Codes and Fault Contracts
4. Anything that would violate the [Principle of Least Astonishment][principle-of-least-astonishment]

Services MUST explicitly define their definition of a breaking change, especially with regard to adding new fields to JSON responses and adding new API arguments with default fields.
Services that are co-located behind a DNS Endpoint with other services MUST be consistent in defining contract extensibility.

The applicable changes described [in this section of the OData V4 spec][odata-breaking-changes] SHOULD be considered part of the minimum bar that all services MUST consider a breaking change.