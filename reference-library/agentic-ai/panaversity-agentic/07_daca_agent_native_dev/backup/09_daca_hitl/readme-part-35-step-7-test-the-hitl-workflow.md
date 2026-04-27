## Step 7: Test the HITL Workflow
We’ll test the HITL integration by sending a message that triggers a human review, using the Streamlit UI to approve or reject the response, and verifying the workflow completes accordingly.

### Step 7.1: Initialize State for Testing
Initialize message counts for `alice` and `bob` (same as previous tutorials):
- For `alice`:
  ```bash
  curl -X POST http://localhost:8001/analytics/alice/initialize -H "Content-Type: application/json" -d '{"message_count": 5}'
  ```
  Output:
  ```json
  {"status": "success", "user_id": "alice", "message_count": 5}
  ```
- For `bob`:
  ```bash
  curl -X POST http://localhost:8001/analytics/bob/initialize -H "Content-Type: application/json" -d '{"message_count": 3}'
  ```
  Output:
  ```json
  {"status": "success", "user_id": "bob", "message_count": 3}
  ```

### Step 7.2: Send a Message Requiring Human Review
Send a message to the Chat Service that contains a sensitive keyword (e.g., "urgent"):
```bash
curl -X POST http://localhost:8000/chat/ -H "Content-Type: application/json" -d '{"user_id": "bob", "text": "This is an urgent request!", "metadata": {"timestamp": "2025-04-06T12:00:00Z", "session_id": "123e4567-e89b-12d3-a456-426614174001"}, "tags": ["greeting"]}'
```

The request will hang because the workflow is waiting for human input.

#### Check the Chat Service Logs
```bash
docker-compose logs chat-service-app
```
Output:
```
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,123 - ChatService - INFO - Received chat request for user bob: This is an urgent request!
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,124 - ChatService - INFO - Scheduling workflow with instance_id: chat-bob-1744064460
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,125 - ChatService - INFO - Starting workflow for user bob with message: This is an urgent request!
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,130 - ChatService - INFO - Sensitive content detected in message: This is an urgent request!. Requesting human review.
```

### Step 7.3: Review the Message in the Streamlit UI
Open the Streamlit UI at `http://localhost:8501`. You should see a review request:
- **User:** bob
- **Message:** This is an urgent request!
- **Proposed Reply:** (e.g., "Hi Bob! You've sent 3 messages so far. No previous conversation. I understand your request is urgent—how can I assist you?")
- **Instance ID:** chat-bob-1744064460

#### Approve the Response
Click the "Approve" button.

#### Check the Streamlit Logs
```bash
docker-compose logs review-ui-app
```
Output:
```
fastapi-daca-tutorial_review-ui-app_1  | 2025-04-06 04:01:00,150 - ReviewUI - INFO - Published HumanDecisionMade event for instance_id chat-bob-1744064460: approved=True
```

#### Check the Chat Service Response
The `curl` request should now complete with a response:
```json
{
  "user_id": "bob",
  "reply": "Hi Bob! You've sent 3 messages so far. No previous conversation. I understand your request is urgent—how can I assist you?",
  "metadata": {
    "timestamp": "2025-04-06T04:01:00Z",
    "session_id": "some-uuid"
  }
}
```

#### Check the Analytics Service Logs
```bash
docker-compose logs analytics-service-app
```
Output:
```
fastapi-daca-tutorial_analytics-service-app_1  | 2025-04-06 04:01:00,162 - AnalyticsService - INFO - Received event: {'user_id': 'bob', 'event_type': 'MessageSent'}
fastapi-daca-tutorial_analytics-service-app_1  | 2025-04-06 04:01:00,163 - AnalyticsService - INFO - Incrementing message count for user bob
...
fastapi-daca-tutorial_analytics-service-app_1  | 2025-04-06 04:01:00,169 - AnalyticsService - INFO - Processed MessageSent event for user bob
```

### Step 7.4: Test Rejection
Send another message requiring review:
```bash
curl -X POST http://localhost:8000/chat/ -H "Content-Type: application/json" -d '{"user_id": "alice", "text": "I need help immediately!", "metadata": {"timestamp": "2025-04-06T12:00:00Z", "session_id": "123e4567-e89b-12d3-a456-426614174002"}, "tags": ["support"]}'
```

In the Streamlit UI (`http://localhost:8501`), click the "Reject" button for this request.

#### Check the Chat Service Response
The `curl` request should complete with:
```json
{
  "user_id": "alice",
  "reply": "Message rejected by human reviewer.",
  "metadata": {
    "timestamp": "2025-04-06T04:01:00Z",
    "session_id": "some-uuid"
  }
}
```

#### Check the Chat Service Logs
```bash
docker-compose logs chat-service-app
```
Output:
```
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,170 - ChatService - INFO - Received chat request for user alice: I need help immediately!
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,171 - ChatService - INFO - Scheduling workflow with instance_id: chat-alice-1744064460
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,172 - ChatService - INFO - Starting workflow for user alice with message: I need help immediately!
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,175 - ChatService - INFO - Sensitive content detected in message: I need help immediately!. Requesting human review.
fastapi-daca-tutorial_chat-service-app_1  | 2025-04-06 04:01:00,180 - ChatService - INFO - Human rejected the response for user alice. Aborting workflow.
```

#### Verify Message Count
Since the message was rejected, the Analytics Service should not increment the message count for `alice`. Check the count:
- Visit `http://localhost:8001/docs` and test `/analytics/alice`:
  - Expected: `{"message_count": 5}` (unchanged).

---