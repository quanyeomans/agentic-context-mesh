## What You’ll Learn
- What Human-in-the-Loop (HITL) integration is and why it’s important for agentic systems.
- How to modify the Chat Service workflow to emit a "HumanReviewRequired" event and pause for human input.
- Building a simple Streamlit UI for human review (approve/reject decisions).
- Integrating the feedback loop with Dapr by publishing a "HumanDecisionMade" event.
- Running the updated application with Docker Compose, including the new Streamlit UI service.