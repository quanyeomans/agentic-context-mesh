## Emerging Patterns of the Future

As the popularity of Operators increases, there are new usages and patterns that are challenging the status-quo of best practices and design principles.

### Operator Lifecycle Management

With increasing Operator complexity and versioned, distributed controllers; there has been a need for the management and transparency of Operators and their resources. This pattern aids in the reuse of Operators through discoverability, minimal dependencies and declarative UI controls[1].

In addition to this, as Operators become increasingly designed to reconcile with certain characteristics toward an anticipated end-state, maintaining the life cycle within the cluster through proper management enables iterations, experimentation and testing of new behaviors.

### Policy-Aware Operators

Many Operators have a static set of role based authorizations within a cluster to reconcile resources.
There is ongoing activity to provide operators more dynamic access, based on the behavior they are required to exhibit for reconciling a resource. This might mean a temporary elevation to create a resource directly, or to request that a custom resource definition is loaded into the Kubernetes API server.

There is precedent for Operators[2] to allow for privileged creation of resources on the behalf of the Operators; extending to new patterns and operating models[3]. Future potential of this pattern would also allow for a policy-engine to control Operator authorization.

### References

\[1\] https://olm.operatorframework.io/

\[2\] https://github.com/cloud-ark/kubeplus

\[3\] https://oam.dev/