# Start the workflow runtime
workflow_runtime = WorkflowRuntime()
workflow_runtime.register_workflow(chat_workflow)
workflow_runtime.register_activity(fetch_message_count)
workflow_runtime.register_activity(get_conversation_history)
workflow_runtime.register_activity(generate_reply)
workflow_runtime.register_activity(store_conversation)
workflow_runtime.register_activity(publish_message_sent_event)
workflow_runtime.register_activity(check_sensitive_content)
workflow_runtime.register_activity(request_human_review)
workflow_runtime.register_activity(wait_for_human_decision)

@app.on_event("startup")
async def start_workflow_runtime():
    await workflow_runtime.start()

@app.on_event("shutdown")
async def stop_workflow_runtime():
    await workflow_runtime.shutdown()

@app.post("/chat/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    logger.info(f"Received chat request for user {request.user_id}: {request.text}")
    instance_id = f"chat-{request.user_id}-{int(time.time())}"
    logger.info(f"Scheduling workflow with instance_id: {instance_id}")

    with DaprClient() as d:
        d.start_workflow(
            workflow_component="dapr",
            workflow_name="chat_workflow",
            input={
                "user_id": request.user_id,
                "message": request.text
            },
            instance_id=instance_id
        )

        # Wait for the workflow to complete
        while True:
            state = d.get_workflow(instance_id=instance_id, workflow_component="dapr")
            if state.runtime_status == "COMPLETED":
                result = state.result
                return ChatResponse(**result)
            elif state.runtime_status == "FAILED":
                raise HTTPException(status_code=500, detail="Workflow failed")
            time.sleep(0.5)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
```

#### Explanation of Changes
- **Sensitive Keywords**: Added a list of `SENSITIVE_KEYWORDS` (e.g., "urgent", "help", "emergency") to flag messages for human review.
- **New Activities**:
  - `check_sensitive_content`: Checks if the message contains sensitive keywords.
  - `request_human_review`: Publishes a "HumanReviewRequired" event to the `human-review` topic with the message, proposed reply, and workflow instance ID.
  - `wait_for_human_decision`: Polls the Dapr state store for a human decision (stored under `human-decision-`). In a production system, you’d use Dapr’s pub/sub subscription API or external event triggers to avoid polling.
- **Updated Workflow (`chat_workflow`)**:
  - After generating a reply, the workflow checks for sensitive content.
  - If the message is sensitive, it emits a "HumanReviewRequired" event and pauses, waiting for a human decision.
  - If the human rejects the response, the workflow returns a rejection message. If approved, it proceeds with storing the conversation and publishing the "MessageSent" event.
- **Polling for Human Decision**: For simplicity, we poll the state store for the human decision. In a real-world application, you’d use Dapr’s pub/sub subscription to listen for the "HumanDecisionMade" event directly in the workflow.

### Step 2.2: Update Dapr Pub/Sub Configuration
We need to configure the `human-review` topic for the "HumanReviewRequired" event. Since the Chat Service publishes to this topic, we don’t need a subscription (the Streamlit UI will subscribe to it). However, we’ll ensure the topic is available by updating the `components/subscriptions.yaml` file to include a subscription for the Streamlit UI (added later).

For now, the Chat Service only needs to publish to the `human-review` topic, which is supported by the existing `pubsub` component (Redis).

---