## 15. Push notifications via webhooks
### 15.1. Scope
Services MAY implement push notifications via web hooks.
This section addresses the following key scenario:

> Push notification via HTTP Callbacks, often called Web Hooks, to publicly-addressable servers.

The approach set forth is chosen due to its simplicity, broad applicability, and low barrier to entry for service subscribers.
It's intended as a minimal set of requirements and as a starting point for additional functionality.

### 15.2. Principles
The core principles for services that support web hooks are:

1. Services MUST implement at least a poke/pull model. In the poke/pull model, a notification is sent to a client, and clients then send a request to get the current state or the record of change since their last notification. This approach avoids complexities around message ordering, missed messages, and change sets.  Services MAY add more data to provide rich notifications.
2. Services MUST implement the challenge/response protocol for configuring callback URLs.
3. Services SHOULD have a recommended age-out period, with flexibility for services to vary based on scenario.
4. Services SHOULD allow subscriptions that are raising successful notifications to live forever and SHOULD be tolerant of reasonable outage periods.
5. Firehose subscriptions MUST be delivered only over HTTPS. Services SHOULD require other subscription types to be HTTPS. See the "Security" section for more details.

### 15.3. Types of subscriptions
There are two subscription types, and services MAY implement either, both, or none.
The supported subscription types are:

1. Firehose subscriptions – a subscription is manually created for the subscribing application, typically in an app registration portal.  Notifications of activity that any users have consented to the app receiving are sent to this single subscription.
2. Per-resource subscriptions – the subscribing application uses code to programmatically create a subscription at runtime for some user-specific entity(s).

Services that support both subscription types SHOULD provide differentiated developer experiences for the two types:

1. Firehose – Services MUST NOT require developers to create code except to directly verify and respond to notifications.  Services MUST provide administrative UI for subscription management.  Services SHOULD NOT assume that end users are aware of the subscription, only the subscribing application's functionality.
2. Per-user – Services MUST provide an API for developers to create and manage subscriptions as part of their app as well as verifying and responding to notifications.  Services MAY expect end users to be aware of subscriptions and MUST allow end users to revoke subscriptions where they were created directly in response to user actions.

### 15.4. Call sequences
The call sequence for a firehose subscription MUST follow the diagram below.
It shows manual registration of application and subscription, and then the end user making use of one of the service's APIs.
At this part of the flow, two things MUST be stored:

1. The service MUST store the end user's act of consent to receiving notifications from this specific application (typically a background usage OAUTH scope.)
2. The subscribing application MUST store the end user's tokens in order to call back for details once notified of changes.

The final part of the sequence is the notification flow itself.

Non-normative implementation guidance:  A resource in the service changes and the service needs to run the following logic:

1. Determine the set of users who have access to the resource, and could thus expect apps to receive notifications about it on their behalf.
2. See which of those users have consented to receiving notifications and from which apps.
3. See which apps have registered a firehose subscription.
4. Join 1, 2, 3 to produce the concrete set of notifications that must be sent to apps.

It should be noted that the act of user consent and the act of setting up a firehose subscription could arrive in either order.
Services SHOULD send notifications with setup processed in either order.

![Firehose subscription setup][websequencediagram-firehose-subscription-setup]

For a per-user subscription, app registration is either manual or automated.
The call flow for a per-user subscription MUST follow the diagram below.
It shows the end user making use of one of the service's APIs, and again, the same two things MUST be stored:

1. The service MUST store the end user's act of consent to receiving notifications from this specific application (typically a background usage OAUTH scope).
2. The subscribing application MUST store the end user's tokens in order to call back for details once notified of changes.

In this case, the subscription is set up programmatically using the end-user's token from the subscribing application.
The app MUST store the ID of the registered subscription alongside the user tokens.

Non normative implementation guidance: In the final part of the sequence, when an item of data in the service changes and the service needs to run the following logic:

1. Find the set of subscriptions that correspond via resource to the data that changed.
2. For subscriptions created under an app+user token, send a notification to the app per subscription with the subscription ID and user id of the subscription-creator.
- For subscriptions created with an app only token, check that the owner of the changed data or any user that has visibility of the changed data has consented to notifications to the application, and if so send a set of notifications per user id to the app per subscription with the subscription ID.

  ![User subscription setup][websequencediagram-user-subscription-setup]

### 15.5. Verifying subscriptions
When subscriptions change either programmatically or in response to change via administrative UI portals, the subscribing service needs to be protected from malicious or unexpected calls from services pushing potentially large volumes of notification traffic.

For all subscriptions, whether firehose or per-user, services MUST send a verification request as part of creation or modification via portal UI or API request, before sending any other notifications.

Verification requests MUST be of the following format as an HTTP/HTTPS POST to the subscription's _notificationUrl_.

```http
POST https://{notificationUrl}?validationToken={randomString}
ClientState: clientOriginatedOpaqueToken (if provided by client on subscription-creation)
Content-Length: 0
```

