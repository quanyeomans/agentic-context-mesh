## Conclusion
In this tutorial, we introduced Human-in-the-Loop integration into our DACA microservices. We modified the Chat Service workflow to emit a "HumanReviewRequired" event for sensitive messages, built a Streamlit UI for human review, and integrated the feedback loop with Dapr by publishing a "HumanDecisionMade" event. We also updated the `review_ui` project to use **uv** for dependency management, ensuring consistency with the rest of the DACA application. The updated application runs seamlessly with Docker Compose, and the HITL workflow ensures human oversight for critical decisions. We’re now ready to explore production deployment in the next tutorial!

---

### Final `chat_service/main.py`
```python
import logging
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import openai
from dapr.ext.workflow import WorkflowRuntime, DaprWorkflowContext, WorkflowActivityContext
from dapr.clients import DaprClient
from dapr.clients.grpc._state import StateOptions, StateItem, Concurrency, Consistency
import uuid
from datetime import datetime, timezone
import json
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ChatService")

app = FastAPI()

class ChatRequest(BaseModel):
    user_id: str
    text: str
    metadata: Dict[str, Any]
    tags: list[str] = []

class ChatResponse(BaseModel):
    user_id: str
    reply: str
    metadata: Dict[str, Any]

SENSITIVE_KEYWORDS = ["urgent", "help", "emergency"]

dapr_client = DaprClient()

def get_openai_client():
    with DaprClient() as d:
        secret = d.get_secret(store_name="secretstore", key="openai-api-key").secret
        openai.api_key = secret["openai-api-key"]
    return openai

def fetch_message_count(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> int:
    user_id = input["user_id"]
    with DaprClient() as d:
        resp = d.invoke_method(
            app_id="analytics-service",
            method_name=f"analytics/{user_id}",
            http_verb="GET"
        )
        data = resp.json()
        return data.get("message_count", 0)

def get_conversation_history(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> str:
    user_id = input["user_id"]
    with DaprClient() as d:
        actor_type = "UserSessionActor"
        actor_id = user_id
        history = d.invoke_actor(actor_type, actor_id, "GetHistory", "").data.decode('utf-8')
        return history if history else "No previous conversation."

def generate_reply(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> str:
    user_id = input["user_id"]
    message = input["message"]
    message_count = input["message_count"]
    history = input["history"]
    
    prompt = f"User {user_id} has sent {message_count} messages. History: {history}\nMessage: {message}\nReply as a helpful assistant:"
    client = get_openai_client()
    response = client.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=150
    )
    return response.choices[0].text.strip()

def store_conversation(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> None:
    user_id = input["user_id"]
    message = input["message"]
    reply = input["reply"]
    with DaprClient() as d:
        actor_type = "UserSessionActor"
        actor_id = user_id
        conversation = f"User: {message}\nAssistant: {reply}"
        d.invoke_actor(actor_type, actor_id, "AddMessage", conversation)

def publish_message_sent_event(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> None:
    user_id = input["user_id"]
    with DaprClient() as d:
        d.publish_event(
            pubsub_name="pubsub",
            topic_name="messages",
            data=json.dumps({"user_id": user_id, "event_type": "MessageSent"}),
            data_content_type="application/json"
        )

def check_sensitive_content(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> Dict[str, Any]:
    message = input["message"]
    is_sensitive = any(keyword in message.lower() for keyword in SENSITIVE_KEYWORDS)
    return {"is_sensitive": is_sensitive, "message": message}

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

def wait_for_human_decision(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> Dict[str, Any]:
    instance_id = input["instance_id"]
    with DaprClient() as d:
        while True:
            state_key = f"human-decision-{instance_id}"
            state = d.get_state(
                store_name="statestore",
                key=state_key,
                state_options=StateOptions(concurrency=Concurrency.first_write, consistency=Consistency.strong)
            )
            if state.data:
                decision_data = json.loads(state.data.decode('utf-8'))
                d.delete_state(store_name="statestore", key=state_key)
                return decision_data
            time.sleep(1)

def chat_workflow(context: DaprWorkflowContext, input: Dict[str, Any]) -> Dict[str, Any]:
    user_id = input["user_id"]
    message = input["message"]
    instance_id = context.instance_id

    logger.info(f"Starting workflow for user {user_id} with message: {message}")

    message_count = yield context.call_activity(fetch_message_count, input={"user_id": user_id})
    history = yield context.call_activity(get_conversation_history, input={"user_id": user_id})
    proposed_reply = yield context.call_activity(generate_reply, input={
        "user_id": user_id,
        "message": message,
        "message_count": message_count,
        "history": history
    })
    sensitive_result = yield context.call_activity(check_sensitive_content, input={"message": message})
    is_sensitive = sensitive_result["is_sensitive"]

    if is_sensitive:
        logger.info(f"Sensitive content detected in message: {message}. Requesting human review.")
        yield context.call_activity(request_human_review, input={
            "user_id": user_id,
            "message": message,
            "proposed_reply": proposed_reply,
            "instance_id": instance_id
        })
        decision = yield context.call_activity(wait_for_human_decision, input={"instance_id": instance_id})
        approved = decision["approved"]

        if not approved:
            logger.info(f"Human rejected the response for user {user_id}. Aborting workflow.")
            return {
                "user_id": user_id,
                "reply": "Message rejected by human reviewer.",
                "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "session_id": str(uuid.uuid4())}
            }

    yield context.call_activity(store_conversation, input={
        "user_id": user_id,
        "message": message,
        "reply": proposed_reply
    })
    yield context.call_activity(publish_message_sent_event, input={"user_id": user_id})

    logger.info(f"Completed workflow for user {user_id}")
    return {
        "user_id": user_id,
        "reply": proposed_reply,
        "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "session_id": str(uuid.uuid4())}
    }

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

### Final `review_ui/app.py`
```python
import streamlit as st
from dapr.clients import DaprClient
import json
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ReviewUI")

