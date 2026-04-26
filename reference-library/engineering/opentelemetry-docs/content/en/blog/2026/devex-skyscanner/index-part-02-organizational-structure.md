## Organizational structure

The Hubble team, consisting of six platform engineers, manages most of
Skyscanner's collectors. As part of the wider platform engineering organization,
they handle the compute platform that runs Skyscanner's primarily Java-based
microservices architecture.

Service teams themselves remain abstracted from the deployment and telemetry
collection infrastructure. For Java services, teams inherit a base Docker image
containing the pre-configured OpenTelemetry Java agent. For Python and Node.js
services, the platform team provides wrapper libraries that set sensible
defaults based on environment and resource attributes. These approaches minimize
boilerplate setup and give service teams observability out of the box without
requiring deep OpenTelemetry knowledge.