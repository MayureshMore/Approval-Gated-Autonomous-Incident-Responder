# Mock Prod Stack

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Endpoints (see ../TOOL_CONTRACT.md for full schemas):
- GET  /alerts, /services, /metrics?service=, /logs?service=&level=&limit=, /deploys?service=
- POST /rollback, /restart, /scale   (destructive — agent must gate these)
- POST /reset                        (reset scenario for a fresh demo)

Demo scenario: checkout-service degraded by bad deploy v1.4.2.
Correct fix = rollback to v1.4.1. Restart alone will NOT fix it.
