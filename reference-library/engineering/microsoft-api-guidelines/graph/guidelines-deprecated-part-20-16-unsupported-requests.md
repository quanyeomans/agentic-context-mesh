## 16. Unsupported requests
RESTful API clients MAY request functionality that is currently unsupported.
RESTful APIs MUST respond to valid but unsupported requests consistent with this section.

### 16.1. Essential guidance
RESTful APIs will often choose to limit functionality that can be performed by clients.
For instance, auditing systems allow records to be created but not modified or deleted.
Similarly, some APIs will expose collections but require or otherwise limit filtering and ordering criteria, or MAY not support client-driven pagination.

### 16.2. Feature allow list
If a service does not support any of the below API features, then an error response MUST be provided if the feature is requested by a caller.
The features are:
- Key Addressing in a collection, such as: `https://api.contoso.com/v1.0/people/user1@contoso.com`
- Filtering a collection by a property value, such as: `https://api.contoso.com/v1.0/people?$filter=name eq 'david'`
- Filtering a collection by range, such as: `http://api.contoso.com/v1.0/people?$filter=hireDate ge 2014-01-01 and hireDate le 2014-12-31`
- Client-driven pagination via $top and $skip, such as: `http://api.contoso.com/v1.0/people?$top=5&$skip=2`
- Sorting by $orderBy, such as: `https://api.contoso.com/v1.0/people?$orderBy=name desc`
- Providing $delta tokens, such as: `https://api.contoso.com/v1.0/people?$delta`

#### 16.2.1. Error response
Services MUST provide an error response if a caller requests an unsupported feature found in the feature allow list.
The error response MUST be an HTTP status code from the 4xx series, indicating that the request cannot be fulfilled.
Unless a more specific error status is appropriate for the given request, services SHOULD return "400 Bad Request" and an error payload conforming to the error response guidance provided in the Microsoft REST API Guidelines.
Services SHOULD include enough detail in the response message for a developer to determine exactly what portion of the request is not supported.

Example:

```http
GET https://api.contoso.com/v1.0/people?$orderBy=name HTTP/1.1
Accept: application/json
```

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "error": {
    "code": "ErrorUnsupportedOrderBy",
    "message": "Ordering by name is not supported."
  }
}
```