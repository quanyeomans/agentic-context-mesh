## Building Blocks: HTTP, REST, & JSON
The Microsoft Azure Cloud platform exposes its APIs through the core building blocks of the Internet; namely HTTP, REST, and JSON. This section provides you with a general understanding of how these technologies should be applied when creating your service.

[](#http)
### HTTP
Azure services must adhere to the HTTP specification, [RFC 7231](https://tools.ietf.org/html/rfc7231). This section further refines and constrains how service implementors should apply the constructs defined in the HTTP specification. It is therefore, important that you have a firm understanding of the following concepts:

- [Uniform Resource Locators (URLs)](#uniform-resource-locators-urls)
- [HTTP Request / Response Pattern](#http-request--response-pattern)
- [HTTP Query Parameters and Header Values](#http-query-parameters-and-header-values)

#### Uniform Resource Locators (URLs)

A Uniform Resource Locator (URL) is how developers access the resources of your service. Ultimately, URLs are how developers form a cognitive model of your service's resources.

[:white_check_mark:](#http-url-pattern) **DO** use this URL pattern:
```text
https://<tenant>.<region>.<service>.<cloud>/<service-root>/<resource-collection>/<resource-id>
```

Where:
 | Field | Description
 | - | - |
 | tenant | Regionally-unique ID representing a tenant (used for isolation, billing, quota enforcement, lifetime of resources, etc.)
 | region | Identifies the tenant's selected region. This region string MUST match one of the strings in the "Name" column returned from running this Azure CLI's "az account list-locations -o table"
 | service | Name of the service (ex: blobstore, servicebus, directory, or management)
 | cloud | Cloud domain name, e.g. `azure.net` (see Azure CLI's "az cloud list")
 | service&#x2011;root | Service-specific path (ex: blobcontainer, myqueue)
 | resource&#x2011;collection | Name of the collection, unabbreviated, pluralized
 | resource&#x2011;id | Id of resource within the resource-collection. This MUST be the raw string/number/guid value with no quoting but properly escaped to fit in a URL segment.

[:white_check_mark:](#http-url-casing) **DO** use kebab-casing (preferred) or camel-casing for URL path segments. If the segment refers to a JSON field, use camel casing.

[:white_check_mark:](#http-url-length) **DO** return `414-URI Too Long` if a URL exceeds 2083 characters

[:white_check_mark:](#http-url-case-sensitivity) **DO** treat service-defined URL path segments as case-sensitive. If the passed-in case doesn't match what the service expects, the request **MUST** fail with a `404-Not found` HTTP return code.

Some customer-provided path segment values may be compared case-insensitivity if the abstraction they represent is normally compared with case-insensitivity. For example, a UUID path segment of 'c55f6b35-05f6-42da-8321-2af5099bd2a2' should be treated identical to 'C55F6B35-05F6-42DA-8321-2AF5099BD2A2'

[:white_check_mark:](#http-url-return-casing) **DO** ensure proper casing when returning a URL in an HTTP response header value or inside a JSON response body

[:white_check_mark:](#http-url-allowed-characters) **DO** restrict the characters in service-defined path segments to `0-9  A-Z  a-z  -  .  _  ~`, with `:` allowed only as described below to designate an action operation.

[:ballot_box_with_check:](#http-url-allowed-characters-2) **YOU SHOULD** restrict the characters allowed in user-specified path segments (i.e. path parameters values) to `0-9  A-Z  a-z  -  .  _  ~` (do not allow `:`).

[:ballot_box_with_check:](#http-url-should-be-readable) **YOU SHOULD** keep URLs readable; if possible, avoid UUIDs & %-encoding (ex: Cádiz is %-encoded as C%C3%A1diz)

[:heavy_check_mark:](#http-url-allowed-characters-3) **YOU MAY** use these other characters in the URL path but they will likely require %-encoding [[RFC 3986](https://datatracker.ietf.org/doc/html/rfc3986#section-2.1)]: `/  ?  #  [  ]  @  !  $  &  '  (  )  *  +  ,  ;  =`

[:heavy_check_mark:](#http-direct-endpoints) **YOU MAY** support a direct endpoint URL for performance/routing:
```text
https://<tenant>-<service-root>.<service>.<cloud>/...
```

Examples:
- Request URL: `https://blobstore.azure.net/contoso.com/account1/container1/blob2`
- Response header ([RFC 2557](https://datatracker.ietf.org/doc/html/rfc2557#section-4)): `content-location : https://contoso-dot-com-account1.blobstore.azure.net/container1/blob2`
- GUID format: `https://00000000-0000-0000-C000-000000000046-account1.blobstore.azure.net/container1/blob2`

[:white_check_mark:](#http-url-return-consistent-form) **DO** return URLs in response headers/bodies in a consistent form regardless of the URL used to reach the resource. Either always a UUID for `<tenant>` or always a single verified domain.

[:heavy_check_mark:](#http-url-parameter-values) **YOU MAY** use URLs as values
```text
https://api.contoso.com/items?url=https://resources.contoso.com/shoes/fancy
```

#### HTTP Request / Response Pattern
The HTTP Request / Response pattern dictates how your API behaves. For example: POST methods that create resources must be idempotent, GET method results may be cached, the If-Modified and ETag headers offer optimistic concurrency. The URL of a service, along with its request/response bodies, establishes the overall contract that developers have with your service. As a service provider, how you manage the overall request / response pattern should be one of the first implementation decisions you make.

Cloud applications embrace failure. Therefore, to enable customers to write fault-tolerant applications, _all_ service operations (including POST) **must** be idempotent. Implementing services in an idempotent manner, with an "exactly once" semantic, enables developers to retry requests without the risk of unintended consequences.

##### Exactly Once Behavior = Client Retries & Service Idempotency

[:white_check_mark:](#http-all-methods-idempotent) **DO** ensure that _all_ HTTP methods are idempotent.

[:ballot_box_with_check:](#http-use-put-or-patch) **YOU SHOULD** use PUT or PATCH to create a resource as these HTTP methods are easy to implement, allow the customer to name their own resource, and are idempotent.

[:heavy_check_mark:](#http-post-must-be-idempotent) **YOU MAY** use POST to create a resource but you must make it idempotent and, of course, the response **MUST** return the URL of the created resource with a 201-Created. One way to make POST idempotent is to use the Repeatability-Request-ID & Repeatability-First-Sent headers (See [Repeatability of requests](#repeatability-of-requests)).

##### HTTP Return Codes

[:white_check_mark:](#http-success-status-codes) **DO** adhere to the return codes in the following table when the method completes synchronously and is successful:

Method | Description | Response Status Code
-------|-------------|---------------------
PATCH  | Create/Modify the resource with JSON Merge Patch | `200-OK`, `201-Created`
PUT    | Create/Replace the _whole_ resource | `200-OK`, `201-Created`
POST   | Create new resource (ID set by service) | `201-Created` with URL of created resource
POST   | Action | `200-OK`
GET    | Read (i.e. list) a resource collection | `200-OK`
GET    | Read the resource | `200-OK`
DELETE | Remove the resource | `204-No Content`\; avoid `404-Not Found`

[:white_check_mark:](#http-lro-status-code) **DO** return status code `202-Accepted` and follow the guidance in [Long-Running Operations & Jobs](#long-running-operations--jobs) when a PUT, POST, or DELETE method completes asynchronously.

[:white_check_mark:](#http-method-casing) **DO** treat method names as case sensitive and should always be in uppercase

[:white_check_mark:](#http-return-resource) **DO** return the state of the resource after a PUT, PATCH, POST, or GET operation with a `200-OK` or `201-Created`.

[:white_check_mark:](#http-delete-returns-204) **DO** return a `204-No Content` without a resource/body for a DELETE operation (even if the URL identifies a resource that does not exist; do not return `404-Not Found`)

[:white_check_mark:](#http-post-action-returns-200) **DO** return a `200-OK` from a POST Action. Include a body in the response, even if it has not properties, to allow properties to be added in the future if needed.

[:white_check_mark:](#http-return-403-vs-404) **DO** return a `403-Forbidden` when the user does not have access to the resource _unless_ this would leak information about the existence of the resource that should not be revealed for security/privacy reasons, in which case the response should be `404-Not Found`. [Rationale: a `403-Forbidden` is easier to debug for customers, but should not be used if even admitting the existence of things could potentially leak customer secrets.]

[:white_check_mark:](#http-support-optimistic-concurrency) **DO** support caching and optimistic concurrency by honoring the the `If-Match`, `If-None-Match`, if-modified-since, and if-unmodified-since request headers and by returning the ETag and last-modified response headers

#### HTTP Query Parameters and Header Values

[:white_check_mark:](#http-query-names-casing) **DO** use camel case for query parameter names.

Note: Certain legacy query parameter names use kebab-casing and are allowed only for backwards compatibility.

Because information in the service URL, as well as the request / response, are strings, there must be a predictable, well-defined scheme to convert strings to their corresponding values.

[:white_check_mark:](#http-parameter-validation) **DO** validate all query parameter and request header values and fail the operation with `400-Bad Request` if any value fails validation. Return an error response as described in the [Handling Errors](#handling-errors) section indicating what is wrong so customer can diagnose the issue and fix it themselves.

[:white_check_mark:](#http-parameter-serialization) **DO** use the following table when translating strings:

Data type | Document that string must be
--------- | -------
Boolean   | true / false (all lowercase)
Integer   | -2<sup>53</sup>+1 to +2<sup>53</sup>-1 (for consistency with JSON limits on integers [RFC 8259](https://datatracker.ietf.org/doc/html/rfc8259))
Float     | [IEEE-754 binary64](https://en.wikipedia.org/wiki/Double-precision_floating-point_format)
String    | (Un)quoted?, max length, legal characters, case-sensitive, multiple delimiter
UUID      | 123e4567-e89b-12d3-a456-426614174000 (no {}s, hyphens, case-insensitive) [RFC 4122](https://datatracker.ietf.org/doc/html/rfc4122)
Date/Time (Header) | Sun, 06 Nov 1994 08:49:37 GMT [RFC 7231, Section 7.1.1.1](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.1.1)
Date/Time (Query parameter) | YYYY-MM-DDTHH:mm:ss.sssZ (with at most 3 digits of fractional seconds) [RFC 3339](https://datatracker.ietf.org/doc/html/rfc3339)
Byte array | Base-64 encoded, max length
Array      | One of a) a comma-separated list of values (preferred), or b) separate `name=value` parameter instances for each value of the array


The table below lists the headers most used by Azure services:

Header Key          | Applies to | Example
------------------- | ---------- | -------------
_authorization_     | Request    | Bearer eyJ0...Xd6j (Support Azure Active Directory)
_x-ms-useragent_    | Request    | (see [Distributed Tracing & Telemetry](#distributed-tracing--telemetry))
traceparent         | Request    | (see [Distributed Tracing & Telemetry](#distributed-tracing--telemetry))
tracecontext        | Request    | (see [Distributed Tracing & Telemetry](#distributed-tracing--telemetry))
accept              | Request    | application/json
If-Match            | Request    | "67ab43" or * (no quotes) (see [Conditional Requests](#conditional-requests))
If-None-Match       | Request    | "67ab43" or * (no quotes) (see [Conditional Requests](#conditional-requests))
If-Modified-Since   | Request    | Sun, 06 Nov 1994 08:49:37 GMT (see [Conditional Requests](#conditional-requests))
If-Unmodified-Since | Request    | Sun, 06 Nov 1994 08:49:37 GMT (see [Conditional Requests](#conditional-requests))
date                | Both       | Sun, 06 Nov 1994 08:49:37 GMT (see [RFC 7231, Section 7.1.1.2](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.1.2))
_content-type_      | Both       | application/merge-patch+json
_content-length_    | Both       | 1024
_x-ms-request-id_   | Response   | 4227cdc5-9f48-4e84-921a-10967cb785a0
ETag                | Response   | "67ab43" (see [Conditional Requests](#conditional-requests))
last-modified       | Response   | Sun, 06 Nov 1994 08:49:37 GMT
_x-ms-error-code_   | Response   | (see [Handling Errors](#handling-errors))
_azure-deprecating_ | Response   | (see [Deprecating Behavior Notification](#deprecating-behavior-notification))
retry-after         | Response   | 180 (see [RFC 7231, Section 7.1.3](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.3))

[:white_check_mark:](#http-header-support-standard-headers) **DO** support all headers shown in _italics_

[:white_check_mark:](#http-header-names-casing) **DO** specify headers using kebab-casing

[:white_check_mark:](#http-header-names-case-sensitivity) **DO** compare request header names using case-insensitivity

[:white_check_mark:](#http-header-values-case-sensitivity) **DO** compare request header values using case-sensitivity if the header name requires it

[:white_check_mark:](#http-header-date-values) **DO** accept date values in headers in HTTP-Date format and return date values in headers in the IMF-fixdate format as defined in [RFC 7231, Section 7.1.1.1](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.1.1), e.g. "Sun, 06 Nov 1994 08:49:37 GMT".

Note: The RFC 7231 IMF-fixdate format is a "fixed-length and single-zone subset" of the RFC 1123 / RFC 5822 format, which means: a) year must be four digits, b) the seconds component of time is required, and c) the timezone must be GMT.

[:white_check_mark:](#http-header-request-id) **DO** create an opaque value that uniquely identifies the request and return this value in the `x-ms-request-id` response header.

Your service should include the `x-ms-request-id` value in error logs so that users can submit support requests for specific failures using this value.

[:no_entry:](#http-allow-unrecognized-headers) **DO NOT** fail a request that contains an unrecognized header. Headers may be added by API gateways or middleware and this must be tolerated

[:no_entry:](#http-no-x-custom-headers) **DO NOT** use "x-" prefix for custom headers, unless the header already exists in production [[RFC 6648](https://datatracker.ietf.org/doc/html/rfc6648)].

**Additional References**
- [StackOverflow - Difference between http parameters and http headers](https://stackoverflow.com/questions/40492782)
- [Standard HTTP Headers](https://httpwg.org/specs/rfc7231.html#header.field.registration)
- [Why isn't HTTP PUT allowed to do partial updates in a REST API?](https://stackoverflow.com/questions/19732423/why-isnt-http-put-allowed-to-do-partial-updates-in-a-rest-api)

[](#rest)
### REpresentational State Transfer (REST)
REST is an architectural style with broad reach that emphasizes scalability, generality, independent deployment, reduced latency via caching, and security. When applying REST to your API, you define your service’s resources as a collections of items.
These are typically the nouns you use in the vocabulary of your service. Your service's [URLs](#uniform-resource-locators-urls) determine the hierarchical path developers use to perform CRUD (create, read, update, and delete) operations on resources. Note, it's important to model resource state, not behavior.
There are patterns, later in these guidelines, that describe how to invoke behavior on your service. See [this article in the Azure Architecture Center](https://docs.microsoft.com/azure/architecture/best-practices/api-design) for a more detailed discussion of REST API design patterns.

When designing your service, it is important to optimize for the developer using your API.

[:white_check_mark:](#rest-clear-naming) **DO** focus heavily on clear & consistent naming

[:white_check_mark:](#rest-paths-make-sense) **DO** ensure your resource paths make sense

[:white_check_mark:](#rest-simplify-operations) **DO** simplify operations with few required query parameters & JSON fields

[:white_check_mark:](#rest-specify-string-value-constraints) **DO** establish clear contracts for string values

[:white_check_mark:](#rest-use-standard-status-codes) **DO** use proper response codes/bodies so customer can diagnose their own problems and fix them without contacting Azure support or the service team

#### Resource Schema & Field Mutability

[:white_check_mark:](#rest-response-body-is-resource-schema) **DO** use the same JSON schema for PUT request/response, PATCH response, GET response, and POST request/response on a given URL path. The PATCH request schema should contain all the same fields with no required fields. This allows one SDK type for input/output operations and enables the response to be passed back in a request.

[:white_check_mark:](#rest-field-mutability) **DO** think about your resource's fields and how they are used:

Field Mutability | Service Request's behavior for this field
-----------------| -----------------------------------------
**Create** | Service honors field only when creating a resource. Minimize create-only fields so customers don't have to delete & re-create the resource.
**Update** | Service honors field when creating or updating a resource
**Read**   | Service returns this field in a response. If the client passed a read-only field, the service **MUST** fail the request unless the passed-in value matches the resource's current value

In addition to the above, a field may be "required" or "optional". A required field is guaranteed to always exist and will typically _not_ become a nullable field in a SDK's data structure. This allows customers to write code without performing a null-check.
Because of this, required fields can only be introduced in the 1st version of a service; it is a breaking change to introduce required fields in a later version. In addition, it is a breaking change to remove a required field or make an optional field required or vice versa.

[:white_check_mark:](#rest-flat-is-better-than-nested) **DO** make fields simple and maintain a shallow hierarchy.

[:white_check_mark:](#rest-get-returns-json-body) **DO** use GET for resource retrieval and return JSON in the response body

[:white_check_mark:](#rest-patch-use-merge-patch) **DO** create and update resources using PATCH [RFC 5789] with JSON Merge Patch [(RFC 7396)](https://datatracker.ietf.org/doc/html/rfc7396) request body.

[:white_check_mark:](#rest-put-for-create-or-replace) **DO** use PUT with JSON for wholesale create/replace operations. **NOTE:** If a v1 client PUTs a resource; any fields introduced in V2+ should be reset to their default values (the equivalent to DELETE followed by PUT).

[:white_check_mark:](#rest-delete-resource) **DO** use DELETE to remove a resource.

[:white_check_mark:](#rest-fail-for-unknown-fields) **DO** fail an operation with `400-Bad Request` if the request is improperly-formed or if any JSON field name or value is not fully understood by the specific version of the service. Return an error response as described in [Handling errors](#handling-errors) indicating what is wrong so customer can diagnose the issue and fix it themselves.

[:heavy_check_mark:](#rest-secrets-allowed-in-post-response) **YOU MAY** return secret fields via POST **if absolutely necessary**.

[:no_entry:](#rest-no-secrets-in-get-response) **DO NOT** return secret fields via GET. For example, do not return `administratorPassword` in JSON.

[:no_entry:](#rest-no-computable-fields) **DO NOT** add fields to the JSON if the value is easily computable from other fields to avoid bloating the body.

##### Create / Update / Replace Processing Rules

[:white_check_mark:](#rest-put-patch-status-codes) **DO** follow the processing below to create/update/replace a resource:

When using this method | if this condition happens | use&nbsp;this&nbsp;response&nbsp;code
---------------------- | ------------------------- | ----------------------
PATCH/PUT | Any JSON field name/value not known/valid to the api-version | `400-Bad Request`
PATCH/PUT | Any Read field passed (client can't set Read fields) | `400-Bad Request`
| **If&nbsp;the&nbsp;resource&nbsp;does&nbsp;not&nbsp;exist** |
PATCH/PUT | Any mandatory Create/Update field missing | `400-Bad Request`
PATCH/PUT | Create resource using Create/Update fields | `201-Created`
| **If&nbsp;the&nbsp;resource&nbsp;already&nbsp;exists** |
PATCH | Any Create field doesn't match current value (allows retries) | `409-Conflict`
PATCH | Update resource using Update fields | `200-OK`
PUT | Any mandatory Create/Update field missing | `400-Bad Request`
PUT | Overwrite resource entirely using Create/Update fields | `200-OK`

#### Handling Errors
There are 2 kinds of errors:
- An error where you expect customer code to gracefully recover at runtime
- An error indicating a bug in customer code that is unlikely to be recoverable at runtime; the customer must just fix their code

[:white_check_mark:](#rest-error-code-header) **DO** return an `x-ms-error-code` response header with a string error code indicating what went wrong.

*NOTE: `x-ms-error-code` values are part of your API contract (because customer code is likely to do comparisons against them) and cannot change in the future.*

[:heavy_check_mark:](#rest-error-code-enum) **YOU MAY** implement the `x-ms-error-code` values as an enum with `"modelAsString": true` because it's possible add new values over time.  In particular, it's only a breaking change if the same conditions result in a *different* top-level error code.

[:warning:](#rest-add-codes-in-new-api-version) **YOU SHOULD NOT** add new top-level error codes to an existing API without bumping the service version.

[:white_check_mark:](#rest-descriptive-error-code-values) **DO** carefully craft unique `x-ms-error-code` string values for errors that are recoverable at runtime.  Reuse common error codes for usage errors that are not recoverable.

[:heavy_check_mark:](#rest-error-code-grouping) **YOU MAY** group common customer code errors into a few `x-ms-error-code` string values.

[:white_check_mark:](#rest-error-code-header-and-body-match) **DO** ensure that the top-level error's `code` value is identical to the `x-ms-error-code` header's value.

[:white_check_mark:](#rest-error-response-body-structure) **DO** provide a response body with the following structure:

**ErrorResponse** : Object

Property | Type | Required | Description
-------- | ---- | :------: | -----------
`error` | ErrorDetail | ✔ | The top-level error object whose `code` matches the `x-ms-error-code` response header

**ErrorDetail** : Object

Property | Type | Required | Description
-------- | ---- | :------: | -----------
`code` | String | ✔ | One of a server-defined set of error codes.
`message` | String | ✔ | A human-readable representation of the error.
`target` | String |  | The target of the error.
`details` | ErrorDetail[] |  | An array of details about specific errors that led to this reported error.
`innererror` | InnerError |  | An object containing more specific information than the current object about the error.
_additional properties_ |   | | Additional properties that can be useful when debugging.

**InnerError** : Object

Property | Type | Required | Description
-------- | ---- | :------: | -----------
`code` | String |  | A more specific error code than was provided by the containing error.
`innererror` | InnerError |  | An object containing more specific information than the current object about the error.

Example:
```json
{
  "error": {
    "code": "InvalidPasswordFormat",
    "message": "Human-readable description",
    "target": "target of error",
    "innererror": {
      "code": "PasswordTooShort",
      "minLength": 6,
    }
  }
}
```

[:white_check_mark:](#rest-document-error-code-values) **DO** document the service's top-level error code strings; they are part of the API contract.

[:heavy_check_mark:](#rest-error-non-api-contract-fields) **YOU MAY** treat the other fields as you wish as they are _not_ considered part of your service's API contract and customers should not take a dependency on them or their value. They exist to help customers self-diagnose issues.

[:heavy_check_mark:](#rest-error-additional-properties-allowed) **YOU MAY** add additional properties for any data values in your error message so customers don't resort to parsing your error message.  For example, an error with `"message": "A maximum of 16 keys are allowed per account."` might also add a `"maximumKeys": 16` property.  This is not part of your API contract and should only be used for diagnosing problems.

*Note: Do not use this mechanism to provide information developers need to rely on in code (ex: the error message can give details about why you've been throttled, but the `Retry-After` should be what developers rely on to back off).*

[:warning:](#rest-error-use-default-response) **YOU SHOULD NOT** document specific error status codes in your OpenAPI/Swagger spec unless the "default" response cannot properly describe the specific error response (e.g. body schema is different).

[](#json)
### JSON

[:white_check_mark:](#json-field-name-casing) **DO** use camel case for all JSON field names. Do not upper-case acronyms; use camel case.

[:white_check_mark:](#json-field-names-case-sensitivity) **DO** treat JSON field names with case-sensitivity.

[:white_check_mark:](#json-field-values-case-sensitivity) **DO** treat JSON field values with case-sensitivity. There may be some exceptions but avoid if at all possible.

[:white_check_mark:](#json-field-values-id) **DO** treat JSON field value representing a unique ID as an opaque string value and compare them with case-sensitivity. For example, IDs are frequently [UUIDs](https://en.wikipedia.org/wiki/Universally_unique_identifier), [CUIDs](https://github.com/paralleldrive/cuid2), [Nano ID](https://blog.openapihub.com/en-us/what-is-nano-id-its-difference-from-uuid-as-unique-identifiers/), or other formats. The choice of ID format is a service implementation detail. Customer code should only ever need to get, store, and send these values and should never perform any other kind of parsing or interpretation of ID values.

Services, and the clients that access them, may be written in multiple languages. To ensure interoperability, JSON establishes the "lowest common denominator" type system, which is always sent over the wire as UTF-8 bytes. This system is very simple and consists of three types:

 Type | Description
 ---- | -----------
 Boolean | true/false (always lowercase)
 Number  | Signed floating point (IEEE-754 binary64; int range: -2<sup>53</sup>+1 to +2<sup>53</sup>-1)
 String  | Used for everything else

[:no_entry:](#json-null-response-values) **DO NOT** send JSON fields with a null value from the service to the client. Instead, the service should just not send this field at all (this reduces payload size). Semantically, Azure services treat a missing field and a field with a value of null as identical. 

[:white_check_mark:](#json-null-request-values) **DO** accept JSON fields with a null value only for a PATCH operation with a JSON Merge Patch payload. A field with a value of null instructs the service to delete the field. If the field cannot be deleted, then return 400-BadRequest, else return the resource with the deleted field missing from the response payload (see bullet above).

[:white_check_mark:](#json-integer-values) **DO** use integers within the acceptable range of JSON number.

[:white_check_mark:](#json-specify-string-constraints) **DO** establish a well-defined contract for the format of strings. For example, determine minimum length, maximum length, legal characters, case-(in)sensitive comparisons, etc. Where possible, use standard formats, e.g. RFC 3339 for date/time.

[:white_check_mark:](#json-use-standard-string-formats) **DO** use strings formats that are well-known and easily parsable/formattable by many programming languages, e.g. RFC 3339 for date/time.

[:white_check_mark:](#json-should-be-round-trippable) **DO** ensure that information exchanged between your service and any client is "round-trippable" across multiple programming languages.

[:white_check_mark:](#json-date-time-is-rfc3339) **DO** use [RFC 3339](https://datatracker.ietf.org/doc/html/rfc3339) for date/time.

[:white_check_mark:](#json-durations-use-fixed-time-intervals) **DO** use a fixed time interval to express durations e.g., milliseconds, seconds, minutes, days, etc., and include the time unit in the property name e.g., `backupTimeInMinutes` or `ttlSeconds`.

[:heavy_check_mark:](#json-rfc3339-time-intervals-allowed) **YOU MAY** use [RFC 3339 time intervals](https://wikipedia.org/wiki/ISO_8601#Durations) only when users must be able to specify a time interval that may change from month to month or year to year e.g., "P3M" represents 3 months no matter how many days between the start and end dates, or "P1Y" represents 366 days on a leap year. The value must be round-trippable.

[:white_check_mark:](#json-uuid-is-rfc4412) **DO** use [RFC 4122](https://datatracker.ietf.org/doc/html/rfc4122) for UUIDs.

[:heavy_check_mark:](#json-may-nest-for-grouping) **YOU MAY** use JSON objects to group sub-fields together.

[:heavy_check_mark:](#json-use-arrays-for-ordering) **YOU MAY** use JSON arrays if maintaining an order of values is required. Avoid arrays in other situations since arrays can be difficult and inefficient to work with, especially with JSON Merge Patch where the entire array needs to be read prior to any operation being applied to it.

[:ballot_box_with_check:](#json-prefer-objects-over-arrays) **YOU SHOULD** use JSON objects instead of arrays whenever possible.

#### Enums & SDKs (Client libraries)
It is common for strings to have an explicit set of values. These are often reflected in the OpenAPI definition as enumerations. These are extremely useful for developer tooling, e.g. code completion, and client library generation.

However, it is not uncommon for the set of values to grow over the life of a service. For this reason, Microsoft's tooling uses the concept of an "extensible enum," which indicates that the set of values should be treated as only a _partial_ list.
This indicates to client libraries and customers that values of the enumeration field should be effectively treated as strings and that undocumented value may returned in the future. This enables the set of values to grow over time while ensuring stability in client libraries and customer code.

[:ballot_box_with_check:](#json-use-extensible-enums) **YOU SHOULD** use extensible enumerations unless you are positive that the symbol set will NEVER change over time.

[:white_check_mark:](#json-document-extensible-enums) **DO** document to customers that new values may appear in the future so that customers write their code today expecting these new values tomorrow.

[:heavy_check_mark:](#json-return-extensible-enum-value) **YOU MAY** return a value for an extensible enum that is not one of the values defined for the api-version specified in the request.

[:warning:](#json-accept-extensible-enum-value) **YOU SHOULD NOT** accept a value for an extensible enum that is not one of the values defined for the api-version specified in the request.

[:no_entry:](#json-removing-enum-value-is-breaking) **DO NOT** remove values from your enumeration list as this breaks customer code.

#### Polymorphic types

Polymorphism types in REST APIs refers to the possibility to use the same property of a request or response to have similar but different shapes. This is commonly expressed as a `oneOf` in JsonSchema or OpenAPI. In order to simplify how to determine which specific type a given request or response payload corresponds to, Azure requires the use of an explicit discriminator field.

Note: Polymorphic types can make your service more difficult for nominally typed languages to consume. See the corresponding section in the [Considerations for service design](./ConsiderationsForServiceDesign.md#avoid-surprises) for more information.

[:white_check_mark:](#json-use-discriminator-for-polymorphism) **DO** define a discriminator field indicating the kind of the resource and include any kind-specific fields in the body.

Below is an example of JSON for a Rectangle and Circle with a discriminator field named `kind`:

**Rectangle**
```json
{
   "kind": "rectangle",
   "x": 100,
   "y": 50,
   "width": 10,
   "length": 24,
   "fillColor": "Red",
   "lineColor": "White",
   "subscription": {
      "kind": "free"
   }
}
```

**Circle**
```json
{
   "kind": "circle",
   "x": 100,
   "y": 50,
   "radius": 10,
   "fillColor": "Green",
   "lineColor": "Black",
   "subscription": {
      "kind": "paid",
      "expiration": "2024",
      "invoice": "123456"
   }
}
```
Both Rectangle and Circle have common fields: `kind`, `fillColor`, `lineColor`, and `subscription`. A Rectangle also has `x`, `y`, `width`, and `length` while a Circle has `x`, `y`, and `radius`. The `subscription` is a nested polymorphic type. A `free` subscription has no additional fields and a `paid` subscription has `expiration` and `invoice` fields.

The [Azure Naming Guidelines](./ConsiderationsForServiceDesign.md#common-names) recommend that the discriminator field be named `kind`.

[:ballot_box_with_check:](#json-polymorphism-kind-extensible) **YOU SHOULD** define the discriminator field of a polymorphic type to be an extensible enum.

[:warning:](#json-polymorphism-kind-immutable) **YOU SHOULD NOT** allow an update (patch) to change the discriminator field of a polymorphic type.

[:warning:](#json-polymorphism-versioning) **YOU SHOULD NOT** return properties of a polymorphic type that are not defined for the api-version specified in the request.

[:warning:](#json-polymorphism-arrays) **YOU SHOULD NOT** have a property of an updatable resource whose value is an array of polymorphic objects.

Updating an array property with JSON merge-patch is not version-resilient if the array contains polymorphic types.