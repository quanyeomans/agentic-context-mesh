## Operator Lifecycle Management
An operator is an application, this section will describe considerations regarding the lifecycle of the operator itself.

### Upgrading the Operator
While upgrading the operator, special care should be taken in regards to the managed resources. During an operator upgrade, the managed resources should be kept in the same state and healthy.

### Upgrading the Declarative State
The declarative state is the API of the operator, and it may need to be upgraded. The usage of CRD versions indicates the stability of the CRD and the operator - [read more about versioning a CRD](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/)

### Managing Relations of CRDs

As the number of Operators & CRDs adds up, its complexity of management also increases. For example, how to manage the conflicts between Operators, like two ingress-related functions? How to manage the dependencies and/or correlation of data flow between CRDs, like DB cluster and DB backup CRDs?

To resolve this problem, we would need a concrete model to manage Operators & CRDs and
a new mechanism to oversee them with a policy-based engine.
Community efforts like [KubeVela](https://kubevela.io/) and [Crossplane](https://crossplane.io/)
have been trying to solve this problem by providing solutions to compose CRDs.
KubeVela also provides management of data dependencies between custom resources.