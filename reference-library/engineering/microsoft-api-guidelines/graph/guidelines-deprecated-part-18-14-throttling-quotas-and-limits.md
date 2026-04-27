## 14. Throttling, Quotas, and Limits
### 14.1. Principles
Services should be as responsive as possible, so as not to block callers.
As a rule of thumb any API call that is expected to take longer than 0.5 seconds in the 99th percentile, should consider using the Long-running Operations pattern for those calls.
Obviously, services cannot guarantee these response times in the face of potentially unlimited load from callers. Services should therefore design and document call request limits for clients, and respond with appropriate, actionable errors and error messages if these limits are exceeded.
Services should respond quickly with an error when they are generally overloaded, rather than simply respond slowly.
Finally, many services will have quotas on calls, perhaps a number of operations per hour or day, usually related to a service plan or price.
When these quotas are exceeded services must also provide immediate, actionable errors.
Quotas and Limits should be scoped to a customer unit: a subscription, a tenant, an application, a plan, or without any other identification a range of ip addresses…as appropriate to the service goals so that the load is properly shared and one unit is not interfering with another.

### 14.2. Return Codes (429 vs 503)
HTTP specifies two return codes for these scenarios: '429 Too Many Requests' and '503 Service Unavailable'.
Services should use 429 for cases where clients are making too many calls and can fix the situation by changing their call pattern.
Services should respond with 503 in cases where general load or other problems outside the control of the individual callers is responsible for the service becoming slow.
In all cases, services should also provide information suggesting how long the callers should wait before trying in again.
Clients should respect these headers and also implement other transient fault handling techniques.
However, there may be clients that simply retry immediately upon failure, potentially increasing the load on the service.
To handle this, services should design so that returning 429 or 503 is as inexpensive as possible, either by putting in special fastpath code, or ideally by depending on a common frontdoor or load balancer that provides this functionality.

### 14.3. Retry-After and RateLimit Headers
The Retry-After header is the standard way for responding to clients who are being throttled.
It is also common, but optional, in the case of limits and quotas (but not overall system load) to respond with header describing the limit that was exceeded.
However, services across Microsoft and the industry use a wide range of different headers for this purpose.
We recommend using three headers to describe the limit, the number of calls remaining under the limit, and the time when the limit will reset.
However, other headers may be appropriate for specific types of limits. In all cases these must be documented.

### 14.4. Service Guidance
Services should choose time windows as appropriate for the SLAs or business objectives.
In the case of Quotas, the Retry-After time and time window may be very long (hours, days, weeks, even months. Services use 429 to indicate the specific caller has made too many calls, and 503 to indicate that the service is load shedding but that it is not the caller’s responsibility.

#### 14.4.1. Responsiveness
1. Services MUST respond quickly in all circumstances, even when under load.
2. Calls that take longer than 1s to respond in the 99th percentile SHOULD use the Long-Running Operation pattern
3. Calls that take longer than 0.5s to respond in the 99th percentile should strongly consider the LRO pattern
4. Services SHOULD NOT introduce sleeps, pauses, etc. that block callers or are not actionable (“tar-pitting”).

#### 14.4.2. Rate Limits and Quotas
When a caller has made too many calls

1. Services MUST return a 429 code
2. Services MUST return a standard error response describing the specifics so that a programmer can make appropriate changes
3. Services MUST return a Retry-After header that indicates how long clients should wait before retrying
4. Services MAY return RateLimit headers that document the limit or quota that has been exceeded
5. Services MAY return RateLimit-Limit: the number of calls the client is allowed to make in a time window
6. Services MAY return RateLimit-Remaining: the number of calls remaining in the time window
7. Services MAY return RateLimit-Reset: the time at which the window resets in UTC epoch seconds
8. Services MAY return other service specific RateLimit headers as appropriate for more detailed information or specific limits or quotas

#### 14.4.3. Overloaded services
When services are generally overloaded and  load shedding

1. Services MUST Return a 503 code
2. Services MUST Return a standard error response (see 7.10.2) describing the specifics so that a programmer can make appropriate changes
3. Services MUST Return a Retry-After header that indicates how long clients should wait before retrying
4. In the 503 case, the service SHOULD NOT return RateLimit headers

#### 14.4.4. Example Response

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 5
RateLimit-Limit: 1000
RateLimit-Remaining: 0
RateLimit-Reset: 1538152773
{
  "error": {
    "code": "requestLimitExceeded",
    "message": "The caller has made too many requests in the time period.",
    "details": {
      "code": "RateLimit",
       "limit": "1000",
       "remaining": "0",
       "reset": "1538152773",
      }
    }
}
```

### 14.5. Caller Guidance
Callers include all users of the API: tools, portals, other services, not just user clients

1. Callers MUST wait for a minimum of time indicated in a response with a Retry-After before retrying a request.
2. Callers MAY assume that request is retriable after receiving a response with a Retry-After header without making any changes to the request.
3. Clients SHOULD use shared SDKs and common transient fault libraries to implement the proper behavior

See: https://docs.microsoft.com/en-us/azure/architecture/best-practices/transient-faults

### 14.6. Handling callers that ignore Retry-After headers
Ideally, 429 and 503 returns are so low cost that even clients that retry immediately can be handled.
In these cases, if possible the service team should make an effort to contact or fix the client.
If it is a known partner, a bug or incident should be filed.
In extreme cases it may be necessary to use DoS style protections such as blocking the caller.