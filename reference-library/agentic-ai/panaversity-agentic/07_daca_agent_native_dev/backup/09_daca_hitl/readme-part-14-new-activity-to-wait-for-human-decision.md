# New activity to wait for human decision
def wait_for_human_decision(ctx: WorkflowActivityContext, input: Dict[str, Any]) -> Dict[str, Any]:
    instance_id = input["instance_id"]
    with DaprClient() as d:
        # Subscribe to the human-decision topic to wait for the decision
        while True:
            # In a real-world scenario, use Dapr's pub/sub subscription API or external event triggers
            # For simplicity, we'll poll the state store for the decision
            state_key = f"human-decision-{instance_id}"
            state = d.get_state(
                store_name="statestore",
                key=state_key,
                state_options=StateOptions(concurrency=Concurrency.first_write, consistency=Consistency.strong)
            )
            if state.data:
                decision_data = json.loads(state.data.decode('utf-8'))
                # Clean up the state
                d.delete_state(store_name="statestore", key=state_key)
                return decision_data
            time.sleep(1)  # Poll every second