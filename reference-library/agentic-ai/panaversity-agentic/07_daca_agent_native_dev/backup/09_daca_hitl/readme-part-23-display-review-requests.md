# Display review requests
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

#### Explanation of the Streamlit App
- **Streamlit UI**:
  - Displays a title and a list of review requests.
  - Each request shows the user ID, message, proposed reply, and instance ID.
  - Provides "Approve" and "Reject" buttons for each request.
- **Subscription**:
  - Subscribes to the `human-review` topic to receive "HumanReviewRequired" events.
  - Adds new requests to `st.session_state.review_requests` and refreshes the UI.
- **Human Decision**:
  - When the human clicks "Approve" or "Reject", the app publishes a "HumanDecisionMade" event to the `human-decision` topic.
  - It also stores the decision in the Dapr state store under `human-decision-` for the Chat Service workflow to retrieve.
  - Removes the request from the UI after a decision is made.

### Step 3.2: Create a `pyproject.toml` for the Streamlit App
Instead of `requirements.txt`, we’ll use `pyproject.toml` and `uv` for dependency management. Create `review_ui/pyproject.toml`:
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

#### Explanation of `pyproject.toml`
- `[project]`: Defines the project metadata.
  - `name`: The project name (`review-ui`).
  - `version`: The project version.
  - `dependencies`: Lists the dependencies (`streamlit` and `dapr`).
- `[tool.uv]`: Configures `uv` to manage dependencies and create a `uv.lock` file.

### Step 3.3: Generate the `uv.lock` File
Navigate to the `review_ui` directory and generate the `uv.lock` file:
```bash
cd review_ui
uv sync
```

This will create a `uv.lock` file in the `review_ui` directory, locking the exact versions of the dependencies.

### Step 3.4: Create a `Dockerfile` for the Streamlit App
Create `review_ui/Dockerfile`:
```dockerfile