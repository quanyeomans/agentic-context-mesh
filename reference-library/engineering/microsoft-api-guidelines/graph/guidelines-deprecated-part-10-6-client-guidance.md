## 6. Client guidance
To ensure the best possible experience for clients talking to a REST service, clients SHOULD adhere to the following best practices:

### 6.1. Ignore rule
For loosely coupled clients where the exact shape of the data is not known before the call, if the server returns something the client wasn't expecting, the client MUST safely ignore it.

Some services MAY add fields to responses without changing versions numbers.
Services that do so MUST make this clear in their documentation and clients MUST ignore unknown fields.

### 6.2. Variable order rule
Clients MUST NOT rely on the order in which data appears in JSON service responses.
For example, clients SHOULD be resilient to the reordering of fields within a JSON object.
When supported by the service, clients MAY request that data be returned in a specific order.
For example, services MAY support the use of the _$orderBy_ querystring parameter to specify the order of elements within a JSON array.
Services MAY also explicitly specify the ordering of some elements as part of the service contract.
For example, a service MAY always return a JSON object's "type" information as the first field in an object to simplify response parsing on the client.
Clients MAY rely on ordering behavior explicitly identified by the service.

### 6.3. Silent fail rule
Clients requesting OPTIONAL server functionality (such as optional headers) MUST be resilient to the server ignoring that particular functionality.