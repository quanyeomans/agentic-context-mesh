## Foundation
Kubernetes and the success of other orchestrators has been due to their focus on the main capabilities of containers.
While companies began their journey to cloud native, working with more specific use cases (microservices, stateless applications) made more sense.
As Kubernetes and other container orchestrators grew their reputation and extensibility, requirements became more ambitious.
The desire to use the full lifecycle capabilities of an orchestrator was also transferred to highly distributed data stores.

Kubernetes primitives were not built to manage state by default.
Relying on Kubernetes primitives alone brings difficulty managing stateful application requirements such as replication, failover automation, backup/restore and upgrades (_which can occur based on events that are too specific_).

The Operator Pattern can be used to solve the problem of managing state.
By leveraging Kubernetes built-in capabilities such as self-healing, reconciliation and extending those along with application-specific complexities; it is possible to automate any application lifecycle, operations and turn it into a highly capable offering.

Operators are thought of as synonymous with Kubernetes.
However, the idea of an application whose management is entirely automated can be exported to other platforms.
The aim of this paper is to bring this concept to a higher level than Kubernetes itself.

### Operator Design Pattern
This section describes the pattern with high-level concepts.
The next section _Kubernetes Operator Definition_ will describe the implementations of the pattern in terms of Kubernetes objects and concepts.

The operator design pattern defines how to manage application and infrastructure resources using domain-specific knowledge and declarative state. The goal of the pattern is to reduce the amount of manual imperative work (how to backup, scale, upgrade...) which is required to keep an application in a healthy and well-maintained state, by capturing that domain specific knowledge in code and exposing it using a declarative API.

By using the operator pattern, the knowledge on how to adjust and maintain a resource is captured in code and often within a single service (also called a controller).

When using an operator design pattern the user should only be required to describe the desired state of the application and resources. The operator implementation should make the necessary changes in the world so it will be in the desired state. The operator will also monitor the real state continuously and take actions to keep it healthy and in the same state (preventing drifts).

A general diagram of an operator will have software that can read the desired spec and can create and manage the resources that were described.

![Operator Design Pattern](img/02_1_operator_pattern.png)

The Operator pattern consists of three components:

* The application or infrastructure that we want to manage.
* A domain specific language that enables the user to specify the desired state of the application in a declarative way.
* A controller that runs continuously:
    * Reads and is aware of the state.
    * Runs actions when operations state changes in an automated way.
    * Report the state of the application in a declarative way.

This design pattern will be applied on Kubernetes and its operators in the next sections.

### Operator Characteristics
The core purpose of any operator is to extend its orchestrator's underlying API with new domain knowledge. As an example, an orchestration platform within Kubernetes natively understands things like containers and layer 4 load balancers via the Pod and Service objects. An operator adds new capabilities for more complex systems and applications. For instance, a prometheus-operator introduces new object types _Prometheus_, extending Kubernetes with high-level support for deploying and running Prometheus servers.

The capabilities provided by an operator can be sorted into three overarching categories: dynamic configuration, operational automation and domain knowledge.

#### Dynamic Configuration
Since the early stages of software development, there have been two main ways to configure software: configuration files and environment variables. The cloud native world created newer processes, which are based on querying a well-known API at startup. Most existing software relies on a combination of both of these options. Kubernetes naturally provides many tools to enable custom configuration (such as ConfigMaps and Secrets). Since most Kubernetes resources are generic, they don’t understand any specifics for modifying a given application. In comparison, an operator can define new custom object types (custom resources) to better express the configuration of a particular application in a Kubernetes context.

Allowing for better validation and data structuring reduces the likelihood of small configuration errors and improves the ability of teams to self-serve. This removes the requirement for every team to house the understanding of either the underlying orchestrator or the target application as would be traditionally required. This can include things like progressive defaults, where a few high-level settings are used to populate a best-practices-driven configuration file or adaptive configuration such as adjusting resource usage to match available hardware or expected load based on cluster size.

#### Operational Automation
Along with custom resources, most operators include at least one custom controller. These controllers are daemons that run inside the orchestrator like any other but connect to the underlying API and provide automation of common or repetitive tasks. This is the same way that orchestrators (like Kubernetes) are implemented. You may have seen kube-controller-manager or cloud-controller-manager mentioned in your journey so far. However, as was demonstrated with configuration, operators can extend and enhance orchestrators with higher-level automation such as deploying clustered software, providing automated backups and restores, or dynamic scaling based on load.

By putting these common operational tasks into code, it can be ensured they will be repeatable, testable and upgradable in a standardized fashion. Keeping humans out of the loop on frequent tasks also ensures that steps won’t be missed or excluded and that different pieces of the task can’t drift out of sync with each other. As before, this allows for improved team autonomy by reducing the hours spent on boring-but-important upkeep tasks like application backups.

