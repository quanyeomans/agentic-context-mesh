## Step 3: Create the Streamlit UI for Human Review
We’ll create a new service called `review-ui` that uses **Streamlit** to provide a simple interface for human reviewers to approve or reject messages. The UI will:
- Subscribe to the "HumanReviewRequired" topic to receive review requests.
- Display the message and proposed reply to the human.
- Allow the human to approve or reject the response.
- Publish a "HumanDecisionMade" event with the decision.
- Store the decision in the Dapr state store for the Chat Service workflow to retrieve.

### Step 3.1: Set Up the Streamlit UI
Create a new directory for the Streamlit UI:
```bash
mkdir review_ui
cd review_ui
```

Create a `app.py` file for the Streamlit app:
```bash
touch app.py
```

Edit `review_ui/app.py`:
```python
import streamlit as st
from dapr.clients import DaprClient
import json
import time
import logging