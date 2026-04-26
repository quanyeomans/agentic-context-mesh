# New activity to request human review
def request_human_review(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> None:
    user_id = input["user_id"]
    message = input["message"]
    proposed_reply = input["proposed_reply"]
    instance_id = input["instance_id"]
    with DaprClient() as d:
        d.publish_event(
            pubsub_name="pubsub",
            topic_name="human-review",
            data=json.dumps({
                "user_id": user_id,
                "message": message,
                "proposed_reply": proposed_reply,
                "instance_id": instance_id,
                "event_type": "HumanReviewRequired"
            }),
            data_content_type="application/json"
        )