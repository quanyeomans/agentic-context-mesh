# Activities for the workflow
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