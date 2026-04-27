# Function to publish human decision
def publish_human_decision(instance_id: str, approved: bool):
    with DaprClient() as d:
        # Publish the HumanDecisionMade event
        d.publish_event(
            pubsub_name="pubsub",
            topic_name="human-decision",
            data=json.dumps({
                "instance_id": instance_id,
                "approved": approved,
                "event_type": "HumanDecisionMade"
            }),
            data_content_type="application/json"
        )
        # Store the decision in the state store for the workflow to retrieve
        state_key = f"human-decision-{instance_id}"
        d.save_state(
            store_name="statestore",
            key=state_key,
            value=json.dumps({"instance_id": instance_id, "approved": approved})
        )
    logger.info(f"Published HumanDecisionMade event for instance_id {instance_id}: approved={approved}")