st.title("Human Review Interface")

if "review_requests" not in st.session_state:
    st.session_state.review_requests = []

def publish_human_decision(instance_id: str, approved: bool):
    with DaprClient() as d:
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
        state_key = f"human-decision-{instance_id}"
        d.save_state(
            store_name="statestore",
            key=state_key,
            value=json.dumps({"instance_id": instance_id, "approved": approved})
        )
    logger.info(f"Published HumanDecisionMade event for instance_id {instance_id}: approved={approved}")

def subscribe_to_reviews():
    with DaprClient() as d:
        while True:
            try:
                response = d.subscribe(
                    pubsub_name="pubsub",
                    topic="human-review"
                )
                for event in response:
                    data = json.loads(event.data.decode('utf-8'))
                    if data["event_type"] == "HumanReviewRequired":
                        st.session_state.review_requests.append(data)
                        st.experimental_rerun()
            except Exception as e:
                logger.error(f"Error in subscription: {e}")
                time.sleep(5)

import threading
threading.Thread(target=subscribe_to_reviews, daemon=True).start()

if st.session_state.review_requests:
    for request in st.session_state.review_requests:
        st.subheader(f"Review Request for User: {request['user_id']}")
        st.write(f"**Message:** {request['message']}")
        st.write(f"**Proposed Reply:** {request['proposed_reply']}")
        st.write(f"**Instance ID:** {request['instance_id']}")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Approve", key=f"approve-{request['instance_id']}"):
                publish_human_decision(request["instance_id"], True)
                st.session_state.review_requests.remove(request)
                st.experimental_rerun()
        with col2:
            if st.button("Reject", key=f"reject-{request['instance_id']}"):
                publish_human_decision(request["instance_id"], False)
                st.session_state.review_requests.remove(request)
                st.experimental_rerun()
else:
    st.write("No review requests at the moment.")
```

### Final `review_ui/pyproject.toml`
```toml
[project]
name = "review-ui"
version = "0.1.0"
dependencies = [
    "streamlit==1.32.0",
    "dapr==1.12.0",
]

[tool.uv]
lock = "uv.lock"
```

### Final `review_ui/Dockerfile`
```dockerfile
FROM python:3.9-slim

RUN pip install uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . .

EXPOSE 8501

CMD ["uv", "run", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

---

This tutorial uses **uv** for dependency management in the `review_ui` project, ensuring consistency with the `chat_service` and `analytics_service` projects.