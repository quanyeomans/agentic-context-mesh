## Conditional Requests

When designing an API, you will almost certainly have to manage how your resource is updated. For example, if your resource is a bank account, you will want to ensure that one transaction--say depositing money--does not overwrite a previous transaction.
Similarly, it could be very expensive to send a resource to a client. This could be because of its size, network conditions, or a myriad of other reasons.
Both of these scenarios can be accomplished with conditional requests, where the client specifies a _precondition_
for execution of a request, based on its last modification date or entity tag ("ETag").
An ETag identifies a 'version' or 'instance' of a resource and is computed by the service and returned in an `ETag` response header for GET or other operations on the resource.

### Cache Control

One of the more common uses for conditional requests is cache control. This is especially useful when resources are large in size, expensive to compute/calculate, or hard to reach (significant network latency).
A client can make a "conditional GET request" for the resource, with a precondition header that requests that
data be returned only when the version on the service does not match the ETag or last modified date in the header.
If there are no changes, then there is no need to return the resource, as the client already has the most recent version.

Implementing this strategy is relatively straightforward. First, you will return an `ETag` with a value that uniquely identifies the instance (or version) of the resource. The [Computing ETags](./Guidelines.md#computing-etags) section provides guidance on how to properly calculate the value of your `ETag`.
In these scenarios, when a request is made by the client an `ETag` header is returned, with a value that uniquely identifies that specific instance (or version) of the resource. The `ETag` value can then be sent in subsequent requests as part of the `If-None-Match` header.
This tells the service to compare the `ETag` that came in with the request, with the latest value that it has calculated. If the two values are the same, then it is not necessary to return the resource to the client--it already has it. If they are different, then the service will return the latest version of the resource, along with the updated `ETag` value in the header.

### Optimistic Concurrency

Optimistic concurrency is a strategy used in HTTP to avoid the "lost update" problem that can occur when multiple clients attempt to update a resource simultaneously.
Clients can use ETags returned by the service to specify a _precondition_ for the execution of an update, to ensure that the resource has not been updated since the client last observed it.
For example, the client can specify an `If-Match` header with the last ETag value received by the client in an update request.
The service processes the update only if the ETag value in the header matches the ETag of the current resource on the server.
By computing and returning ETags for your resources, you enable clients to avoid using a strategy where the "last write always wins."