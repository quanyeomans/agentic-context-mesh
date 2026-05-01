# Subscribe to HumanReviewRequired events
def subscribe_to_reviews():
    with DaprClient() as d:
        while True:
            try:
                # Subscribe to the human-review topic
                response = d.subscribe(
                    pubsub_name="pubsub",
                    topic="human-review"
                )
                for event in response:
                    data = json.loads(event.data.decode('utf-8'))
                    if data["event_type"] == "HumanReviewRequired":
                        st.session_state.review_requests.append(data)
                        st.experimental_rerun()  # Refresh the UI to display the new request
            except Exception as e:
                logger.error(f"Error in subscription: {e}")
                time.sleep(5)  # Retry after a delay