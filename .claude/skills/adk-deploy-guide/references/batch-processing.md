# Batch & Event Processing

## The `run_agent()` Helper

For stateless batch processing, create a helper with concurrency control:

```python
import asyncio
import json
from pydantic import BaseModel
from typing import List, Any, Dict

class BQResponse(BaseModel):
    replies: List[str]

# Concurrency control
MAX_CONCURRENT = 10
semaphore = asyncio.Semaphore(MAX_CONCURRENT)

async def run_agent(prompt: str) -> str:
    """Run the agent with concurrency control.

    Uses Runner + InMemorySessionService for stateless batch processing.
    Each invocation creates a fresh session (no conversation history).
    """
    async with semaphore:
        try:
            from app.agent import root_agent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types as genai_types

            session_service = InMemorySessionService()
            await session_service.create_session(
                app_name="app", user_id="invoke_user", session_id="invoke_session"
            )
            runner = Runner(
                agent=root_agent, app_name="app", session_service=session_service
            )

            final_response = ""
            async for event in runner.run_async(
                user_id="invoke_user",
                session_id="invoke_session",
                new_message=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text=prompt)]
                ),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_response = event.content.parts[0].text
            return final_response
        except Exception as e:
            return json.dumps({"error": str(e)})
```

## Adding an /invoke Endpoint

To enable batch/event processing, add an `/invoke` endpoint that auto-detects input format:

```python
@app.post("/invoke")
async def invoke(request: Dict[str, Any]):
    """Universal endpoint: BigQuery, Pub/Sub, Eventarc, or direct HTTP."""

    # BigQuery Remote Function: {"calls": [[row1], [row2], ...]}
    if "calls" in request:
        results = await asyncio.gather(
            *[process_row(row) for row in request["calls"]]
        )
        return BQResponse(replies=results)

    # Pub/Sub Push: {"message": {"data": "base64...", "attributes": {...}}}
    if "message" in request:
        data = base64.b64decode(request["message"]["data"]).decode("utf-8")
        result = await run_agent(f"Process event: {data}")
        return {"status": "success", "result": result}

    # Eventarc: {"data": {...}, "type": "google.cloud.storage.object.v1.finalized"}
    if "type" in request and request["type"].startswith("google.cloud."):
        result = await run_agent(f"Process GCP event: {request['type']}")
        return {"status": "success", "result": result}

    # Direct HTTP: {"input": "your prompt"}
    if "input" in request or "prompt" in request:
        prompt = request.get("input") or request.get("prompt")
        result = await run_agent(prompt)
        return {"status": "success", "result": result}
```

## Local Testing

```bash
# Start local backend
make local-backend

# Test BigQuery batch format
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{"calls": [["test input 1"], ["test input 2"]]}'

# Test Pub/Sub format
DATA=$(echo -n '{"key": "value"}' | base64)
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d "{\"message\": {\"data\": \"$DATA\"}}"
```

## Integration Examples

**BigQuery Remote Function:**
```sql
-- Create connection (one-time setup)
CREATE EXTERNAL CONNECTION `project.region.bq_connection`
OPTIONS (cloud_resource_id="//cloudresourcemanager.googleapis.com/projects/PROJECT_ID");

-- Create remote function
CREATE FUNCTION dataset.analyze_customer(data STRING)
RETURNS STRING
REMOTE WITH CONNECTION `project.region.bq_connection`
OPTIONS (endpoint = 'https://your-service.run.app/invoke');

-- Process millions of rows
SELECT customer_id, dataset.analyze_customer(customer_data) AS analysis
FROM customers;
```

**Pub/Sub Push Subscription:**
```bash
gcloud pubsub subscriptions create my-subscription \
    --topic=my-topic \
    --push-endpoint=https://your-service.run.app/invoke
```

**Eventarc Trigger:**
```bash
gcloud eventarc triggers create storage-trigger \
    --destination-run-service=your-service \
    --destination-run-path=/invoke \
    --event-filters="type=google.cloud.storage.object.v1.finalized" \
    --event-filters="bucket=my-bucket"
```

## Production Considerations

**Rate Limiting & Retry:**
- Use semaphores to limit concurrent Gemini calls (avoid 429 errors)
- Implement exponential backoff for transient failures
- For BigQuery: Raise `TransientError` on 429s to trigger automatic retries

**Error Handling:**
- Return per-row errors as JSON objects, don't fail entire batch
- Log errors with trace IDs for debugging
- Monitor error rates via Cloud Logging/Monitoring

**Cost Control:**
- Set Cloud Run `--max-instances` to cap concurrent executions
- Monitor Gemini API usage and set budget alerts
- Test with small batches before running on production data

## Reference Implementation

See complete production example with chunking, error handling, and monitoring:
https://github.com/richardhe-fundamenta/practical-gcp-examples/blob/main/bq-remote-function-agent/customer-advisor/app/fast_api_app.py
