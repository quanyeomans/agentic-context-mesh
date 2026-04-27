## 5. Taxonomy
As part of onboarding to Microsoft REST API Guidelines, services MUST comply with the taxonomy defined below.

### 5.1. Errors
Errors, or more specifically Service Errors, are defined as a client passing invalid data to the service and the service _correctly_  rejecting that data.
Examples include invalid credentials, incorrect parameters, unknown version IDs, or similar.
These are generally "4xx" HTTP error codes and are the result of a client passing incorrect or invalid data.

Errors do _not_ contribute to overall API availability.

### 5.2. Faults
Faults, or more specifically Service Faults, are defined as the service failing to correctly return in response to a valid client request.
These are generally "5xx" HTTP error codes.

Faults _do_ contribute to the overall API availability.

Calls that fail due to rate limiting or quota failures MUST NOT count as faults.
Calls that fail as the result of a service fast-failing requests (often for its own protection) do count as faults.

### 5.3. Latency
Latency is defined as how long a particular API call takes to complete, measured as closely to the client as possible.
This metric applies to both synchronous and asynchronous APIs in the same way.
For long running calls, the latency is measured on the initial request and measures how long that call (not the overall operation) takes to complete.

### 5.4. Time to complete
Services that expose long operations MUST track "Time to Complete" metrics around those operations.

### 5.5. Long running API faults
For a Long Running API, it's possible for both the initial request which begins the operation and the request which retrieves the results to technically work (each passing back a 200) but for the underlying operation to have failed.
Long Running faults MUST roll up as faults into the overall Availability metrics.