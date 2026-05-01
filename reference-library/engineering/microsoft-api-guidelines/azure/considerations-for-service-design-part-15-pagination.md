## Pagination

Operations that return a collection of resources must consider pagination.
There are hard limits to the payload size of HTTP responses, and when the size of a collection or the resources themselves
can grow arbitrarily large there is the risk of exceeding this limit if the operation does not support pagination.
Further, adding support for pagination is a breaking change so it should be supported in the initial GA of the service
if there is any possibility that it will eventually be needed.

There are two forms of pagination that MAY be supported by RESTful APIs.
Server-driven paging mitigates against denial-of-service attacks by forcibly paginating a request over multiple response payloads.
Client-driven paging enables clients to request only the number of resources that it can use at a given time.
Services should almost always support server-driven paging and may optionally support client-driven paging.

### Server-driven paging

In server-driven paging, the service includes a `nextLink` property in the response to indicate that additional elements
exist in the collection.
The value of the `nextLink` property should be an opaque absolute URL that will return the next page of results.
The absence of a `nextLink` property means that no additional pages are available.
Since `nextLink` is an opaque URL it should include any query parameters required by the service, including `api-version`.
The service should honor a request to a URL derived from `nextLink` by replacing the value for the `apl-version` query parameter
with a different but valid api version. The service may reject the request if any other element of `nextLink` was modified.

The service determines how many items to include in the response and may choose a different number for different collections and even for different pages of the same collection.
An operation may allow the client to specify a maximum number of items in a response with an optional `maxpagesize` parameter.
Operations that support `maxpagesize` should return no more than the value specified in `maxpagesize` but may return fewer.

### Client-driven paging

An operation may support `skip` and `top` query parameters to allow the client to specify an offset into the collection
and the number of results to return, respectively.

Note that when `top` specifies a value larger than the server-driven paging page size, the response will be paged accordingly.