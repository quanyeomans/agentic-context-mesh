## Successful Patterns

Over time, lots of best practices for writing operators have been published by various sources. Following, some of these sources are mentioned and parts of them described based on a scenario.

Scenario: A microservice application ("The PodTato Head", https://github.com/cncf/podtato-head) should be entirely managed via operators (even if another deployment mechanism would make more sense). This application consists of 4 services and 1 database which can be illustrated as follows:

![Sample Application](./img/08_1_sample.png)

Best practices should be applied to this application deployment.

### Management of a single type of application

The features an operator provides, should be specific to a single application. Applied to our example, this means that there should be 5 operators which will manage one component (podtato-server, arm-service, foot-service, hat-service and the database) at a time. This provides a good separation of concerns for all of them (based on https://cloud.google.com/blog/products/containers-kubernetes/best-practices-for-building-kubernetes-operators-and-stateful-apps).

### Operator of Operators

With a growing count of Operators typically used within the lifecycle of application workload deployment and management, there are opportunities for new interplay of resources and meta behaviors across a group of Operators. Whether the goal is to reduce the cognitive burden of managing multiple asynchronous Operators performing resource changes - or to ensure a level of continuity between release versions; the *Operator of Operators* architecture is being applied in some use cases within the industry. This paradigm typically utilizes a *Meta* Operator to create multiple resources that are in turn asynchronously created and then updated in the meta resource. It enables a single custom resource definition to express a desired state outcome and for the requirements to be partitioned and asynchronously acted upon.

![distributed](./img/09_1_distributedops.png)


Coordinating the setup and lifecycle of the whole stack can remain complex. An Operator controlling a metadata resource can help shield the user from this complexity by coordinating the various parts of the stack and exposes a CRD representing the whole stack. If this is the case, the *Meta* operator should delegate the work to the other Operators for the more specific parts.

The controllers that own these sub-components of stacks can appear in two ways:

- An operator distribution package could consist of multiple separate controllers, each handling a sub-component of the stack plus a main controller ( Responsible for the end-user facing CRD, representing the stack as a whole). Deploying such a multi-controller operator as a single package would result in all controllers running at once (one `Pod` each), but only the end-user facing API/CRD is actually exposed and documented for public consumption. When that happens, the controller responsible for this API delegates several duties to the other controllers, that are part of it's packaged using "internal" CRDs. This is useful when the whole "stack" is owned and developed by the same group of operator authors and the "subordinate" controllers don't make sense as a standalone project. To an end-user this set of controllers still appears as a single Operator. The main benefit here is separation of concerns within an operator project.

![Stack-Operator](./img/08_2_umbrella.png)

Technically, there would be a custom resource definition for the whole stack managed by an operator. This operator creates a custom resource for each of the components of the stack which are again managed by operators and managing the underlying resources.


- The second pattern depicted above, describes higher-level workload Operators. These depend on other general-purpose operator projects to deploy sub-components of a stack. An example would be an Operator, which depends on `cert-manager`, the `prometheus operator` and a `postgresql` operator to deploy its workload with rotating certificates, monitoring and a SQL database. In this case the higher-level workload operator should not try to ship and install `cert-manager` etc at runtime. This is because the operator author then signs up for shipping and maintaining the particular versions of these dependencies as well as dealing with the general problem area of CRD lifecycle management.

	*Instead a package management solution should be employed that supports dependency resolution at install time, so that installing the other required operators is delegated to a package manager in the background and not as part of the higher level operator startup code.*

	This is beneficial for operators that depend on other Operators, which are useful on their own and might even be shared with multiple other operators on the cluster. [OLM](https://github.com/operator-framework/operator-lifecycle-manager), part of the Operator Framework Project, is such a package manager.


### One CRD per Controller
Every CRD managed by an operator should be implemented in a single controller. This makes code a bit more readable and should help with separation of concerns.

### Where to publish and find Operators
There are services like operatorhub.io and artifacthub.io which help end-users to find operators including instructions on how they can be installed. These services often include information about current security issues and the sources of operators. Additionally, information about the capabilities of operators is given.

### Further reading
There are lots of more best practices like:
* An operator shouldn't install other operators
* Operators shouldn't make assumptions about the namespaces they are deployed in, but also
* Use an SDK for writing operators

and many other best practices might be found on the internet. More of them could be found on following sources:
* https://github.com/operator-framework/community-operators/blob/master/docs/best-practices.md
* https://cloud.google.com/blog/products/containers-kubernetes/best-practices-for-building-kubernetes-operators-and-stateful-apps