# ...
requires:
  database:
    interface: charm-db
    limit: 1
provides:
  website:
    interface: http
    optional: true
```

Main advantages of Juju:
- Abstraction: Juju provides a layer of abstraction that can help simplify the deployment and management of complex applications on Kubernetes. Juju can abstract away the complexity of Kubernetes APIs and allow developers to focus on the application logic.

- Integration: In Juju, an integration is a connection between applications, or between different units of the same application (the latter are also known as ‘peer relations’). These relations between applications are defined in the charm, and Juju handles the integration between the applications. They allow you to pass data between applications and trigger actions through events.

- Cloud-agnostic: Juju is cloud-agnostic and can deploy applications to various cloud providers and container platforms. This allows developers to deploy and manage their applications on any cloud or container platform with ease.

- Model-driven approach: Juju is based on a model-driven approach that makes it easy to automate and manage the lifecycle of an application, including scaling, upgrading, and monitoring.

Main limitations:
- The framework has a learning curve, and creating effective charms can require significant development effort, making it more suitable for enterprise use cases than smaller projects.