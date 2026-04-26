## 7. Consistency fundamentals
### 7.1. URL structure
Humans SHOULD be able to easily read and construct URLs.

This facilitates discovery and eases adoption on platforms without a well-supported client library.

An example of a well-structured URL is:

```
https://api.contoso.com/v1.0/people/jdoe@contoso.com/inbox
```

An example URL that is not friendly is:

```
https://api.contoso.com/EWS/OData/Users('jdoe@microsoft.com')/Folders('AAMkADdiYzI1MjUzLTk4MjQtNDQ1Yy05YjJkLWNlMzMzYmIzNTY0MwAuAAAAAACzMsPHYH6HQoSwfdpDx-2bAQCXhUk6PC1dS7AERFluCgBfAAABo58UAAA=')
```

A frequent pattern that comes up is the use of URLs as values.
Services MAY use URLs as values.
For example, the following is acceptable:

```
https://api.contoso.com/v1.0/items?url=https://resources.contoso.com/shoes/fancy
```

### 7.2. URL length
The HTTP 1.1 message format, defined in RFC 7230, in section [3.1.1][rfc-7230-3-1-1], defines no length limit on the Request Line, which includes the target URL.
From the RFC:

> HTTP does not place a predefined limit on the length of a
   request-line. [...] A server that receives a request-target longer than any URI it wishes to parse MUST respond
   with a 414 (URI Too Long) status code.

