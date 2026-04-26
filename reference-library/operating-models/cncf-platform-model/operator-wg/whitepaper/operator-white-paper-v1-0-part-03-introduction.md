## Introduction

This whitepaper defines operators in a wider context than Kubernetes. It describes their characteristics and components, gives an overview of common patterns currently in use and explains how they differ from Kubernetes controllers.

Additionally, it provides a deep dive into the capabilities of Kubernetes controllers, including backup, recovery and automatic configuration tuning. Further insights into frameworks currently in use, lifecycle management, security risks and use cases are provided.

This paper includes best practices including observability, security and technical implementation.

It closes with related work, highlights the additional value they can bring beyond this whitepaper and the next steps for operators.

### The Goal of this Document
The goal of this document is to provide a definition of operators for cloud native applications in the context of Kubernetes and other container orchestrators.


### Target Audience / Minimum Level of Experience
This document is intended for application developers, Kubernetes cluster operators and service providers (internal or external) - who want to learn about operators and the problems they can solve. It can also help teams already looking at operators to learn when and where to use them to best effect. It presumes basic Kubernetes knowledge such as familiarity with Pods and Deployments.