## Step 4: Update Dapr Pub/Sub Configuration for the Streamlit UI
We need to configure the Streamlit UI to subscribe to the `human-review` topic and publish to the `human-decision` topic. Update the `components/subscriptions.yaml` file to include subscriptions for the Streamlit UI.

Edit `components/subscriptions.yaml`:
```yaml
apiVersion: dapr.io/v1alpha1
kind: Subscription
metadata:
  name: analytics-subscription
spec:
  topic: messages
  route: /events
  pubsubname: pubsub
---
apiVersion: dapr.io/v1alpha1
kind: Subscription
metadata:
  name: review-ui-subscription
spec:
  topic: human-review
  route: /human-review
  pubsubname: pubsub
```

#### Explanation of Changes
- Added a new subscription for the `review-ui` service to listen to the `human-review` topic.
- The `route: /human-review` is used by Dapr to route events to the Streamlit app, but since we’re using the Dapr Python SDK’s `subscribe` method, this route is not directly used (it’s a placeholder for HTTP-based subscriptions).

---