Services that can generate URLs longer than 2,083 characters MUST make accommodations for the clients they wish to support.
Here are some sources for determining what target clients support:

 * [https://stackoverflow.com/a/417184](https://stackoverflow.com/a/417184)
 * [https://blogs.msdn.microsoft.com/ieinternals/2014/08/13/url-length-limits/](https://blogs.msdn.microsoft.com/ieinternals/2014/08/13/url-length-limits/)

Also note that some technology stacks have hard and adjustable URL limits, so keep this in mind as you design your services.

### 7.3. Canonical identifier
In addition to friendly URLs, resources that can be moved or be renamed SHOULD expose a URL that contains a unique stable identifier.
It MAY be necessary to interact with the service to obtain a stable URL from the friendly name for the resource, as in the case of the "/my" shortcut used by some services.

The stable identifier is not required to be a GUID.

An example of a URL containing a canonical identifier is:

```
https://api.contoso.com/v1.0/people/7011042402/inbox
```

### 7.4. Supported methods
Operations MUST use the proper HTTP methods whenever possible, and operation idempotency MUST be respected.
HTTP methods are frequently referred to as the HTTP verbs.
The terms are synonymous in this context, however the HTTP specification uses the term method.

Below is a list of methods that Microsoft REST services SHOULD support.
Not all resources will support all methods, but all resources using the methods below MUST conform to their usage.

Method  | Description                                                                                                                | Is Idempotent
------- | -------------------------------------------------------------------------------------------------------------------------- | -------------
GET     | Return the current value of an object                                                                                      | True
PUT     | Replace an object, or create a named object, when applicable                                                               | True
DELETE  | Delete an object                                                                                                           | True
POST    | Create a new object based on the data provided, or submit a command                                                        | False
HEAD    | Return metadata of an object for a GET response. Resources that support the GET method MAY support the HEAD method as well | True
PATCH   | Apply a partial update to an object                                                                                        | False
OPTIONS | Get information about a request; see below for details.                                                                    | True

<small>Table 1</small>

#### 7.4.1. POST
POST operations SHOULD support the Location response header to specify the location of any created resource that was not explicitly named, via the Location header.

As an example, imagine a service that allows creation of hosted servers, which will be named by the service:

```http
POST http://api.contoso.com/account1/servers
```

The response would be something like:

```http
201 Created
Location: http://api.contoso.com/account1/servers/server321
```

Where "server321" is the service-allocated server name.

Services MAY also return the full metadata for the created item in the response.

#### 7.4.2. PATCH
PATCH has been standardized by IETF as the method to be used for updating an existing object incrementally (see [RFC 5789][rfc-5789]).
Microsoft REST API Guidelines compliant APIs SHOULD support PATCH.

#### 7.4.3. Creating resources via PATCH (UPSERT semantics)
Services that allow callers to specify key values on create SHOULD support UPSERT semantics, and those that do MUST support creating resources using PATCH.
Because PUT is defined as a complete replacement of the content, it is dangerous for clients to use PUT to modify data.
Clients that do not understand (and hence ignore) properties on a resource are not likely to provide them on a PUT when trying to update a resource, hence such properties could be inadvertently removed.
Services MAY optionally support PUT to update existing resources, but if they do they MUST use replacement semantics (that is, after the PUT, the resource's properties MUST match what was provided in the request, including deleting any server properties that were not provided).

Under UPSERT semantics, a PATCH call to a nonexistent resource is handled by the server as a "create", and a PATCH call to an existing resource is handled as an "update". To ensure that an update request is not treated as a create or vice versa, the client MAY specify precondition HTTP headers in the request.
The service MUST NOT treat a PATCH request as an insert if it contains an If-Match header and MUST NOT treat a PATCH request as an update if it contains an If-None-Match header with a value of "*".

If a service does not support UPSERT, then a PATCH call against a resource that does not exist MUST result in an HTTP "409 Conflict" error.

#### 7.4.4. Options and link headers
OPTIONS allows a client to retrieve information about a resource, at a minimum by returning the Allow header denoting the valid methods for this resource.

In addition, services SHOULD include a Link header (see [RFC 5988][rfc-5988]) to point to documentation for the resource in question:

```http
Link: <{help}>; rel="help"
```

Where {help} is the URL to a documentation resource.

For examples on use of OPTIONS, see [preflighting CORS cross-domain calls][cors-preflight].

### 7.5. Standard request headers
The table of request headers below SHOULD be used by Microsoft REST API Guidelines services.
Using these headers is not mandated, but if used they MUST be used consistently.

All header values MUST follow the syntax rules set forth in the specification where the header field is defined.
Many HTTP headers are defined in [RFC7231][rfc-7231], however a complete list of approved headers can be found in the [IANA Header Registry][IANA-headers]."

Header                            | Type                                  | Description
--------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
Authorization                     | String                                           | Authorization header for the request
Date                              | Date                                             | Timestamp of the request, based on the client's clock, in [RFC 5322][rfc-5322-3-3] date and time format.  The server SHOULD NOT make any assumptions about the accuracy of the client's clock.  This header MAY be included in the request, but MUST be in this format when supplied.  Greenwich Mean Time (GMT) MUST be used as the time zone reference for this header when it is provided.  For example: `Wed, 24 Aug 2016 18:41:30 GMT`.  Note that GMT is exactly equal to UTC (Coordinated Universal Time) for this purpose.
Accept                            | Content type                                     | The requested content type for the response such as: <li>application/xml</li><li>text/xml</li><li>application/json</li><li>text/javascript (for JSONP)</li>Per the HTTP guidelines, this is just a hint and responses MAY have a different content type, such as a blob fetch where a successful response will just be the blob stream as the payload. For services following OData, the preference order specified in OData SHOULD be followed.
Accept-Encoding                   | Gzip, deflate                                    | REST endpoints SHOULD support GZIP and DEFLATE encoding, when applicable. For very large resources, services MAY ignore and return uncompressed data.
Accept-Language                   | "en", "es", etc.                                 | Specifies the preferred language for the response. Services are not required to support this, but if a service supports localization it MUST do so through the Accept-Language header.
Accept-Charset                    | Charset type like "UTF-8"                        | Default is UTF-8, but services SHOULD be able to handle ISO-8859-1.
Content-Type                      | Content type                                     | Mime type of request body (PUT/POST/PATCH)
Prefer                            | return=minimal, return=representation            | If the return=minimal preference is specified, services SHOULD return an empty body in response to a successful insert or update. If return=representation is specified, services SHOULD return the created or updated resource in the response. Services SHOULD support this header if they have scenarios where clients would sometimes benefit from responses, but sometimes the response would impose too much of a hit on bandwidth.
If-Match, If-None-Match, If-Range | String                                           | Services that support updates to resources using optimistic concurrency control MUST support the If-Match header to do so. Services MAY also use other headers related to ETags as long as they follow the HTTP specification.

### 7.6. Standard response headers
Services SHOULD return the following response headers, except where noted in the "required" column.

Response Header    | Required                                      | Description
------------------ | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
Date               | All responses                                 | Timestamp the response was processed, based on the server's clock, in [RFC 5322][rfc-5322-3-3] date and time format.  This header MUST be included in the response.  Greenwich Mean Time (GMT) MUST be used as the time zone reference for this header.  For example: `Wed, 24 Aug 2016 18:41:30 GMT`. Note that GMT is exactly equal to UTC (Coordinated Universal Time) for this purpose.
Content-Type       | All responses                                 | The content type
Content-Encoding   | All responses                                 | GZIP or DEFLATE, as appropriate
Preference-Applied | When specified in request                     | Whether a preference indicated in the Prefer request header was applied
ETag               | When the requested resource has an entity tag | The ETag response-header field provides the current value of the entity tag for the requested variant. Used with If-Match, If-None-Match and If-Range to implement optimistic concurrency control.

### 7.7. Custom headers
Custom headers MUST NOT be required for the basic operation of a given API.

Some of the guidelines in this document prescribe the use of nonstandard HTTP headers.
In addition, some services MAY need to add extra functionality, which is exposed via HTTP headers.
The following guidelines help maintain consistency across usage of custom headers.

Headers that are not standard HTTP headers MUST have one of two formats:

1. A generic format for headers that are registered as "provisional" with IANA ([RFC 3864][rfc-3864])
2. A scoped format for headers that are too usage-specific for registration

These two formats are described below.

### 7.8. Specifying headers as query parameters
Some headers pose challenges for some scenarios such as AJAX clients, especially when making cross-domain calls where adding headers MAY not be supported.
As such, some headers MAY be accepted as Query Parameters in addition to headers, with the same naming as the header:

Not all headers make sense as query parameters, including most standard HTTP headers.

The criteria for considering when to accept headers as parameters are:

1. Any custom headers MUST be also accepted as parameters.
2. Required standard headers MAY be accepted as parameters.
3. Required headers with security sensitivity (e.g., Authorization header) MIGHT NOT be appropriate as parameters; the service owner SHOULD evaluate these on a case-by-case basis.

The one exception to this rule is the Accept header.
It's common practice to use a scheme with simple names instead of the full functionality described in the HTTP specification for Accept.

### 7.9. PII parameters
Consistent with their organization's privacy policy, clients SHOULD NOT transmit personally identifiable information (PII) parameters in the URL (as part of path or query string) because this information can be inadvertently exposed via client, network, and server logs and other mechanisms.

Consequently, a service SHOULD accept PII parameters transmitted as headers.

However, there are many scenarios where the above recommendations cannot be followed due to client or software limitations.
To address these limitations, services SHOULD also accept these PII parameters as part of the URL consistent with the rest of these guidelines.

Services that accept PII parameters -- whether in the URL or as headers -- SHOULD be compliant with privacy policy specified by their organization's engineering leadership.
This will typically include recommending that clients prefer headers for transmission and implementations adhere to special precautions to ensure that logs and other service data collection are properly handled.

### 7.10. Response formats
For organizations to have a successful platform, they must serve data in formats developers are accustomed to using, and in consistent ways that allow developers to handle responses with common code.

Web-based communication, especially when a mobile or other low-bandwidth client is involved, has moved quickly in the direction of JSON for a variety of reasons, including its tendency to be lighter weight and its ease of consumption with JavaScript-based clients.

JSON property names SHOULD be camelCased.

Services SHOULD provide JSON as the default encoding.

#### 7.10.1. Clients-specified response format
In HTTP, response format SHOULD be requested by the client using the Accept header.
This is a hint, and the server MAY ignore it if it chooses to, even if this isn't typical of well-behaved servers.
Clients MAY send multiple Accept headers and the service MAY choose one of them.

The default response format (no Accept header provided) SHOULD be application/json, and all services MUST support application/json.

Accept Header    | Response type                      | Notes
---------------- | ---------------------------------- | -------------------------------------------
application/json | Payload SHOULD be returned as JSON | Also accept text/javascript for JSONP cases

```http
GET https://api.contoso.com/v1.0/products/user
Accept: application/json
```

#### 7.10.2. Error condition responses
For non-success conditions, developers SHOULD be able to write one piece of code that handles errors consistently across different Microsoft REST API Guidelines services.
This allows building of simple and reliable infrastructure to handle exceptions as a separate flow from successful responses.
The following is based on the OData v4 JSON spec.
However, it is very generic and does not require specific OData constructs.
APIs SHOULD use this format even if they are not using other OData constructs.

The error response MUST be a single JSON object.
This object MUST have a name/value pair named "error". The value MUST be a JSON object.

This object MUST contain name/value pairs with the names "code" and "message", and it MAY contain name/value pairs with the names "target", "details" and "innererror."

The value for the "code" name/value pair is a language-independent string.
Its value is a service-defined error code that SHOULD be human-readable.
This code serves as a more specific indicator of the error than the HTTP error code specified in the response.
Services SHOULD have a relatively small number (about 20) of possible values for "code", and all clients MUST be capable of handling all of them.
Most services will require a much larger number of more specific error codes, which are not interesting to all clients.
These error codes SHOULD be exposed in the "innererror" name/value pair as described below.
Introducing a new value for "code" that is visible to existing clients is a breaking change and requires a version increase.
Services can avoid breaking changes by adding new error codes to "innererror" instead.

The value for the "message" name/value pair MUST be a human-readable representation of the error.
It is intended as an aid to developers and is not suitable for exposure to end users.
Services wanting to expose a suitable message for end users MUST do so through an [annotation][odata-json-annotations] or custom property.
Services SHOULD NOT localize "message" for the end user, because doing so might make the value unreadable to the app developer who may be logging the value, as well as make the value less searchable on the Internet.

The value for the "target" name/value pair is the target of the particular error (e.g., the name of the property in error).

The value for the "details" name/value pair MUST be an array of JSON objects that MUST contain name/value pairs for "code" and "message", and MAY contain a name/value pair for "target", as described above.
The objects in the "details" array usually represent distinct, related errors that occurred during the request.
See example below.

The value for the "innererror" name/value pair MUST be an object.
The contents of this object are service-defined.
Services wanting to return more specific errors than the root-level code MUST do so by including a name/value pair for "code" and a nested "innererror". Each nested "innererror" object represents a higher level of detail than its parent.
When evaluating errors, clients MUST traverse through all of the nested "innererrors" and choose the deepest one that they understand.
This scheme allows services to introduce new error codes anywhere in the hierarchy without breaking backwards compatibility, so long as old error codes still appear.
The service MAY return different levels of depth and detail to different callers.
For example, in development environments, the deepest "innererror" MAY contain internal information that can help debug the service.
To guard against potential security concerns around information disclosure, services SHOULD take care not to expose too much detail unintentionally.
Error objects MAY also include custom server-defined name/value pairs that MAY be specific to the code.
Error types with custom server-defined properties SHOULD be declared in the service's metadata document.
See example below.

Error responses MAY contain [annotations][odata-json-annotations] in any of their JSON objects.

We recommend that for any transient errors that may be retried, services SHOULD include a Retry-After HTTP header indicating the minimum number of seconds that clients SHOULD wait before attempting the operation again.

##### ErrorResponse : Object

Property | Type | Required | Description
-------- | ---- | -------- | -----------
`error` | Error | ✔ | The error object.

##### Error : Object

Property | Type | Required | Description
-------- | ---- | -------- | -----------
`code` | String | ✔ | One of a server-defined set of error codes.
`message` | String | ✔ | A human-readable representation of the error.
`target` | String |  | The target of the error.
`details` | Error[] |  | An array of details about specific errors that led to this reported error.
`innererror` | InnerError |  | An object containing more specific information than the current object about the error.

##### InnerError : Object

Property | Type | Required | Description
-------- | ---- | -------- | -----------
`code` | String |  | A more specific error code than was provided by the containing error.
`innererror` | InnerError |  | An object containing more specific information than the current object about the error.

##### Examples

Example of "innererror":

```json
{
  "error": {
    "code": "BadArgument",
    "message": "Previous passwords may not be reused",
    "target": "password",
    "innererror": {
      "code": "PasswordError",
      "innererror": {
        "code": "PasswordDoesNotMeetPolicy",
        "minLength": "6",
        "maxLength": "64",
        "characterTypes": ["lowerCase","upperCase","number","symbol"],
        "minDistinctCharacterTypes": "2",
        "innererror": {
          "code": "PasswordReuseNotAllowed"
        }
      }
    }
  }
}
```

In this example, the most basic error code is "BadArgument", but for clients that are interested, there are more specific error codes in "innererror."
The "PasswordReuseNotAllowed" code may have been added by the service at a later date, having previously only returned "PasswordDoesNotMeetPolicy."
Existing clients do not break when the new error code is added, but new clients MAY take advantage of it.
The "PasswordDoesNotMeetPolicy" error also includes additional name/value pairs that allow the client to determine the server's configuration, validate the user's input programmatically, or present the server's constraints to the user within the client's own localized messaging.

Example of "details":

```json
{
  "error": {
    "code": "BadArgument",
    "message": "Multiple errors in ContactInfo data",
    "target": "ContactInfo",
    "details": [
      {
        "code": "NullValue",
        "target": "PhoneNumber",
        "message": "Phone number must not be null"
      },
      {
        "code": "NullValue",
        "target": "LastName",
        "message": "Last name must not be null"
      },
      {
        "code": "MalformedValue",
        "target": "Address",
        "message": "Address is not valid"
      }
    ]
  }
}
```

In this example there were multiple problems with the request, with each individual error listed in "details."

### 7.11. HTTP Status Codes
Standard HTTP Status Codes SHOULD be used; see the HTTP Status Code definitions for more information.

### 7.12. Client library optional
Developers MUST be able to develop on a wide variety of platforms and languages, such as Windows, macOS, Linux, C#, Python, Node.js, and Ruby.

Services SHOULD be able to be accessed from simple HTTP tools such as curl without significant effort.

Service developer portals SHOULD provide the equivalent of "Get Developer Token" to facilitate experimentation and curl support.