For the subscription to be set up, the application MUST respond with 200 OK to this request, with the _validationToken_ value as the sole entity body.
Note that if the _notificationUrl_ contains query parameters, the _validationToken_ parameter must be appended with an `&`.

If any challenge request does not receive the prescribed response within 5 seconds of sending the request, the service MUST return an error, MUST NOT create the subscription, and MUST NOT send further requests or notifications to _notificationUrl_.

Services MAY perform additional validations on URL ownership.

### 15.6. Receiving notifications
Services SHOULD send notifications in response to service data changes that do not include details of the changes themselves, but include enough information for the subscribing application to respond appropriately to the following process:

1. Applications MUST identify the correct cached OAuth token to use for a callback
2. Applications MAY look up any previous delta token for the relevant scope of change
3. Applications MUST determine the URL to call to perform the relevant query for the new state of the service, which MAY be a delta query.

Services that are providing notifications that will be relayed to end users MAY choose to add more detail to notification packets in order to reduce incoming call load on their service.
 Such services MUST be clear that notifications are not guaranteed to be delivered and may be lossy or out of order.

Notifications MAY be aggregated and sent in batches.
Applications MUST be prepared to receive multiple events inside a single push notification.

The service MUST send all Web Hook data notifications as POST requests.

Services MUST allow for a 30-second timeout for notifications.
If a timeout occurs or the application responds with a 5xx response, then the service SHOULD retry the notification with exponential back-off.
All other responses will be ignored.

The service MUST NOT follow 301/302 redirect requests.

#### 15.6.1. Notification payload
The basic format for notification payloads is a list of events, each containing the id of the subscription whose referenced resources have changed, the type of change, the resource that should be consumed to identify the exact details of the change and sufficient identity information to look up the token required to call that resource.

For a firehose subscription, a concrete example of this may look like:

```json
{
  "value": [
    {
      "subscriptionId": "32b8cbd6174ab18b",
      "resource": "https://api.contoso.com/v1.0/users/user@contoso.com/files?$delta",
      "userId" : "",
      "tenantId" : "<Tenant Id>"
    }
  ]
}
```

For a per-user subscription, a concrete example of this may look like:

```json
{
  "value": [
    {
      "subscriptionId": "32b8cbd6174ab183",
      "clientState": "clientOriginatedOpaqueToken",
      "expirationDateTime": "2016-02-04T11:23Z",
      "resource": "https://api.contoso.com/v1.0/users/user@contoso.com/files/$delta",
      "userId" : "",
      "tenantId" : "<Tenant Id>"
    },
    {
      "subscriptionId": "97b391179fa22",
      "clientState ": "clientOriginatedOpaqueToken",
      "expirationDateTime": "2016-02-04T11:23Z",
      "resource": "https://api.contoso.com/v1.0/users/user@contoso.com/files/$delta",
      "userId" : "",
      "tenantId" : "<Tenant Id>"
    }
  ]
}
```

Following is a detailed description of the JSON payload.

A notification item consists a top-level object that contains an array of events, each of which identified the subscription due to which this notification is being sent.

Field | Description
----- | --------------------------------------------------------------------------------------------------
value | Array of events that have been raised within the subscription’s scope since the last notification.

Each item of the events array contains the following properties:

Field              | Description
------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
subscriptionId     | The id of the subscription due to which this notification has been sent.Services MUST provide the *subscriptionId* field.
clientState        | Services MUST provide the *clientState* field if it was provided at subscription creation time.
expirationDateTime | Services MUST provide the *expirationDateTime* field if the subscription has one.
resource           | Services MUST provide the resource field. This URL MUST be considered opaque by the subscribing application.  In the case of a richer notification it MAY be subsumed by message content that implicitly contains the resource URL to avoid duplication.If a service is providing this data as part of a more detailed data packet, then it need not be duplicated.
userId             | Services MUST provide this field for user-scoped resources.  In the case of user-scoped resources, the unique identifier for the user should be used.In the case of resources shared between a specific set of users, multiple notifications must be sent, passing the unique identifier of each user.For tenant-scoped resources, the user id of the subscription should be used.
tenantId           | Services that wish to support cross-tenant requests SHOULD provide this field. Services that provide notifications on tenant-scoped data MUST send this field.

### 15.7. Managing subscriptions programmatically
For per-user subscriptions, an API MUST be provided to create and manage subscriptions.
The API must support at least the operations described here.

#### 15.7.1. Creating subscriptions
A client creates a subscription by issuing a POST request against the subscriptions resource.
The subscription namespace is client-defined via the POST operation.

```
https://api.contoso.com/apiVersion/$subscriptions
```

The POST request contains a single subscription object to be created.
That subscription object has the following properties:

Property Name   | Required | Notes
--------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------
resource        | Yes      | Resource path to watch.
notificationUrl | Yes      | The target web hook URL.
clientState     | No       | Opaque string passed back to the client on all notifications. Callers may choose to use this to provide tagging mechanisms.

If the subscription was successfully created, the service MUST respond with the status code 201 CREATED and a body containing at least the following properties:

