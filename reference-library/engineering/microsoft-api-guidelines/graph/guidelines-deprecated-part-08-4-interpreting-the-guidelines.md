## 4. Interpreting the guidelines
### 4.1. Application of the guidelines
These guidelines are applicable to any REST API exposed publicly by Microsoft or any partner service.
Private or internal APIs SHOULD also try to follow these guidelines because internal services tend to eventually be exposed publicly.
 Consistency is valuable to not only external customers but also internal service consumers, and these guidelines offer best practices useful for any service.

There are legitimate reasons for exemption from these guidelines.
Obviously, a REST service that implements or must interoperate with some externally defined REST API must be compatible with that API and not necessarily these guidelines.
Some services MAY also have special performance needs that require a different format, such as a binary protocol.

### 4.2. Guidelines for existing services and versioning of services
We do not recommend making a breaking change to a service that predates these guidelines simply for the sake of compliance.
The service SHOULD try to become compliant at the next version release when compatibility is being broken anyway.
When a service adds a new API, that API SHOULD be consistent with the other APIs of the same version.
So if a service was written against version 1.0 of the guidelines, new APIs added incrementally to the service SHOULD also follow version 1.0. The service can then upgrade to align with the latest version of the guidelines at the service's next major release.

### 4.3. Requirements language
The keywords "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119][rfc-2119].

### 4.4. License

This work is licensed under the Creative Commons Attribution 4.0 International License.
To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/ or send a letter to Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.