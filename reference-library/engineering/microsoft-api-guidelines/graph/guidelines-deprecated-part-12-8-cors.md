## 8. CORS
Services compliant with the Microsoft REST API Guidelines MUST support [CORS (Cross Origin Resource Sharing)][cors].
Services SHOULD support an allowed origin of CORS * and enforce authorization through valid OAuth tokens.
Services SHOULD NOT support user credentials with origin validation.
There MAY be exceptions for special cases.

### 8.1. Client guidance
Web developers usually don't need to do anything special to take advantage of CORS.
All of the handshake steps happen invisibly as part of the standard XMLHttpRequest calls they make.

Many other platforms, such as .NET, have integrated support for CORS.

#### 8.1.1. Avoiding preflight
Because the CORS protocol can trigger preflight requests that add additional round trips to the server, performance-critical apps might be interested in avoiding them.
The spirit behind CORS is to avoid preflight for any simple cross-domain requests that old non-CORS-capable browsers were able to make.
All other requests require preflight.

A request is "simple" and avoids preflight if its method is GET, HEAD or POST, and if it doesn't contain any request headers besides Accept, Accept-Language and Content-Language.
For POST requests, the Content-Type header is also allowed, but only if its value is "application/x-www-form-urlencoded", "multipart/form-data" or "text/plain."
For any other headers or values, a preflight request will happen.

### 8.2. Service guidance
 At minimum, services MUST:
- Understand the Origin request header that browsers send on cross-domain requests, and the Access-Control-Request-Method request header that they send on preflight OPTIONS requests that check for access.
- If the Origin header is present in a request:
  - If the request uses the OPTIONS method and contains the Access-Control-Request-Method header, then it is a preflight request intended to probe for access before the actual request. Otherwise, it is an actual request. For preflight requests, beyond performing the steps below to add headers, services MUST perform no additional processing and MUST return a 200 OK. For non-preflight requests, the headers below are added in addition to the request's regular processing.
  - Add an Access-Control-Allow-Origin header to the response, containing the same value as the Origin request header. Note that this requires services to dynamically generate the header value. Resources that do not require cookies or any other form of [user credentials][cors-user-credentials] MAY respond with a wildcard asterisk (*) instead. Note that the wildcard is acceptable here only, and not for any of the other headers described below.
  - If the caller requires access to a response header that is not in the set of [simple response headers][cors-simple-headers] (Cache-Control, Content-Language, Content-Type, Expires, Last-Modified, Pragma), then add an Access-Control-Expose-Headers header containing the list of additional response header names the client should have access to.
  - If the request requires cookies, then add an Access-Control-Allow-Credentials header set to "true."
  - If the request was a preflight request (see first bullet), then the service MUST:
    - Add an Access-Control-Allow-Headers response header containing the list of request header names the client is permitted to use. This list need only contain headers that are not in the set of [simple request headers][cors-simple-headers] (Accept, Accept-Language, Content-Language). If there are no restrictions on headers the service accepts, the service MAY simply return the same value as the Access-Control-Request-Headers header sent by the client.
    - Add an Access-Control-Allow-Methods response header containing the list of HTTP methods the caller is permitted to use.

Add an Access-Control-Max-Age pref response header containing the number of seconds for which this preflight response is valid (and hence can be avoided before subsequent actual requests). Note that while it is customary to use a large value like 2592000 (30 days), many browsers self-impose a much lower limit (e.g., five minutes).

Because browser preflight response caches are notoriously weak, the additional round trip from a preflight response hurts performance.
Services used by interactive Web clients where performance is critical SHOULD avoid patterns that cause a preflight request
- For GET and HEAD calls, avoid requiring request headers that are not part of the simple set above. Allow them to be provided as query parameters instead.
  - The Authorization header is not part of the simple set, so the authentication token MUST be sent through the "access_token" query parameter instead, for resources requiring authentication. Note that passing authentication tokens in the URL is not recommended, because it can lead to the token getting recorded in server logs and exposed to anyone with access to those logs. Services that accept authentication tokens through the URL MUST take steps to mitigate the security risks, such as using short-lived authentication tokens, suppressing the auth token from getting logged, and controlling access to server logs.

- Avoid requiring cookies. XmlHttpRequest will only send cookies on cross-domain requests if the "withCredentials" attribute is set; this also causes a preflight request.
  - Services that require cookie-based authentication MUST use a "dynamic canary" to secure all APIs that accept cookies.

- For POST calls, prefer simple Content-Types in the set of ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain") where applicable. Any other Content-Type will induce a preflight request.
  - Services MUST NOT contravene other API recommendations in the name of avoiding CORS preflight requests. In particular, in accordance with recommendations, most POST requests will actually require a preflight request due to the Content-Type.
  - If eliminating preflight is critical, then a service MAY support alternative mechanisms for data transfer, but the RECOMMENDED approach MUST also be supported.

In addition, when appropriate services MAY support the JSONP pattern for simple, GET-only cross-domain access.
In JSONP, services take a parameter indicating the format (_$format=json_) and a parameter indicating a callback (_$callback=someFunc_), and return a text/javascript document containing the JSON response wrapped in a function call with the indicated name.
More on JSONP at Wikipedia: [JSONP](https://en.wikipedia.org/wiki/JSONP).