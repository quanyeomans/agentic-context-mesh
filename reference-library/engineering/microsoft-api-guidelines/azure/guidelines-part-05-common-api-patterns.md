## Common API Patterns

[](#actions)
### Performing an Action
The REST specification is used to model the state of a resource, and is primarily intended to handle CRUD (Create, Read, Update, Delete) operations. However, many services require the ability to perform an action on a resource, e.g. getting the thumbnail of an image or rebooting a VM.  It is also sometimes useful to perform an action on a collection.

[:ballot_box_with_check:](#actions-url-pattern-for-resource-action) **YOU SHOULD** pattern your URL like this to perform an action on a resource
**URL Pattern**
```text
https://.../<resource-collection>/<resource-id>:<action>?
```

**Example**
```text
https://.../users/Bob:grant?access=read
```

[:ballot_box_with_check:](#actions-url-pattern-for-collection-action) **YOU SHOULD** pattern your URL like this to perform an action on a collection
**URL Pattern**
```text
https://.../<resource-collection>:<action>?
```

**Example**
```text
https://.../users:grant?access=read
```

Note: To avoid potential collision of actions and resource ids, you should disallow the use of the ":" character in resource ids.

[:white_check_mark:](#actions-use-post-method) **DO** use a POST operation for any action on a resource or collection.

[:white_check_mark:](#actions-support-repeatability-headers) **DO** support the Repeatability-Request-ID & Repeatability-First-Sent request headers if the action needs to be idempotent if retries occur.

[:white_check_mark:](#actions-synchronous-success-status-code) **DO** return a `200-OK` when the action completes synchronously and successfully.

[:ballot_box_with_check:](#actions-action-name-is-verb) **YOU SHOULD** use a verb as the `<action>` component of the path.

[:no_entry:](#actions-no-actions-for-crud) **DO NOT** use an action operation when the operation behavior could reasonably be defined as one of the standard REST Create, Read, Update, Delete, or List operations.

[](#collections)
### Collections
[:white_check_mark:](#collections-response-is-object) **DO** structure the response to a list operation as an object with a top-level array field containing the set (or subset) of resources.

[:ballot_box_with_check:](#collections-support-server-driven-paging) **YOU SHOULD** support paging today if there is ever a chance in the future that the number of items can grow to be very large.

NOTE: It is a breaking change to add paging in the future

[:heavy_check_mark:](#collections-use-get-method) **YOU MAY** expose an operation that lists your resources by supporting a GET method with a URL to a resource-collection (as opposed to a resource-id).

**Example Response Body**
```json
{
    "value": [
       { "id": "Item 01", "etag": "\"abc\"", "price": 99.95, "size": "Medium" },
       { … },
       { … },
       { "id": "Item 99", "etag": "\"def\"", "price": 59.99, "size": "Large" }
    ],
    "nextLink": "{opaqueUrl}"
 }
```

[:white_check_mark:](#collections-items-have-id-and-etag) **DO** include the _id_ field and _etag_ field (if supported) for each item as this allows the customer to modify the item in a future operation. Note that the etag field _must_ have escaped quotes embedded within it; for example, "\"abc\"" or W/"\"abc\"".

[:white_check_mark:](#collections-document-pagination-reliability) **DO** clearly document that resources may be skipped or duplicated across pages of a paginated collection unless the operation has made special provisions to prevent this (like taking a time-expiring snapshot of the collection).

[:white_check_mark:](#collections-include-nextlink-for-more-results) **DO** return a `nextLink` field with an absolute URL that the client can GET in order to retrieve the next page of the collection.

Note: The service is responsible for performing any URL-encoding required on the `nextLink` URL.

[:white_check_mark:](#collections-nextlink-includes-all-query-params) **DO** include any query parameters required by the service in `nextLink`, including `api-version`.

[:ballot_box_with_check:](#collections-response-array-name) **YOU SHOULD** use `value` as the name of the top-level array field unless a more appropriate name is available.

[:no_entry:](#collections-no-nextlink-on-last-page) **DO NOT** return the `nextLink` field at all when returning the last page of the collection.

[:no_entry:](#collections-nextlink-value-never-null) **DO NOT** return the `nextLink` field with a value of null.

[:warning:](#collections-avoid-count-property) **YOU SHOULD NOT** return a `count` of all objects in the collection as this may be expensive to compute.

#### Query options

[:heavy_check_mark:](#collections-query-options) **YOU MAY** support the following query parameters allowing customers to control the list operation:

Parameter&nbsp;name | Type | Description
------------------- | ---- | -----------
`filter`       | string            | an expression on the resource type that selects the resources to be returned
`orderby`      | string&nbsp;array | a list of expressions that specify the order of the returned resources
`skip`         | integer           | an offset into the collection of the first resource to be returned
`top`          | integer           | the maximum number of resources to return from the collection
`maxpagesize`  | integer           | the maximum number of resources to include in a single response
`select`       | string&nbsp;array | a list of field names to be returned for each resource
`expand`       | string&nbsp;array | a list of the related resources to be included in line with each resource

[:white_check_mark:](#collections-error-on-unknown-parameter) **DO** return an error if the client specifies any parameter not supported by the service.

[:white_check_mark:](#collections-parameter-names-case-sensitivity) **DO** treat these query parameter names as case-sensitive.

[:white_check_mark:](#collections-select-expand-ordering) **DO** apply `select` or `expand` options after applying all the query options in the table above.

[:white_check_mark:](#collections-query-options-ordering) **DO** apply the query options to the collection in the order shown in the table above.

[:no_entry:](#collections-query-options-no-dollar-sign) **DO NOT** prefix any of these query parameter names with "$" (the convention in the [OData standard](http://docs.oasis-open.org/odata/odata/v4.01/odata-v4.01-part1-protocol.html#sec_QueryingCollections)).

#### filter

[:heavy_check_mark:](#collections-filter-param) **YOU MAY** support filtering of the results of a list operation with the `filter` query parameter.

The value of the `filter` query parameter is an expression involving the fields of the resource that produces a Boolean value. This expression is evaluated for each resource in the collection and only items where the expression evaluates to true are included in the response.

[:white_check_mark:](#collections-filter-behavior) **DO** omit all resources from the collection for which the filter expression evaluates to false or to null, or references properties that are unavailable due to permissions.

Example: return all Products whose Price is less than $10.00

```text
GET https://api.contoso.com/products?filter=price lt 10.00
```

##### filter operators

:heavy_check_mark: **YOU MAY** support the following operators in filter expressions:

Operator                 | Description           | Example
--------------------     | --------------------- | -----------------------------------------------------
**Comparison Operators** |                       |
eq                       | Equal                 | city eq 'Redmond'
ne                       | Not equal             | city ne 'London'
gt                       | Greater than          | price gt 20
ge                       | Greater than or equal | price ge 10
lt                       | Less than             | price lt 20
le                       | Less than or equal    | price le 100
**Logical Operators**    |                       |
and                      | Logical and           | price le 200 and price gt 3.5
or                       | Logical or            | price le 3.5 or price gt 200
not                      | Logical negation      | not price le 3.5
**Grouping Operators**   |                       |
( )                      | Precedence grouping   | (priority eq 1 or city eq 'Redmond') and price gt 100

[:white_check_mark:](#collections-filter-unknown-operator) **DO** respond with an error message as defined in the [Handling Errors](#handling-errors) section if a client includes an operator in a filter expression that is not supported by the operation.

[:white_check_mark:](#collections-filter-operator-ordering) **DO** use the following operator precedence for supported operators when evaluating filter expressions. Operators are listed by category in order of precedence from highest to lowest. Operators in the same category have equal precedence and should be evaluated left to right:

| Group           | Operator | Description
| ----------------|----------|------------
| Grouping        | ( )      | Precedence grouping   |
| Unary           | not      | Logical Negation      |
| Relational      | gt       | Greater Than          |
|                 | ge       | Greater than or Equal |
|                 | lt       | Less Than             |
|                 | le       | Less than or Equal    |
| Equality        | eq       | Equal                 |
|                 | ne       | Not Equal             |
| Conditional AND | and      | Logical And           |
| Conditional OR  | or       | Logical Or            |

[:heavy_check_mark:](#collections-filter-functions) **YOU MAY** support orderby and filter functions such as concat and contains. For more information, see [odata Canonical Functions](https://docs.oasis-open.org/odata/odata/v4.01/odata-v4.01-part2-url-conventions.html#_Toc31360979).

##### Operator examples
The following examples illustrate the use and semantics of each of the logical operators.

Example: all products with a name equal to 'Milk'

```text
GET https://api.contoso.com/products?filter=name eq 'Milk'
```

Example: all products with a name not equal to 'Milk'

```text
GET https://api.contoso.com/products?filter=name ne 'Milk'
```

Example: all products with the name 'Milk' that also have a price less than 2.55:

```text
GET https://api.contoso.com/products?filter=name eq 'Milk' and price lt 2.55
```

Example: all products that either have the name 'Milk' or have a price less than 2.55:

```text
GET https://api.contoso.com/products?filter=name eq 'Milk' or price lt 2.55
```

Example: all products that have the name 'Milk' or 'Eggs' and have a price less than 2.55:

```text
GET https://api.contoso.com/products?filter=(name eq 'Milk' or name eq 'Eggs') and price lt 2.55
```

#### orderby

[:heavy_check_mark:](#collections-orderby-param) **YOU MAY** support sorting of the results of a list operation with the `orderby` query parameter.
*NOTE: It is unusual for a service to support `orderby` because it is very expensive to implement as it requires sorting the entire large collection before being able to return any results.*

The value of the `orderby` parameter is a comma-separated list of expressions used to sort the items.
A special case of such an expression is a property path terminating on a primitive property.

Each expression in the `orderby` parameter value may include the suffix "asc" for ascending or "desc" for descending, separated from the expression by one or more spaces.

[:white_check_mark:](#collections-orderby-ordering) **DO** sort the collection in ascending order on an expression if "asc" or "desc" is not specified.

[:white_check_mark:](#collections-orderby-null-ordering) **DO** sort NULL values as "less than" non-NULL values.

[:white_check_mark:](#collections-orderby-behavior) **DO** sort items by the result values of the first expression, and then sort items with the same value for the first expression by the result value of the second expression, and so on.

[:white_check_mark:](#collections-orderby-inherent-sort-order) **DO** use the inherent sort order for the type of the field. For example, date-time values should be sorted chronologically and not alphabetically.

[:white_check_mark:](#collections-orderby-unsupported-field) **DO** respond with an error message as defined in the [Handling Errors](#handling-errors) section if the client requests sorting by a field that is not supported by the operation.

For example, to return all people sorted by name in ascending order:
```text
GET https://api.contoso.com/people?orderby=name
```

For example, to return all people sorted by name in descending order and a secondary sort order of hireDate in ascending order.
```text
GET https://api.contoso.com/people?orderby=name desc,hireDate
```

Sorting MUST compose with filtering such that:
```text
GET https://api.contoso.com/people?filter=name eq 'david'&orderby=hireDate
```
will return all people whose name is David sorted in ascending order by hireDate.

##### Considerations for sorting with pagination

[:white_check_mark:](#collections-consistent-options-with-pagination) **DO** use the same filtering options and sort order for all pages of a paginated list operation response.

##### skip
[:white_check_mark:](#collections-skip-param-definition) **DO** define the `skip` parameter as an integer with a default and minimum value of 0.

[:heavy_check_mark:](#collections-skip-param) **YOU MAY** allow clients to pass the `skip` query parameter to specify an offset into collection of the first resource to be returned.
##### top

[](#collections-)
[:heavy_check_mark:](#collections-top-param) **YOU MAY** allow clients to pass the `top` query parameter to specify the maximum number of resources to return from the collection.

If supporting `top`:
:white_check_mark: **DO** define the `top` parameter as an integer with a minimum value of 1. If not specified, `top` has a default value of infinity.

[:white_check_mark:](#collections-top-behavior) **DO** return the collection's `top` number of resources (if available), starting from `skip`.

##### maxpagesize

[:heavy_check_mark:](#collections-maxpagesize-param) **YOU MAY** allow clients to pass the `maxpagesize` query parameter to specify the maximum number of resources to include in a single page response.

[:white_check_mark:](#collections-maxpagesize-definition) **DO** define the `maxpagesize` parameter as an optional integer with a default value appropriate for the collection.

[:white_check_mark:](#collections-maxpagesize-might-return-fewer) **DO** make clear in documentation of the `maxpagesize` parameter that the operation may choose to return fewer resources than the value specified.

[](#versioning)
### API Versioning

Azure services need to change over time. However, when changing a service, there are 2 requirements:
 1. Already-running customer workloads must not break due to a service change
 2. Customers can adopt a new service version without requiring any code changes (Of course, the customer must modify code to leverage any new service features.)

*NOTE: the [Azure Breaking Change Policy](http://aka.ms/AzBreakingChangesPolicy) has tables (section 5) describing what kinds of changes are considered breaking. Breaking changes are allowable (due to security/compliance/etc.) if approved by the [Azure Breaking Change Reviewers](mailto:azbreakchangereview@microsoft.com) but only following ample communication to customers and a lengthy deprecation period.*

[:white_check_mark:](#versioning-review-required) **DO** review any API changes with the Azure API Stewardship Board

Clients specify the version of the API to be used in every request to the service, even requests to an `Operation-Location` or `nextLink` URL returned by the service.

[:white_check_mark:](#versioning-api-version-query-param) **DO** use a required query parameter named `api-version` on every operation for the client to specify the API version.

[:white_check_mark:](#versioning-date-based-versioning) **DO** use `YYYY-MM-DD` date values, with a `-preview` suffix for preview versions, as the valid values for `api-version`.

[:white_check_mark:](#versioning-api-version-missing) **DO** return HTTP 400 with error code "MissingApiVersionParameter" and message "The api-version query parameter (?api-version=) is required for all requests" if client omits the `api-version` query parameter.

[:white_check_mark:](#versioning-api-version-unsupported) **DO** return HTTP 400 with error code "UnsupportedApiVersionValue" and message "Unsupported api-version '{0}'. The supported api-versions are '{1}'." if client passes an `api-version` value unrecognized by the service. For the supported api-versions, just list all the stable versions still supported by the service and just the latest public preview version (if any).

```text
PUT https://service.azure.com/users/Jeff?api-version=2021-06-04
```

[:white_check_mark:](#versioning-use-later-date) **DO** use a later date for each new preview version

When releasing a new preview, the service team may completely retire any previous preview versions after giving customers at least 90 days to upgrade their code

[:no_entry:](#versioning-no-breaking-changes) **DO NOT** introduce any breaking changes into the service.

[:no_entry:](#versioning-no-version-in-path) **DO NOT** include a version number segment in any operation path.

[:no_entry:](#versioning-use-later-date-2) **DO NOT** use the same date when transitioning from a preview API to a GA API. If the preview `api-version` is '2021-06-04-preview', the GA version of the API **must be** a date later than 2021-06-04

[:no_entry:](#versioning-preview-goes-ga-within-one-year) **DO NOT** keep a preview feature in preview for more than 1 year; it must go GA (or be removed) within 1 year after introduction.

#### Use Extensible Enums

While removing a value from an enum is a breaking change, adding value to an enum can be handled with an _extensible enum_.  An extensible enum is a string value that has been marked with a special marker - setting `modelAsString` to true within an `x-ms-enum` block.  For example:

```json
"createdByType": {
   "type": "string",
   "description": "The type of identity that created the resource.",
   "enum": [
      "User",
      "Application",
      "ManagedIdentity",
      "Key"
   ],
   "x-ms-enum": {
      "name": "createdByType",
      "modelAsString": true
   }
}
```

[:ballot_box_with_check:](#versioning-use-extensible-enums) **YOU SHOULD** use extensible enums unless you are positive that the symbol set will **NEVER** change over time.

[](#deprecation)
### Deprecating Behavior Notification

When the [API Versioning](#api-versioning) guidance above cannot be followed and the [Azure Breaking Change Reviewers](mailto:azbreakchangereview@microsoft.com) approve a breaking change to a specific API version it must be communicated to its callers. The API version that is being deprecated must add the `azure-deprecating` response header with a semicolon-delimited string notifying the caller what is being deprecated, when it will no longer function, and a URL linking to more information such as what new operation they should use instead.

The purpose is to inform customers (when debugging/logging responses) that they must take action to modify their call to the service's operation and use a newer API version or their call will soon stop working entirely. It is not expected that client code will examine/parse this header's value in any way; it is purely informational to a human being. The string is _not_ part of an API contract (except for the semi-colon delimiters) and may be changed/improved at any time without incurring a breaking change.

[:white_check_mark:](#deprecation-header) **DO** include the `azure-deprecating` header in the operation's response _only if_ the operation will stop working in the future and the client _must take_ action in order for it to keep working.
> NOTE: We do not want to scare customers with this header.

[:white_check_mark:](#deprecation-header-value) **DO** make the header's value a semicolon-delimited string indicating a set of deprecations where each one indicates what is deprecating, when it is deprecating, and a URL to more information.

Deprecations should use the following pattern:
```text
<description> will retire on <date> ()
```

Multiple deprecations are allowed, semicolon delimited.

Where the following placeholders should be provided:
- `description`: a human-readable description of what is being deprecated
- `date`: the target date that this will be deprecated. This should be expressed following the format in [ISO 8601](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.1.1), e.g. "2022-10-31".
- `url`: a fully qualified url that the user can follow to learn more about what is being deprecated, preferably to Azure Updates.

For example:
- `azure-deprecating: API version 2009-27-07 will retire on 2022-12-01 (https://azure.microsoft.com/updates/video-analyzer-retirement);TLS 1.0 & 1.1 will retire on 2020-10-30 (https://azure.microsoft.com/updates/azure-active-directory-registration-service-is-ending-support-for-tls-10-and-11/)`
- `azure-deprecating: Model version 2021-01-15 used in Sentiment analysis will retire on 2022-12-01 (https://aka.ms/ta-modelversions?sentimentAnalysis)`
- `azure-deprecating: TLS 1.0 & 1.1 support will retire on 2022-10-01 (https://devblogs.microsoft.com/devops/deprecating-weak-cryptographic-standards-tls-1-0-and-1-1-in-azure-devops-services/)`

[:no_entry:](#deprecation-header-review) **DO NOT** introduce this header without approval from [Azure Breaking Change Reviewers](mailto:azbreakchangereview@microsoft.com) and an official deprecation notice on [Azure Updates](https://azure.microsoft.com/updates/).

[](#repeatability)
### Repeatability of requests

Fault tolerant applications require that clients retry requests for which they never got a response, and services must handle these retried requests idempotently. In Azure, all HTTP operations are naturally idempotent except for POST used to create a resource and [POST when used to invoke an action](
https://github.com/microsoft/api-guidelines/blob/vNext/azure/Guidelines.md#performing-an-action).

[:ballot_box_with_check:](#repeatability-headers) **YOU SHOULD** support repeatable requests as defined in [OASIS Repeatable Requests Version 1.0](https://docs.oasis-open.org/odata/repeatable-requests/v1.0/repeatable-requests-v1.0.html) for POST operations to make them retriable.
- The tracked time window (difference between the `Repeatability-First-Sent` value and the current time) **MUST** be at least 5 minutes.
- Document the POST operation's support for the `Repeatability-First-Sent`, `Repeatability-Request-ID`, and `Repeatability-Result` headers in the API contract and documentation.
- Any operation that does not support repeatability headers should return a 501 (Not Implemented) response for any request that contains valid repeatability request headers.

### Long-Running Operations & Jobs

A _long-running operation (LRO)_ is typically an operation that should execute synchronously, but due to services not wanting to maintain long-lived connections (>1 seconds) and load-balancer timeouts, the operation must execute asynchronously. For this pattern, the client initiates the operation on the service, and then the client repeatedly polls the service (via another API call) to track the operation's progress/completion.

LROs are always started by 1 logical client and may be polled (have their status checked) by the same client, another client, or even multiple clients/browsers. An example would be a dashboard or portal that shows all the operations along with their status.  See the [Long Running Operations section](./ConsiderationsForServiceDesign.md#long-running-operations) in Considerations for Service Design for an introduction to the design of long-running operations.

[:white_check_mark:](#lro-response-time) **DO** implement an operation as an LRO if the 99th percentile response time is greater than 1 second and when the client should poll the operation before making more progress.

[:no_entry:](#lro-no-patch-lro) **DO NOT** implement PATCH as an LRO. If LRO update semantics are required, implement it using the [LRO POST action pattern](#lro-existing-resource) .

#### Patterns to Initiate a Long-Running Operation

[:white_check_mark:](#lro-valid-inputs-synchronously) **DO** perform as much validation as practical when initiating an LRO operation to alert clients of errors early.

[:white_check_mark:](#lro-returns-operation-location) **DO** include an `operation-location` response header with the absolute URL of the status monitor for the operation.

[:ballot_box_with_check:](#lro-operation-location-includes-api-version) **YOU SHOULD** include the `api-version` query parameter in the `operation-location` response header with the same version passed on the initial request but expect a client to change the `api-version` value to whatever a new/different client desires it to be.

[:white_check_mark:](#lro-put-response-headers) **DO** include response headers with any additional values needed for a [GET polling request](#lro-poll) to the status monitor (e.g. location).

#### Create or replace operation with additional long-running processing
[](#put-operation-with-additional-long-running-processing)

[:white_check_mark:](#lro-create-init) **DO** use the following pattern when implementing an operation that creates or replaces a resource that involves additional long-running processing:

```text
PUT /UrlToResourceBeingCreated?api-version=<api-version>
operation-id: <optionalStatusMonitorResourceId>

<JSON Resource in body>
```

The response must look like this:

```text
201 Created
operation-id: <statusMonitorResourceId>
operation-location: https://operations/<operation-id>?api-version=<api-version>

<JSON Resource in body>
```

The request and response body schemas must be identical and represent the resource.

The PUT creates or replaces the resource immediately and returns but the additional long-running processing can take time to complete.

For an idempotent PUT (same `operation-id` or same request body within some short time window), the service should return the same response as shown above.

For a non-idempotent PUT, the service can choose to overwrite the existing resource (as if the resource were deleted) or the service can return `409-Conflict` with the error's code property indicated why this PUT operation failed.

[:white_check_mark:](#lro-put-operation-id-request-header) **DO** allow the client to pass an `Operation-Id` header with a ID for the status monitor for the operation.

If the `Operation-Id` header is not specified, the service may create an operation-id (typically a GUID) and return it via the `operation-id` and `operation-location` response headers; in this case the service must figure out how to deal with retries/idempotency.

[:white_check_mark:](#lro-put-operation-id-default-is-guid) **DO** generate an ID (typically a GUID) for the status monitor if the `Operation-Id` header was not passed by the client.

[:white_check_mark:](#lro-put-operation-id-unique-except-retries) **DO** fail a request with a `409-Conflict` if the `Operation-Id` header matches an existing operation unless the request is identical to the prior request (a retry scenario).

[:white_check_mark:](#lro-put-valid-inputs-synchronously) **DO** perform as much validation as practical when initiating the operation to alert clients of errors early.

[:white_check_mark:](#lro-put-returns-200-or-201) **DO** return a `201-Created` status code for create or `200-OK` for replace from the initial request with a representation of the resource, if the resource was created or replaced successfully.

[:white_check_mark:](#lro-put-returns-operation-id-header) **DO** include an `Operation-Id` header in the response with the ID of the status monitor for the operation.

[:ballot_box_with_check:](#lro-put-returns-operation-location) **YOU SHOULD** include an `Operation-Location` header in the response with the absolute URL of the status monitor for the operation.

[:ballot_box_with_check:](#lro-put-operation-location-includes-api-version) **YOU SHOULD** include the `api-version` query parameter in the `Operation-Location` header with the same version passed on the initial request.

#### DELETE LRO pattern

[:white_check_mark:](#lro-delete) **DO** use the following pattern when implementing an LRO operation to delete a resource:

```text
DELETE /UrlToResourceBeingDeleted?api-version=<api-version>
operation-id: <optionalStatusMonitorResourceId>
```

The response must look like this:

```text
202 Accepted
operation-id: <statusMonitorResourceId>
operation-location: https://operations/<operation-id>
```

Consistent with non-LRO DELETE operations, if a request body is specified, return `400-Bad Request`.

[:white_check_mark:](#lro-delete-operation-id-request-header) **DO** allow the client to pass an `Operation-Id` header with an ID for the operation's status monitor.

[:white_check_mark:](#lro-delete-operation-id-default-is-guid) **DO** generate an ID (typically a GUID) for the status monitor if the `Operation-Id` header was not passed by the client.

[:white_check_mark:](#lro-delete-returns-202) **DO** return a `202-Accepted` status code from the request that initiates an LRO if the processing of the operation was successfully initiated.

[:warning:](#lro-delete-returns-only-202) **YOU SHOULD NOT** return any other `2xx` status code from the initial request of an LRO -- return `202-Accepted` and a status monitor even if processing was completed before the initiating request returns.

#### LRO action on a resource pattern
[](#post-or-delete-lro-pattern)

[:white_check_mark:](#lro-existing-resource) **DO** use the following pattern when implementing an LRO action operating on an existing resource:

```text
POST /UrlToExistingResource:<action>?api-version=<api-version>&<actionParamsGoHere>
operation-id: <optionalStatusMonitorResourceId>`

<JSON Action parameters can go in body if query params don't work>
```

The response must look like this:

```text
202 Accepted
operation-id: <statusMonitorResourceId>
operation-location: https://operations/<operation-id>

<JSON Status Monitor Resource in body>
```

The request body contains information to be used to execute the action.

For an idempotent POST (same `operation-id` and request body within some short time window), the service should return the same response as the initial request.

For a non-idempotent POST, the service can treat the POST operation as idempotent (if performed within a short time window) or can treat the POST operation as initiating a brand new LRO action operation.

[:no_entry:](#lro-no-post-create) **DO NOT** use a long-running POST to create a resource -- use PUT as described above.

[:white_check_mark:](#lro-operation-id-request-header) **DO** allow the client to pass an `Operation-Id` header with an ID for the operation's status monitor.

[:white_check_mark:](#lro-operation-id-default-is-guid) **DO** generate an ID (typically a GUID) for the status monitor if the `Operation-Id` header was not passed by the client.

[:white_check_mark:](#lro-operation-id-unique-except-retries) **DO** fail a request with a `409-Conflict` if the `Operation-Id` header matches an existing operation unless the request is identical to the prior request (a retry scenario).

[:white_check_mark:](#lro-returns-202) **DO** return a `202-Accepted` status code from the request that initiates an LRO action on a resource if the processing of the operation was successfully initiated.

[:warning:](#lro-returns-only-202) **YOU SHOULD NOT** return any other `2xx` status code from the initial request of an LRO -- return `202-Accepted` and a status monitor even if processing was completed before the initiating request returns.

[:white_check_mark:](#lro-returns-status-monitor) **DO** return a status monitor in the response body as described in [Obtaining status and results of long-running operations](#obtaining-status-and-results-of-long-running-operations).

#### LRO action with no related resource pattern

[:white_check_mark:](#lro-action-no-resource) **DO** use the following pattern when implementing an LRO action not related to a specific resource (such as a batch operation):

```text
PUT <operation-endpoint>/<operation-id>?api-version=<api-version>

<JSON body with parameters for the operation>>
```

The response must look like this:

```text
201 Created
operation-location: <absolute URL of status monitor>

<JSON Status Monitor Resource in body>
```

[:ballot_box_with_check:](#lro-put-action-operation-endpoint) **YOU SHOULD**
define a unique operation endpoint for each LRO action with no related resource.

[:white_check_mark:](#lro-put-action-operation-id) **DO** require the
`Operation-Id` as the final path segment in the URL.

Note: The `operation-id` URL segment (not header) is *required*, forcing the client to specify the status monitor's resource ID
and is also used for retries/idempotency.

[:white_check_mark:](#lro-put-action-returns-201) **DO** return a `201 Created` status code
with an `operation-location` response header if the LRO Action operation was accepted for processing.

[:white_check_mark:](#lro-put-action-returns-status-monitor) **DO** return a
status monitor in the response body that contains the operation status, request parameters, and when the operation completes either
the operation result or error.

Note: Since all request parameters must be present in the status monitor,
the request and response body of the PUT can be defined with a single schema.

[:ballot_box_with_check:](#lro-put-action-status-monitor-url) **YOU SHOULD**
return the status monitor for an operation for a subsequent GET on the URL that initiates the LRO, and use this endpoint as
the status monitor URL returned in the `operation-location` response header.

#### The Status Monitor Resource

All patterns that initiate a LRO either implicitly or explicitly create a [Status Monitor resource](https://datatracker.ietf.org/doc/html/rfc7231#section-6.3.3) in the service's `operations` collection.

[:white_check_mark:](#lro-status-monitor-structure) **DO** return a status monitor in the response body that conforms with the following structure:

Property | Type        | Required | Description
-------- | ----------- | :------: | -----------
`id`     | string      | true     | The unique id of the operation
`kind`   | string enum | true(*)  | The kind of operation
`status` | string enum | true     | The operation's current status: "NotStarted", "Running", "Succeeded", "Failed", and "Canceled"
`error`  | ErrorDetail |          | If `status`=="Failed", contains reason for failure
`result` | object      |          | If `status`=="Succeeded" && Action LRO (POST or PUT), contains success result if needed
additionalproperties | | | Additional named or dynamic properties of the operation

(*): When a status monitor endpoint supports multiple operations with different result structures or additional properties,
the status monitor **must** be polymorphic -- it **must** contain a required `kind` property that indicates the kind of long-running operation.

#### Obtaining status and results of long-running operations

[:white_check_mark:](#lro-poll) **DO** use the following pattern to allow clients to poll the current state of a Status Monitor resource:

```text
GET <operation-endpoint>/<operation-id>?api-version=<api-version>
```

The response must look like this:

```text
200 OK
retry-after: <delay-seconds>    (if status not terminal)

<JSON Status Monitor Resource in body>
```

[:white_check_mark:](#lro-status-monitor-get-returns-200) **DO** support the GET method on the status monitor endpoint that returns a `200-OK` response with the current state of the status monitor.

[:ballot_box_with_check:](#lro-status-monitor-accepts-any-api-version) **YOU SHOULD** allow any valid value of the `api-version` query parameter to be used in the GET operation on the status monitor.

- Note: Clients may replace the value of `api-version` in the `operation-location` URL with a value appropriate for their application. Remember that the client initiating the LRO may not be the same client polling the LRO's status.

[:white_check_mark:](#lro-status-monitor-includes-all-fields) **DO** include the `id` of the operation and any other values needed for the client to form a GET request to the status monitor (e.g. a `location` path parameter).

[:white_check_mark:](#lro-status-monitor-post-action-result) **DO** include the `result` property (if any) in the status monitor for a POST action-type long-running operation when the operation completes successfully.

[:no_entry:](#lro-status-monitor-no-resource-result) **DO NOT** include a `result` property in the status monitor for a long-running operation that is not an action-type long-running operation.

[:white_check_mark:](#lro-status-monitor-retry-after) **DO** include a `retry-after` header in the response if the operation is not complete. The value of this header should be an integer number of seconds that the client should wait before polling the status monitor again.

[:white_check_mark:](#lro-status-monitor-retention) **DO** retain the status monitor resource for some publicly documented period of time (at least 24 hours) after the operation completes.

#### Pattern to List Status Monitors

Use the following patterns to allow clients to list Status Monitor resources.

[:ballot_box_with_check:](#lro-list-status-monitors)
**YOU MAY** support a GET method on any status monitor collection URL that returns a list of the status monitors in that collection.

[:ballot_box_with_check:](#lro-put-action-list-status-monitors)
**YOU SHOULD** support a list operation for any status monitor collection that includes status monitors for LRO Actions with no related resource.

[:ballot_box_with_check:](#lro-list-status-monitors-filter)
**YOU SHOULD** support the `filter` query parameter on the list operation for any polymorphic status monitor collection and support filtering on the `kind` value of the status monitor.

For example, the following request should return all status monitor resources whose `kind` is either "VMInitializing" *or* "VMRebooting"
and whose status is "NotStarted" *or* "Succeeded".

```text
GET /operations?filter=(kind eq 'VMInitializing' or kind eq 'VMRebooting') and (status eq 'NotStarted' or status eq 'Succeeded')
```

[](#byos)
### Bring your own Storage (BYOS)

Many services need to store and retrieve data files. For this scenario, the service should not implement its own
storage APIs and should instead leverage the existing Azure Storage service. When doing this, the customer
"owns" the storage account and just tells your service to use it. Colloquially, we call this Bring Your Own Storage as the customer is bringing their storage account to another service. BYOS provides significant benefits to service implementors: security, performance, uptime, etc. And, of course, most Azure customers are already familiar with the Azure Storage service.

While Azure Managed Storage may be easier to get started with, as your service evolves and matures, BYOS provides the most flexibility and implementation choices. Further, when designing your APIs, be cognizant of expressing storage concepts and how clients will access your data. For example, if you are working with blobs, then you should not expose the concept of folders.

[:white_check_mark:](#byos-pattern) **DO** use the Bring Your Own Storage pattern.

[:white_check_mark:](#byos-prefix-for-folder) **DO** use a blob prefix for a logical folder (avoid terms such as `directory`, `folder`, or `path`).

[:no_entry:](#byos-allow-container-reuse) **DO NOT** require a fresh container per operation.

[:white_check_mark:](#byos-authorization) **DO** use managed identity and Role Based Access Control ([RBAC](https://docs.microsoft.com/azure/role-based-access-control/overview)) as the mechanism allowing customers to grant permission to their Storage account to your service.

[:white_check_mark:](#byos-define-rbac-roles) **DO** Add RBAC roles for every service operation that requires accessing Storage scoped to the exact permissions.

[:white_check_mark:](#byos-rbac-compatibility) **DO** Ensure that RBAC roles are backward compatible, and specifically, do not take away permissions from a role that would break the operation of the service. Any change of RBAC roles that results in a change of the service behavior is considered a breaking change.


#### Handling 'downstream' errors
It is not uncommon to rely on other services, e.g. storage, when implementing your service. Inevitably, the services you depend on will fail. In these situations, you can include the downstream error code and text in the inner-error of the response body. This provides a consistent pattern for handling errors in the services you depend upon.

[:white_check_mark:](#byos-include-downstream-errors) **DO** include error from downstream services as the 'inner-error' section of the response body.

#### Working with files
Generally speaking, there are two patterns that you will encounter when working with files; single file access, and file collections.

##### Single file access
Designing an API for accessing a single file, depending on your scenario, is relatively straight forward.

[:heavy_check_mark:](#byos-sas-token) **YOU MAY** use a Shared Access Signature [SAS](https://docs.microsoft.com/azure/storage/common/storage-sas-overview) to provide access to a single file. SAS is considered the minimum security for files and can be used in lieu of, or in addition to, RBAC.

[:ballot_box_with_check:](#byos-http-insecure) **YOU SHOULD** if using HTTP (not HTTPS) document to users that all information is sent over the wire in clear text.

[:white_check_mark:](#byos-http-status-code) **DO** return an HTTP status code representing the result of your service operation's behavior.

[:white_check_mark:](#byos-include-storage-error) **DO** include the Storage error information in the 'inner-error' section of an error response if the error was the result of an internal Storage operation failure. This helps the client determine the underlying cause of the error, e.g.: a missing storage object or insufficient permissions.

[:white_check_mark:](#byos-support-single-object) **DO** allow the customer to specify a URL path to a single Storage object if your service requires access to a single file.

[:heavy_check_mark:](#byos-last-modified) **YOU MAY** allow the customer to provide a [last-modified](https://datatracker.ietf.org/doc/html/rfc7232#section-2.2) timestamp (in RFC 7231 format) for read-only files. This allows the client to specify exactly which version of the files your service should use.
When reading a file, your service passes this timestamp to Azure Storage using the [if-unmodified-since](https://datatracker.ietf.org/doc/html/rfc7232#section-3.4) request header. If the Storage operation fails with 412, the Storage object was modified and your service operation should return an appropriate 4xx status code and return the Storage error in your operation's 'inner-error' (see guideline above).

[:white_check_mark:](#byos-folder-support) **DO** allow the customer to specify a URL path to a logical folder (via prefix and delimiter) if your service requires access to multiple files (within this folder). For more information, see [List Blobs API](https://docs.microsoft.com/rest/api/storageservices/list-blobs)

[:heavy_check_mark:](#byos-extensions) **YOU MAY** offer an `extensions` field representing an array of strings indicating file extensions of desired blobs within the logical folder.

A common pattern when working with multiple files is for your service to receive requests that contain the location(s) of files to process ("input") and a location(s) to place any files that result from processing ("output"). Note: the terms "input" and "output" are just examples; use terms more appropriate to your service's domain.

For example, a service's request body to configure BYOS may look like this:

```json
{
  "input":{
    "location": "https://mycompany.blob.core.windows.net/documents/english/?<sas token>",
    "delimiter": "/",
    "extensions" : [ ".bmp", ".jpg", ".tif", ".png" ],
    "lastModified": "Wed, 21 Oct 2015 07:28:00 GMT"
  },
  "output":{
    "location": "https://mycompany.blob.core.windows.net/documents/spanish/?<sas token>",
    "delimiter":"/"
  }
}
```

Depending on the requirements of the service, there can be any number of "input" and "output" sections, including none.

[:white_check_mark:](#byos-location-and-delimiter) **DO** include a JSON object that has string values for "location" and "delimiter". For "location", the customer must pass a URL to a blob prefix which represents a directory. For "delimiter", the customer must specify the delimiter character they desire to use in the location URL; typically "/" or "\".

[:heavy_check_mark:](#byos-directory-last-modified) **YOU MAY** support the "lastModified" field for input directories (see guideline above).

[:white_check_mark:](#byos-sas-for-input-location) **DO** support a "location" URL with a container-scoped SAS that has a minimum of `listing` and `read` permissions for input directories.

[:white_check_mark:](#byos-sas-for-output-location) **DO** support a "location" URL with a container-scoped SAS that has a minimum of `write` permissions for output directories.

[](#condreq)
### Conditional Requests

The [HTTP Standard][] defines request headers that clients may use to specify a _precondition_
for execution of an operation. These headers allow clients to implement efficient caching mechanisms
and avoid data loss in the event of concurrent updates to a resource. The headers that specify conditional execution are `If-Match`, `If-None-Match`, `If-Modified-Since`, `If-Unmodified-Since`, and `If-Range`.

[HTTP Standard]: https://datatracker.ietf.org/doc/html/rfc9110


[](#condreq-support-etags-consistently)

[](#condreq-for-read)

[](#condreq-no-pessimistic-update)
[:white_check_mark:](#condreq-support) **DO** honor any precondition headers received as part of a client request.

The HTTP Standard does not allow precondition headers to be ignored, as it can be unsafe to do so.

[:white_check_mark:](#condreq-unsupported-error) **DO** return the appropriate precondition failed error response if the service cannot verify the truth of the precondition.

Note: The Azure Breaking Changes review board will allow a GA service that currently ignores precondition headers to begin honoring them in a new API version without a formal breaking change notification. The potential for disruption to customer applications is low and outweighed by the value of conforming to HTTP standards.

While conditional requests can be implemented using last modified dates, entity tags ("ETags") are strongly
preferred since last modified dates cannot distinguish updates made less than a second apart.

[:ballot_box_with_check:](#condreq-return-etags) **YOU SHOULD** return an `ETag` with any operation returning the resource or part of a resource or any update of the resource (whether the resource is returned or not).

#### Conditional Request behavior

This section gives a summary of the processing to perform for precondition headers.
See the [Conditional Requests section of the HTTP Standard][] for details on how and when to evaluate these headers.

[Conditional Requests section of the HTTP Standard]: https://datatracker.ietf.org/doc/html/rfc9110#name-conditional-requests

[:white_check_mark:](#condreq-for-read-behavior) **DO** adhere to the following table for processing a GET request with precondition headers:

| GET Request | Return code | Response                                    |
|:------------|:------------|:--------------------------------------------|
| ETag value = `If-None-Match` value   | `304-Not Modified` | no additional information   |
| ETag value != `If-None-Match` value  | `200-OK`           | Response body include the serialized value of the resource (typically JSON)    |

For more control over caching, please refer to the `cache-control` [HTTP header](https://developer.mozilla.org/docs/Web/HTTP/Headers/Cache-Control).

[:white_check_mark:](#condreq-behavior) **DO** adhere to the following table for processing a PUT, PATCH, or DELETE request with precondition headers:

| Operation   | Header        | Value | ETag check | Return code | Response       |
|:------------|:--------------|:------|:-----------|:------------|----------------|
| PATCH / PUT | `If-None-Match` | *     | check for _any_ version of the resource ('*' is a wildcard used to match anything), if none are found, create the resource. | `200-OK` or  `201-Created`  | Response header MUST include the new `ETag` value. Response body SHOULD include the serialized value of the resource (typically JSON).  |
| PATCH / PUT | `If-None-Match` | *     | check for _any_ version of the resource, if one is found, fail the operation |  `412-Precondition Failed` | Response body SHOULD return the serialized value of the resource (typically JSON) that was passed along with the request.|
| PATCH / PUT | `If-Match` | value of ETag     | value of `If-Match` equals the latest ETag value on the server, confirming that the version of the resource is the most current | `200-OK` or  `201-Created`  | Response header MUST include the new `ETag` value. Response body SHOULD include the serialized value of the resource (typically JSON).  |
| PATCH / PUT | `If-Match` | value of ETag     | value of `If-Match` header DOES NOT equal the latest ETag value on the server, indicating a change has ocurred since after the client fetched the resource|  `412-Precondition Failed` | Response body SHOULD return the serialized value of the resource (typically JSON) that was passed along with the request.|
| DELETE      | `If-Match` | value of ETag     | value matches the latest value on the server | `204-No Content` | Response body SHOULD be empty.  |
| DELETE      | `If-Match` | value of ETag     | value does NOT match the latest value on the server | `412-Preconditioned Failed` | Response body SHOULD be empty.|

#### Computing ETags

The strategy that you use to compute the `ETag` depends on its semantic. For example, it is natural, for resources that are inherently versioned, to use the version as the value of the `ETag`. Another common strategy for determining the value of an `ETag` is to use a hash of the resource. If a resource is not versioned, and unless computing a hash is prohibitively expensive, this is the preferred mechanism.

[:ballot_box_with_check:](#condreq-etag-is-hash) **YOU SHOULD** use a hash of the representation of a resource rather than a last modified/version number

While it may be tempting to use a revision/version number for the resource as the ETag, it interferes with client's ability to retry update requests. If a client sends a conditional update request, the service acts on the request, but the client never receives a response, a subsequent identical update will be seen as a conflict even though the retried request is attempting to make the same update.

[:ballot_box_with_check:](#condreq-etag-hash-entire-resource) **YOU SHOULD**, if using a hash strategy, hash the entire resource.

[:ballot_box_with_check:](#condreq-strong-etag-for-range-requests) **YOU SHOULD**, if supporting range requests, use a strong ETag in order to support caching.

[:heavy_check_mark:](#condreq-timestamp-precision) **YOU MAY** use or, include, a timestamp in your resource schema. If you do this, the timestamp shouldn't be returned with more than subsecond precision, and it SHOULD be consistent with the data and format returned, e.g. consistent on milliseconds.

[:heavy_check_mark:](#condreq-weak-etags-allowed) **YOU MAY** consider Weak ETags if you have a valid scenario for distinguishing between meaningful and cosmetic changes or if it is too expensive to compute a hash.

[:white_check_mark:](#condreq-etag-depends-on-encoding) **DO**, when supporting multiple representations (e.g. Content-Encodings) for the same resource, generate different ETag values for the different representations.

[](#substrings)
### Returning String Offsets & Lengths (Substrings)

All string values in JSON are inherently Unicode and UTF-8 encoded, but clients written in a high-level programming language must work with strings in that language's string encoding, which may be UTF-8, UTF-16, or CodePoints (UTF-32).
When a service response includes a string offset or length value, it should specify these values in all 3 encodings to simplify client development and ensure customer success when isolating a substring.
See the [Returning String Offsets & Lengths] section in Considerations for Service Design for more detail, including an example JSON response containing string offset and length fields.

[Returning String Offsets & Lengths]: https://github.com/microsoft/api-guidelines/blob/vNext/azure/ConsiderationsForServiceDesign.md#returning-string-offsets--lengths-substrings

[:white_check_mark:](#substrings-return-value-for-each-encoding) **DO** include all 3 encodings (UTF-8, UTF-16, and CodePoint) for every string offset or length value in a service response.

[:white_check_mark:](#substrings-return-value-structure) **DO** define every string offset or length value in a service response as an object with the following structure:

| Property    | Type    | Required | Description |
| ----------- | ------- | :------: | ----------- |
| `utf8`      | integer | true     | The offset or length of the substring in UTF-8 encoding |
| `utf16`     | integer | true     | The offset or length of the substring in UTF-16 encoding |
| `codePoint` | integer | true     | The offset or length of the substring in CodePoint encoding |

[](#telemetry)
### Distributed Tracing & Telemetry

Azure SDK client guidelines specify that client libraries must send telemetry data through the `User-Agent` header, `X-MS-UserAgent` header, and Open Telemetry.
Client libraries are required to send telemetry and distributed tracing information on every  request. Telemetry information is vital to the effective operation of your service and should be a consideration from the outset of design and implementation efforts.

[:white_check_mark:](#telemetry-headers) **DO** follow the Azure SDK client guidelines for supporting telemetry headers and Open Telemetry.

[:no_entry:](#telemetry-allow-unrecognized-headers) **DO NOT** reject a call if you have custom headers you don't understand, and specifically, distributed tracing headers.

**Additional References**
- [Azure SDK client guidelines](https://azure.github.io/azure-sdk/general_azurecore.html)
- [Azure SDK User-Agent header policy](https://azure.github.io/azure-sdk/general_azurecore.html#azurecore-http-telemetry-x-ms-useragent)
- [Azure SDK Distributed tracing policy](https://azure.github.io/azure-sdk/general_azurecore.html#distributed-tracing-policy)
- [Open Telemetry](https://opentelemetry.io/)

In addition to distributed tracing, Azure also uses a set of common correlation headers:

|Name                         |Applies to|Description|
|-----------------------------|----------|-----------|
|x-ms-client-request-id       |Both      |Optional. Caller-specified value identifying the request, in the form of a GUID with no decoration such as curly braces (e.g. `x-ms-client-request-id: 9C4D50EE-2D56-4CD3-8152-34347DC9F2B0`). If the caller provides this header the service **must** include this in their log entries to facilitate correlation of log entries for a single request. Because this header can be client-generated, it should not be assumed to be unique by the service implementation.
|x-ms-request-id              |Response  |Required. Service generated correlation id identifying the request, in the form of a GUID with no decoration such as curly braces. In contrast to the the `x-ms-client-request-id`, the service **must** ensure that this value is globally unique. Services should log this value with their traces to facilitate correlation of log entries for a single request.