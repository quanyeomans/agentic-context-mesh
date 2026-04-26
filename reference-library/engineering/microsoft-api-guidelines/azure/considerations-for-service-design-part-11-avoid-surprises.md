## Avoid Surprises
A major inhibitor to adoption and usage is when an API behaves in an unexpected way. Often, these are subtle design decisions that seem benign at the time, but end up introducing significant downstream friction for developers.

One common area of friction for developers is _polymorphism_ -- where a value may have any of several types or structures.
Polymorphism can be beneficial in certain cases, e.g. as a way to express inheritance, but also creates friction because it requires the value to be introspected before being processed and cannot be represented in a natural/useful way in many nominally typed languages. The use of a discriminator field (`kind`) simplifies the introspection, but developers frequently end up having to explicitly cast the response to the appropriate type in order to use it.

Collections are another common area of friction for developers. It is important to define collections in a consistent manner within your service and across services of the platform.  In particular, features such as pagination, filtering, and sorting, when supported, should follow common API patterns. See [Collections](./Guidelines.md#collections) for specific guidance.

An important consideration when defining a new service is support for pagination.

[:ballot_box_with_check:](#support-paging) **YOU SHOULD** support server-side paging, even if your resource does not currently need paging. This avoids a breaking change when your service expands. See [Collections](./Guidelines.md#collections) for specific guidance.

Another consideration for collections is support for sorting the set of returned items with the _orderby_ query parameter.
Sorting collection results can be extremely expensive for a service to implement as it must retrieve all items to sort them. And if the operation supports paging (which is likely), then a client request to get another page may have to retrieve all items and sort them again to determine which items are on the desired page.

[:heavy_check_mark:](#paging-orderby) **YOU MAY** support `orderby` if customer scenarios really demand it and the service is confident that it can support it in perpetuity (even if the backing storage service changes someday).

Another important design pattern for avoiding surprises is idempotency. An operation is idempotent if it can be performed multiple times and have the same result as a single execution.
HTTP requires certain operations like GET, PUT, and DELETE to be idempotent, but for cloud services it is important to make _all_ operations idempotent so that clients can use retry in failure scenarios without risk of unintended consequences.
See the [HTTP Request / Response Pattern section of the Guidelines](./Guidelines.md#http-request--response-pattern) for detailed guidance on making operations idempotent.