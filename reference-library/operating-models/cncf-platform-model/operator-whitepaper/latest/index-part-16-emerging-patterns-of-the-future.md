## Emerging Patterns of the Future

As the popularity of Operators increases, there are new usages and patterns that are challenging the status-quo of best practices and design principles.

### Operator Lifecycle Management

With increasing Operator complexity and versioned, distributed controllers; there has been a need for the management and transparency of Operators and their resources. This pattern aids in the reuse of Operators through discoverability, minimal dependencies and declarative UI controls[1].

In addition to this, as Operators become increasingly designed to reconcile with certain characteristics toward an anticipated end-state, maintaining the life cycle within the cluster through proper management enables iterations, experimentation and testing of new behaviors.

### Policy-Aware Operators

Many Operators have a static set of role based authorizations within a cluster to reconcile resources.
There is ongoing activity to provide operators more dynamic access, based on the behavior they are required to exhibit for reconciling a resource. This might mean a temporary elevation to create a resource directly, or to request that a custom resource definition is loaded into the Kubernetes API server.

There is precedent for Operators[2] to allow for privileged creation of resources on the behalf of the Operators; extending to new patterns and operating models[3]. Future potential of this pattern would also allow for a policy-engine to control Operator authorization.

### Operator data modelling

As Kubernetes adoption continues to grow, the role of Kubernetes operators is also likely to evolve. In the future, we can expect operators to become more intelligent, automated, and integrated with other Kubernetes tools and services. One area where we may see significant developments is in the use of data modelling to support more complex and dynamic applications running on Kubernetes. By using data modelling to define and manage the state of the system, operators can more easily manage and automate complex workflows, applications, and services. This approach can also improve scalability, flexibility, and maintainability, as well as enable more advanced features like auto-scaling and self-healing. In the future, we can expect Kubernetes operators to play a critical role in enabling advanced data modelling and management capabilities for Kubernetes-based applications and services.

### References

\[1\] https://olm.operatorframework.io/

\[2\] https://github.com/cloud-ark/kubeplus

\[3\] https://oam.dev/