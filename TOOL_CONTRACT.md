# Tool / Interface Contract

This is the frozen contract between the **agent** and the **mock environment**.
Both people build against THIS so you don't block each other. If you change a
signature, tell the other person immediately.

Base URL of mock env: `http://localhost:8000`

## Read-only tools (always allowed — no approval)

### get_active_alerts()
GET /alerts
-> `[{ "id", "service", "severity", "summary", "fired_at" }]`

### get_metrics(service: str)
GET /metrics?service={service}
-> `{ "service", "status", "version", "error_rate", "p99_latency_ms", "cpu", "memory", "replicas" }`

### get_logs(service: str, level: str = "ERROR", limit: int = 20)
GET /logs?service={service}&level={level}&limit={limit}
-> `[{ "ts", "level", "service", "message" }]`

### list_services()
GET /services
-> `[{ "service", "status", "version", "replicas" }]`

### get_recent_deploys(service: str, limit: int = 5)
GET /deploys?service={service}&limit={limit}
-> `[{ "version", "deployed_at", "deployed_by", "status" }]`

## Destructive tools (REQUIRES_APPROVAL — harness must pause before these)

### rollback_service(service: str, to_version: str)
POST /rollback  body: `{ "service", "to_version" }`
-> `{ "ok", "service", "from_version", "to_version", "message" }`

### restart_service(service: str)
POST /restart  body: `{ "service" }`
-> `{ "ok", "service", "message" }`

### scale_service(service: str, replicas: int)
POST /scale  body: `{ "service", "replicas" }`
-> `{ "ok", "service", "replicas", "message" }`

## Utility (demo only)
### POST /reset  -> resets scenario to the degraded state for a fresh demo.

## Approval contract
The agent code must treat this set as gated:
```python
REQUIRES_APPROVAL = {"rollback_service", "restart_service", "scale_service"}
```
When the model requests one of these, the harness PAUSES and surfaces to the UI:
```json
{
  "action": "rollback_service",
  "args": {"service": "checkout-service", "to_version": "v1.4.1"},
  "risk": "destructive",
  "reason": "<agent's stated justification>"
}
```
UI shows approve/reject. Only on approve does the tool actually execute.