#### Domain Knowledge
Similar to operational automation, it can be written into an operator to encode specialized domain knowledge about particular software or processes. A common example of this is application upgrades. While a simple stateless application might need nothing more than a Deployment’s rolling upgrade; databases and other stateful applications often require very specific steps in sequence to safely perform upgrades. The operator can handle this autonomously as it knows your current and requested versions and can run specialized upgrade code when needed. More generally, this can apply to anything a pre-cloud-native environment would use manual checklists for (effectively using the operator as an executable runbook).
Another common way to take advantage of automated domain knowledge is error remediation. For example, the Kubernetes built-in remediation behaviors mostly start and end with “restart container until it works” which is a powerful solution but often not the best or fastest solution.
An operator can monitor its application and react to errors with specific behavior to resolve the error or escalate the issue if it can’t be automatically resolved. This can reduce MTTR (mean time to recovery) and also reduce operator fatigue from recurring issues.

### Operator Components in Kubernetes

*“An operator is a Kubernetes controller that understands 2 domains: Kubernetes and something else. By combining knowledge of both domains, it can automate tasks that usually require a human operator that understands both domains”*
(Jimmy Zelinskie, https://github.com/kubeflow/tf-operator/issues/300#issuecomment-357527937)

![Operator Big Picture](img/02_2_operator.png)
Operators enable the extension of the Kubernetes API with operational knowledge.
This is achieved by combining Kubernetes controllers and watched objects that describe the desired state. The controller can watch one or more objects and the objects can be either Kubernetes primitives such as Deployments, Services or things that reside outside of the cluster such as Virtual Machines or Databases.

The desired state refers hereby to any resource that is defined in code and which the operator is configured to manage. Subsequently, the current state references the deployed instance of those resources.

The controller will constantly compare the desired state with the current state using the reconciliation loop which ensures that the watched objects get transitioned to the desired state in a defined way.

The desired state is encapsulated in one or more Kubernetes custom resources and the controller contains the operational knowledge which is needed to get the objects (such as deployments, services) to their target state.

#### Kubernetes Controllers
A Kubernetes Controller takes care of routine tasks to ensure the desired state expressed by a particular resource type matches the current state (current state,https://engineering.bitnami.com/articles/a-deep-dive-into-kubernetes-controllers.html, https://fntlnz.wtf/post/what-i-learnt-about-kubernetes-controller/). For instance, the Deployment controller takes care that the desired amount of pod replicas is running and a new pod spins up, when one pod is deleted or fails.

Technically, there is no difference between a typical controller and an operator. Often the difference referred to is the operational knowledge that is included in the operator. Therefore, a controller is the implementation, and the operator is the pattern of using custom controllers with CRDs and automation is what is looking to be achieved with this. As a result, a controller which spins up a pod when a custom resource is created, and the pod gets destroyed afterwards can be described as a simple controller. If the controller has operational knowledge like how to upgrade or remediate from errors, it is an operator.

#### Custom Resources and Custom Resource Definitions
Custom resources are used to store and retrieve structured data in Kubernetes as an extension of the default Kubernetes API  (https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/).
In the case of an operator, a custom resource contains the desired state of the resource (e.g. application) but does not contain the implementation logic. Such information could be the version information of application components, but also enabled features of an application or information where backups of the application could be part of this. A custom resource definition (CRD) defines how such an object looks like, for example, which fields exist and how the CRD is named. Such a CRD can be scaffolded using tools (as the operator SDK) or be written by hand.


The following example illustrates, how such an custom resource instance definition could look like:

```yaml
apiVersion: example-app.appdelivery.cncf.io/v1alpha1
kind: ExampleApp
metadata:
  name: appdelivery-example-app
spec:
  appVersion: 0.0.1
  features:
    exampleFeature1: true
    exampleFeature2: false
  backup:
    enabled: true
    storageType: “s3”
    host: “my-backup.example.com”
    bucketName: “example-backup”
  status:
    currentVersion: 0.0.1
    url: https://myloadbalancer/exampleapp/
    authSecretName: appdelivery-example-app-auth
    backup:
      lastBackupTime: 12:00
```

This example represents a custom resource with the name “appdelivery-example-app” of the kind “ExampleApp”.

The “spec” section is where the user can declare the desired state. This example declares that appVersion 0.0.1 should be deployed with one feature enabled and another disabled. Furthermore, backups of this application should be made, and a s3 bucket should be used.

The “status” section is where the operator can communicate useful information back to the user. In this example, the status shows the current deployed version. If it is different from the “appVersion” in the spec, then the user can expect that the operator is working to deploy the version requested in the spec. Other common information in the status section includes how to connect to an application and the health of the application.

#### Control Loop
The control (reconciliation) loop in a Kubernetes controller ensures that the state that the user declares using a CRD matches the state of the application, but also that the transition between the states works as intended. One common use-case could be the migration of database schemes when upgrading an application. The control loop can be triggered on specific events, as a change on the crd, but also time-based, like for backing up data at a defined time.

### Operator Capabilities
An operator is able to assist with operating an application or other managed components by solving many different tasks. When talking about operators, the first and most well known capability is the ability of installing and upgrading stateful applications. However, an operator could manage the full lifecycle of an application without requiring manual input on installation/upgrades.

The following sections should give an overview about capabilities an operator could have and what a user can expect if an operator implements these capabilities.

#### Install an Application / Take Ownership of an Application
An operator should be able to provision and set up all the required resources, so no manual work would be required during the installation. An operator must check and verify that resources that were provisioned are working as expected, and ready to be used.

An operator should also be able to recognize resources that were provisioned before the installation process, and only take ownership of them for later use. In this case, the ownership process should be seamless and not cause downtime. The ownership process purpose is to enable easy migration of resources to the operator.

An Operator should report the version of the resources and their health status during the process.

#### Upgrade an Application
An operator should be able to upgrade the version of the application/resources. The operator should know how to update the required dependencies and execute custom commands such as running a database migration.

An operator should monitor the update and rollback if there was a problem during the process.

An operator should report the version of the resources and their health status during the process. If there was an error, the version reported should be the version that is currently being used.

#### Backup

This capability is for operators that manage data and ensure that the operator is able to create consistent backups. This backup should be done in a way that the user of the operator can be certain that the previous version can be restored if data is lost or compromised. Furthermore, the status information provided should give insights about when the backup last ran and where it is located.

![Example Backup Process](plantuml/backup-sequence.png)

The above illustration shows how such a process could look like. At first, the backup gets triggered either by a human or another trigger (e.g. time-trigger). The operator instructs its watched resource (application) to set up a consistent state (like a consistent snapshot). Afterwards, the data of the application gets backed up to external storage using appropriate tools. This could either be a one-step process (backup directly to external storage) or in multiple steps, like writing to a persistent volume at first and to the external storage afterwards. The external storage might be an NFS/CIFS share (or any other network file system) on-premises, but also an object store/bucket on a cloud provider infrastructure. Whether the backup failed or succeeded, the state (of the backup) including the backed-up application version and the location of the backup might be written to the status section of the custom resource.

#### Recovery from backup

The recovery capability of an operator might assist a user in restoring the application state from a successful backup. Therefore, the application state (application version and data) should be restored.

There might be many ways to achieve this. One possible way could be that the current application state also gets backed up (including configuration), so the user only has to create a custom resource for the application and point to the backup. The operator would read the configuration, restore the application version and restore the data. Another possible solution might be that the user only backed up the data and might have to specify the application version used. Nevertheless, in both ways, the operator ensures that the application is up and running after using the data from the backup specified.

#### Auto-Remediation
The auto-remediation capability of an operator should ensure that it is able to restore the application from a more complex failed state, which might not be handled or detected by mechanisms such as health checks (live and readiness probes). Therefore, the operator needs to have a deep understanding of the application. This can be achieved by metrics that might indicate application failures or errors, but also by dealing with kubernetes mechanisms like health checks.

Some examples might be:
* Rolling back to the last known configuration if a defined amount of pod starts is unsuccessful after a version change.
  In some points a restart of the application might be a short-term solution which also could be done by the operator.
* It could also be imaginable that an operator informs another operator of a dependent service that a backend system is not reachable at the moment (to take remediation actions).

In any situation, this capability enables the operator to take actions to keep the system up and running.


#### Monitoring/Metrics - Observability
While the managed application should provide the telemetry data for itself, the operator could provide metrics about its own behavior and only provide a high level overview about the applications state (as it would be possible for auto-remediation). Furthermore, typical telemetry data provided by the operator could be the count of remediation actions, duration of backups, but also information about the last errors or operational tasks which were handled.


#### Scaling
Scaling is part of the day-2 operations that an operator can manage in order to keep the application / resources functional. The scaling capability doesn’t require the scaling to be automated, but only that the operator will know how to change the resources in terms of horizontal and vertical scaling.

An operator should be able to increase or decrease any resource that it owns, such as CPU, memory, disk size and number of instances.

Ideally the scaling action will be without downtime. Scaling action ends when all the resources are in consistent state and ready to be used, so an operator should verify the state of all the resources and report it.

#### Auto-Scaling
An operator should be able to perform the scaling capability based on metrics that it collects constantly and according to thresholds. An operator should be able to automatically increase and decrease every resource that it’s own.

An operator should respect basic scaling configuration of min and max.


#### Auto-Configuration tuning
This capability should empower the operator to manage the configuration of the managed application. As an example, the operator could adopt memory settings of an application according to the operation environment (e.g. Kubernetes) or the change of DNS names. Furthermore, the operator should be able to handle configuration changes in a seamless way, e.g. if a configuration change requires a restart, this should be triggered.

These capabilities should be transparent to the users. The user should have the possibility to override such auto-configuration mechanisms if they want to do so. Furthermore, automatic reconfigurations should be well-documented in a way that the user could comprehend what is happening on the infrastructure.

#### Uninstalling / Disconnect
When deleting the declarative requested state (in most cases a custom resource), an operator should allow two behaviors:
- Uninstalling: An operator should be able to completely remove or delete every managed resource.
- Disconnecting: An operator should stop managing the provisioned resources.

Both processes should be applied to every resource that the operator directly provisioned.
An operator should report any failure in the process in a declarative way (using the [status field](https://kubernetes.io/docs/concepts/overview/working-with-objects/kubernetes-objects/#object-spec-and-status) for example).