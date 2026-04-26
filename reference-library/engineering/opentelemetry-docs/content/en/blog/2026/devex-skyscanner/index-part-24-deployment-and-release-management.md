## Deployment and release management

Skyscanner uses the OpenTelemetry Collector Contrib distribution, having adopted
it because it included everything they needed. The team learned during the
interview that Contrib isn't recommended for production use, and plans to
explore building custom collector images with only the components they need.

Skyscanner updates collectors approximately every six months, though they'll
upgrade more frequently if tracking specific features or critical fixes. They
follow RSS feeds and CNCF Slack channels to stay informed about releases.

Their rollout strategy uses progressive promotion across cluster tiers: Dev
clusters, then three Alpha production clusters, followed by eight Beta
production clusters, and finally the remaining 13 production clusters. Using
Argo CD for deployment, changes are promoted via pull requests between tiers.

> "We've definitely messed stuff up in the development testing clusters and then
> gone and fixed them before promoting further," Neil said.

This gradual approach has caught configuration issues before they reach
production. While they don't yet have automated testing and rollback
capabilities for their OpenTelemetry Collector deployments, these improvements
are on the horizon.