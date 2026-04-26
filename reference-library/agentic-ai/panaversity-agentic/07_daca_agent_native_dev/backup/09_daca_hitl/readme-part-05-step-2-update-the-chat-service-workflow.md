## Step 2: Update the Chat Service Workflow
We’ll modify the Chat Service’s workflow in `chat_service/main.py` to include HITL integration. The workflow will check for sensitive keywords, emit a "HumanReviewRequired" event, and pause until a "HumanDecisionMade" event is received.

### Step 2.1: Update `chat_service/main.py`
Edit `chat_service/main.py` to add HITL logic to the workflow. Below is the updated code with HITL integration:

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