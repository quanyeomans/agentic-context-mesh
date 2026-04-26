## Step 9: Next Steps
You’ve successfully integrated Human-in-the-Loop into the DACA application! The Chat Service now pauses for human review when a message contains sensitive keywords, and the Streamlit UI allows a human to approve or reject the agent’s response. In the next tutorial, we’ll explore deploying this application to a production environment, such as a Kubernetes cluster, using Dapr’s Kubernetes integration.

### Optional Exercises
1. Enhance the Streamlit UI to include a history of reviewed messages and their decisions.
2. Add more sophisticated sensitive content detection (e.g., using a machine learning model to flag messages).
3. Replace the polling mechanism in `wait_for_human_decision` with a proper Dapr pub/sub subscription to listen for the "HumanDecisionMade" event.

---