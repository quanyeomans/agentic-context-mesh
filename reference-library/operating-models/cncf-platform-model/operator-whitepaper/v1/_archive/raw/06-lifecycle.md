---
title: "Operator Lifecycle Management"
source: CNCF TAG App Delivery (Platform Engineering)
source_url: https://github.com/cncf/tag-app-delivery
licence: Apache-2.0
domain: operating-models
subdomain: cncf-platform-model
date_added: 2026-04-25
---

**Status**: WIP | **Maintainer** : N/A | 

## Operator Lifecycle Management
An operator is an application, this section will describe considerations regarding the lifecycle of the operator itself. 

### Upgrading the Operator
While upgrading the operator, special care should be taken in regards to the managed resources. During an operator upgrade, the managed resources should be kept in the same state and healthy. 

### Upgrading the Declarative State
the declarative state is the API of the operator, and it may need to be upgraded. The usage of CRD versions indicates the stability of the CRD and the operator - [read more about versioning a CRD](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/)
