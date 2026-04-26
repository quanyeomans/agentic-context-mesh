## Operator Frameworks for Kubernetes
Currently, many frameworks exist to simplify the process of bootstrapping an operator/controller project and to write operators. This chapter describes some of them without any claim to comprehensiveness.

### CNCF Operator Framework

The *[Operator Framework](https://github.com/operator-framework)* is an open source toolkit to manage Kubernetes native applications, called Operators, in an effective, automated, and scalable way.

It aims at Operator Developers with an SDK to streamline Operator development with scaffolding tools (based on [kubebuilder](https://github.com/kubernetes-sigs/kubebuilder)), a test harness for unit tests and integration as well as functional tests and packaging / distribution mechanisms to publish version histories of Operators in conjunction with a user-configurable update graph. Supported project types are Golang, Helm and Ansible. Python and Java are currently in development.

It also caters for Kubernetes administrators that require a central point to install, configure and update Operators in a multi-tenant environment with potentially dozens of Operators installed. It covers the following aspects of Operator lifecycle:

- Continuous over-the-Air Updates and Catalogs of Operators a publishing mechanism and source of updates
- Dependency Model so Operator can have dependencies on cluster features or on each other
- Discoverability for less privileged tenants that usually cannot list CRDs or see Operators installed in separate namespaces
- Cluster Stability that avoid runtime conflicts of Operators on multi-tenant clusters while honoring the global nature of CRDs, and the subtleties of CRD versioning and CRD conversion
- Declarative UI controls that allows consoles to generate rich UI experiences for end users interacting with Operator services

Main advantages of the Operator Framework are:

- Simplified development: The Operator Framework simplifies the development of Kubernetes operators by providing a framework, tooling, and best practices for building operators.

- Reusability: The Operator Framework promotes the creation of reusable operators, which can be used across different applications and projects.

- Kubernetes-native: The Operator Framework is built on top of Kubernetes APIs and conventions, making it easier to develop operators that integrate well with the Kubernetes ecosystem.

- Robustness: The Operator Framework generates code that adheres to best practices for building Kubernetes operators, making it easier to build robust, production-grade applications.

- Community-driven: The Operator Framework has a large and active community that provides support, resources, and examples to help developers get started and solve problems.
  
Main limitations:
- Learning curve: Building Kubernetes operators with the Operator Framework can still be a complex task, especially for developers who are new to Kubernetes or the concept of operators.

- Limited flexibility: The Operator Framework is opinionated about how Kubernetes operators should be built, which can limit flexibility and customization options in certain cases.

- Performance overhead: Kubernetes operators built with the Operator Framework can add a performance overhead to the Kubernetes cluster, especially for large-scale or distributed applications.

- Maintenance: The Operator Framework requires ongoing maintenance and updates to ensure compatibility with new Kubernetes releases and changes to the operator's dependencies.

### Kopf

**[Kopf](https://github.com/nolar/kopf)** —**K**ubernetes **O**perator **P**ythonic **F**ramework— is a framework
to create Kubernetes operators faster and easier, just in a few lines of Python.
It takes away most of the low-level Kubernetes API communication hassle and
marshalls the Kubernetes resource changes to Python functions and back:

```python
import kopf

@kopf.on.create(kind='KopfExample')
def created(patch, spec, **_):
    patch.status['name'] = spec.get('name', 'world')

@kopf.on.event(kind='KopfExample', field='status.name', value=kopf.PRESENT)
def touched(memo, status, **_):
    memo.last_name = status['name']

@kopf.timer('KopfExample', interval=5, when=lambda memo, **_: 'last_name' in memo)
def greet_regularly(memo, **_):
    print(f"Hello, {memo['last_name']}!")
```

You should consider using this framework if you want or need to make ad-hoc
(here-and-now one-time non-generalizable) operators in Python 3.7+; especially if you want to bring your application domain directly to Kubernetes as custom
resources.
For more features, see the [documentation](https://kopf.readthedocs.io/en/stable/).

Main advantages of using kopf:
- Easy to use: Kopf is designed to be easy to use and understand, making it a great choice for developers who are new to Kubernetes or building operators.

- Python-based: As a Python-based framework, Kopf allows developers to leverage the vast Python ecosystem and libraries, making it easier to integrate with other tools and systems.

- Declarative approach: Kopf provides a declarative approach to building operators, which makes it easier to define the desired state of the system and handle updates and changes automatically.

- Lightweight and fast: Kopf is lightweight and has a low overhead, making it a good choice for building operators that need to be deployed in resource-constrained environments.

Main limitations:

- Python-specific: While Python is a popular language, some developers may prefer to use other languages to build Kubernetes operators.

- Limited adoption: Compared to other frameworks and tools, Kopf has a relatively small community and limited adoption, which can limit the availability of resources and support.

- Limited flexibility: Kopf is designed to be simple and easy to use, which can limit its flexibility and customization options for more complex or specialized use cases.

- Learning curve: While Kopf is designed to be easy to use, building Kubernetes operators still requires knowledge of Kubernetes concepts and best practices, which can be a challenge for new users.

### kubebuilder

The kubebuilder framework provides developers the possibilities to extend the Kubernetes API by using Custom Resource Definitions, and to create controllers that handle these custom resources.

The main entry point provided by the kubebuilder framework is a *Manager*. In the same way the native Kubernetes controllers are grouped into a single Kubernetes Controller Manager (`kube-controller-manager`), you will be able to create several controllers and make them managed by a single manager.

As Kubernetes API resources are attached to domains and arranged in Groups, Versions and Kinds, the Kubernetes custom resources you will define will be attached to your own domain, and arranged in your own groups, versions and kinds.

The first step when using kubebuilder is to create a project attached to your domain, that  will create the source code for building a single Manager.

After initiating your project with a specific domain, you can add APIs to your domain and make these APIs managed by the manager.

Adding a resource to the project will generate some sample code for you: a sample *Custom Resource Definition* that you will adapt to build your own custom resource, and a sample *Reconciler* that will implement the reconcile loop for your operator handling this resource.

The kubebuilder framework leverages the `controller-runtime` library, that provides the Manager and Reconciler concepts, among others.

The kubebuilder framework provides all the requisites for building the manager binary, the image of a container starting the manager, and the Kubernetes resources necessary for deploying this manager, including the `CustomResourceDefinition` resource defining your custom resource, a `Deployment` to deploy the manager, and RBAC rules for your operator to be able to access the Kubernetes API.

Main advantages of using kubebuilder are:

- Simplified development: Kubebuilder provides a framework and tooling to scaffold and automate much of the boilerplate code required for building Kubernetes controllers and API servers, allowing developers to focus on business logic.

- Kubernetes-native: Kubebuilder is built on top of Kubernetes APIs and conventions, making it easier to develop controllers and APIs that integrate well with the Kubernetes ecosystem.

- Reusability: Kubebuilder encourages the creation of reusable, composable controllers and APIs, which can be shared across different applications and projects.

- Robustness: Kubebuilder generates code that adheres to best practices for building Kubernetes controllers and APIs, making it easier to build robust, production-grade applications.

Main limitations:

- Learning curve: Kubebuilder has a significant learning curve, especially for developers who are new to Kubernetes or the Go programming language.

- Complexity: While Kubebuilder simplifies much of the development process, building Kubernetes controllers and APIs can still be a complex task, especially for large-scale or distributed applications.

- Limited flexibility: Kubebuilder is opinionated about how Kubernetes controllers and APIs should be built, which can limit flexibility in certain cases. For example, it may not be suitable for building highly customized or specialized controllers.

### Metacontroller - Lightweight Kubernetes Controllers as a Service

[Metacontroller](https://metacontroller.github.io/metacontroller/) is an operator, that makes it easy to write and deploy custom operators.

It introduces two CRD's itself (2021) :
* [Composite Controller](https://metacontroller.github.io/metacontroller/api/compositecontroller.html) - allowing to write operator triggered by CRD
* [Decorator Controller](https://metacontroller.github.io/metacontroller/api/decoratorcontroller.html) - allowing to write operator triggered by any kubernetes object (also managed by other operators)

Metacontrollers itself, configured by one of its CRD, will take care of observing cluster state and call controller, provided by user(user controller), to take actions.

User controller should, having given resources as input, compute the desired state of dependent objects.

This could also be called `lambda controller` pattern (more on this [here](https://metacontroller.github.io/metacontroller/concepts.html#lambda-controller)), as the output is calculated only considering input and the logic used by metacontroller could also reside at a Function-as-a-Service provider.

Main advantages of metacontroller :
* Only a function (called via webhook) without any boilerplate related to watching kubernetes resources needs to be provided
* Such a function can be written in any language, and exposed via http

Main limitations :
* Only certain patterns are possible to implement, mentioned above
* The current architecture relies on a single metacontroller in a cluster
* Metacontroller is not aware of any external state, it relies entirely on cluster state

Example metacontroller configuration, shown below, is used to add additional network exposure for `StatefulSet` without explicitly defining `Service` manifest.
```yaml
apiVersion: metacontroller.k8s.io/v1alpha1
kind: DecoratorController
metadata:
  name: service-per-pod
spec:
  resources:
  - apiVersion: apps/v1
    resource: statefulsets
    annotationSelector:
      matchExpressions:
      - {key: service, operator: Exists}
      - {key: port, operator: Exists}
  attachments:
  - apiVersion: v1
    resource: services
  hooks:
    sync:
      webhook:
        url: http://service-per-pod.metacontroller/sync-service-per-pod
        timeout: 10s

```
With above configuration :
* `metacontroller`, for every object matching `spec.resources` description (in this case - `apps/v1/statefulsets` with `service` and `port` annotations), will watch for any change in matching objects (create/update/delete) and invoke `hooks.sync` on each of those
* the `hooks.sync` can return objects which are described in `spec.attachments` (in this case - `v1/services`) which will be created/updated/deleted by `metacontroller`, according to `hook` response
  For example, if below `StatefulSet` will be deployed:
```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  annotations:
    service: "statefulset.kubernetes.io/pod-name"
    ports: "80:8080"
...
```
given `Service` object will be created by metacontroller:
```yaml
apiVersion: "v1"
kind: "Service"
spec:
  selector: "statefulset.kubernetes.io/pod-name"
  ports:
  - port: 80
    targetPort: 8080
```

The user defined endpoint (in this example - `http://service-per-pod.metacontroller/sync-service-per-pod`) only needs to care about the calculation of the `Service` and how it should look like for a given `StatefulSet`.

Additional examples and ideas that could be implemented using metacontroller, can be found at the [metacontroller-examples](https://metacontroller.github.io/metacontroller/examples.html) page !

For any question, please visit our slack channel ([#metacontroller](https://kubernetes.slack.com/archives/CA0SUPUDP)) or ask it on [github discussions](https://github.com/metacontroller/metacontroller/discussions/).

### Juju - Model-driven Operator Framework

Juju Operator Framework is an open-source tool that simplifies the deployment, management, and scaling of complex applications in cloud and container environments. Juju provides a powerful model-driven approach that allows developers to create reusable and composable "charms" to encapsulate application knowledge, configuration, and logic. These charms can be easily deployed and orchestrated by Juju "operators," which are automated agents that handle the lifecycle of an application. One of the significant advantages of Juju is its ability to abstract away the underlying infrastructure, making it easier to deploy and manage applications across multiple clouds and container environments. 

Below is an example of integrations between a web app and database.
```