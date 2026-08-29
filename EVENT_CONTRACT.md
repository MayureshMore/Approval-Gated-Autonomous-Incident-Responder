# Event Contract (the seam between Agent and Interface)

The agent (Person A) emits events; the dashboard (Person B) renders them. Both
build to THIS. Don't change a field without telling the other person.

Transport: the agent POSTs each event to `POST {AGENT_BUS_URL}/events`.
The dashboard polls `GET /events`. (File mirror `ui/events.json` also works.)

## Event kinds (all have `kind`; `t` = unix timestamp)
```
run_started        { scenario: str }
tool_call          { tool: str, args: object }
tool_result        { tool: str, result: object }
awaiting_approval  { action: str, args: object, reason: str, risk: "destructive" }
approval_decision  { action: str, approved: bool, by: "human"|"auto" }
agent_message      { text: str }
run_finished       { }
```

## Metrics rule
The dashboard reads service health from any `tool_result` whose `result` has
`service` + `status` (or a `p99_latency_ms` field). So metric tools must return the
shape in TOOL_CONTRACT.md. Don't rename those fields.

## Approval handshake
1. Agent emits `awaiting_approval` and then BLOCKS, polling `GET /decision?action=<name>`.
2. Dashboard shows the card; on click it POSTs `/decision {action, approved}`.
3. Agent's poll returns `{pending:false, approved:bool}` and it proceeds or aborts.

## Bus endpoints (Person B owns these; Person A calls them)
```
POST /events            push one event
GET  /events            full event log (dashboard polls)
POST /decision          { action, approved }   (dashboard buttons)
GET  /decision?action=  { pending } | { pending:false, approved }
POST /reset             clear events + decisions (between demo runs)
GET  /                  serves ui/dashboard.html
```

---

## Addendum — additive kinds from the agent (Person A, 2026-08-29)

The seven kinds above are unchanged and every required field is still emitted;
`tests/test_contracts.py` fails the build if that ever stops being true. The
agent additionally emits the kinds below. **The dashboard may ignore any of them
without breaking** — but two are worth rendering.

```
subagent_started   { subagent: "metrics"|"logs"|"deploys", task: str }
subagent_finished  { subagent: str, findings: object }
sandbox_exec       { code: str, payload_keys: [str] }
error              { message: str }
```

- **`sandbox_exec` — please render this.** Showing the code the agent wrote, in a
  box labelled "executed in sandbox", is the single strongest visual we have for
  the harness track.
- **`subagent_*`** — three parallel lanes that fill in as they finish reads well
  and is the subagent evidence for judging.

Optional fields added to existing kinds (all safe to ignore):

- every event: `run_id`
- `awaiting_approval`, `approval_decision`: `request_id`
- `approval_decision`: `reason`
- `run_started`: `provider`, `approval_mode`, `tools`
- `run_finished`: `steps`, `destructive_executed`, `error`

### One request for the bus
Have `GET /decision` echo back the `request_id` it was passed. The agent already
sends it and tolerates its absence, so nothing breaks today — it just closes a
stale-approval edge case if a single run ever gates two actions.