Property Name      | Required | Notes
------------------ | -------- | -------------------------------------------------------------------------------------------
id                 | Yes      | Unique ID of the new subscription that can be used later to update/delete the subscription.
expirationDateTime | No       | Uses existing Microsoft REST API Guidelines defined time formats.

Creation of subscriptions SHOULD be idempotent.
The combination of properties scoped to the auth token, provides a uniqueness constraint.

Below is an example request using a User + Application principal to subscribe to notifications from a file:

```http
POST https://api.contoso.com/files/v1.0/$subscriptions HTTP 1.1
Authorization: Bearer {UserPrincipalBearerToken}

{
  "resource": "http://api.service.com/v1.0/files/file1.txt",
  "notificationUrl": "https://contoso.com/myCallbacks",
  "clientState": "clientOriginatedOpaqueToken"
}
```

The service SHOULD respond to such a message with a response format minimally like this:

```json
{
  "id": "32b8cbd6174ab18b",
  "expirationDateTime": "2016-02-04T11:23Z"
}
```

Below is an example using an Application-Only principal where the application is watching all files to which it's authorized:

```http
POST https://api.contoso.com/files/v1.0/$subscriptions HTTP 1.1
Authorization: Bearer {ApplicationPrincipalBearerToken}

{
  "resource": "All.Files",
  "notificationUrl": "https://contoso.com/myCallbacks",
  "clientState": "clientOriginatedOpaqueToken"
}
```

The service SHOULD respond to such a message with a response format minimally like this:

```json
{
  "id": "8cbd6174abb391179",
  "expirationDateTime": "2016-02-04T11:23Z"
}
```

#### 15.7.2. Updating subscriptions
Services MAY support amending subscriptions.
 To update the properties of an existing subscription, clients use PATCH requests providing the ID and the properties that need to change.
Omitted properties will retain their values.
To delete a property, assign a value of JSON null to it.

As with creation, subscriptions are individually managed.

The following request changes the notification URL of an existing subscription:

```http
PATCH https://api.contoso.com/files/v1.0/$subscriptions/{id} HTTP 1.1
Authorization: Bearer {UserPrincipalBearerToken}

{
  "notificationUrl": "https://contoso.com/myNewCallback"
}
```

If the PATCH request contains a new _notificationUrl_, the server MUST perform validation on it as described above.
If the new URL fails to validate, the service MUST fail the PATCH request and leave the subscription in its previous state.

The service MUST return an empty body and `204 No Content` to indicate a successful patch.

The service MUST return an error body and status code if the patch failed.

The operation MUST succeed or fail atomically.

#### 15.7.3. Deleting subscriptions
Services MUST support deleting subscriptions.
Existing subscriptions can be deleted by making a DELETE request against the subscription resource:

```http
DELETE https://api.contoso.com/files/v1.0/$subscriptions/{id} HTTP 1.1
Authorization: Bearer {UserPrincipalBearerToken}
```

As with update, the service MUST return `204 No Content` for a successful delete, or an error body and status code to indicate failure.

#### 15.7.4. Enumerating subscriptions
To get a list of active subscriptions, clients issue a GET request against the subscriptions resource using a User + Application or Application-Only bearer token:

```http
GET https://api.contoso.com/files/v1.0/$subscriptions HTTP 1.1
Authorization: Bearer {UserPrincipalBearerToken}
```

The service MUST return a format as below using a User + Application principal bearer token:

```json
{
  "value": [
    {
      "id": "32b8cbd6174ab18b",
      "resource": " http://api.contoso.com/v1.0/files/file1.txt",
      "notificationUrl": "https://contoso.com/myCallbacks",
      "clientState": "clientOriginatedOpaqueToken",
      "expirationDateTime": "2016-02-04T11:23Z"
    }
  ]
}
```

An example that may be returned using Application-Only principal bearer token:

```json
{
  "value": [
    {
      "id": "6174ab18bfa22",
      "resource": "All.Files ",
      "notificationUrl": "https://contoso.com/myCallbacks",
      "clientState": "clientOriginatedOpaqueToken",
      "expirationDateTime": "2016-02-04T11:23Z"
    }
  ]
}
```

### 15.8. Security
All service URLs must be HTTPS (that is, all inbound calls MUST be HTTPS). Services that deal with Web Hooks MUST accept HTTPS.

We recommend that services that allow client defined Web Hook Callback URLs SHOULD NOT transmit data over HTTP.
This is because information can be inadvertently exposed via client, network, server logs and other mechanisms.

However, there are scenarios where the above recommendations cannot be followed due to client endpoint or software limitations.
Consequently, services MAY allow web hook URLs that are HTTP.

Furthermore, services that allow client defined HTTP web hooks callback URLs SHOULD be compliant with privacy policy specified by engineering leadership.
This will typically include recommending that clients prefer SSL connections and adhere to special precautions to ensure that logs and other service data collection are properly handled.

For example, services may not want to require developers to generate certificates to onboard.
Services might only enable this on test accounts.