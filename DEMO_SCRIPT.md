# Demo script (rehearse twice, record once as fallback)

Target: 90 seconds. Reset before every run:
`curl -X POST localhost:8000/reset && curl -X POST localhost:8500/reset`

## The narration
"This is an on-call AI incident responder. A critical alert just fired on
checkout-service.

[start the run — `AGENT_BUS_URL=http://localhost:8500 python run_agent.py --provider sim --subagents`]

Three subagents sweep metrics, logs and deploys in parallel — watch them fill in.

Watch it work: it pulls the metrics — 34% error rate, 4-second latency — reads the
error logs, and checks recent deploys. It found that version 1.4.2 shipped 15
minutes before the alert. Then — this part matters — it doesn't guess. It writes a
diagnostic and runs it **in a sandbox** to score the correlation: 0.76, and the bad
version is named right there in the error logs.

So it proposes rolling back to 1.4.1. And notice — it **stopped and asked me first.**
It will not touch production until a human approves. This is the whole point: the
speed of automation, but nothing irreversible happens without a person in the loop.

[click Approve]

Rollback executes… and checkout-service is back to healthy — error rate 0.4%.

The agent did 30 minutes of 2 AM investigation in seconds, and asked before the one
action that could make things worse."

## Bonus beat — session survival (Layer 4, cheap and verified)
Do this instead of the approve-immediately ending, if there's time — it's a
stronger closer. Verified working end-to-end (real process kill, not staged):

"But watch what happens if I lose the agent entirely — laptop dies, tab
closes, doesn't matter."

[reach the **Waiting for approval** card, then kill the agent process —
Ctrl-C or `kill`]

"The card stays right there. Nothing was lost — the bus and the dashboard
don't even know the agent is gone."

[run `python run_agent.py --list-runs`]

"Here's the checkpoint — step 6, still waiting on that same rollback. Now I
resume it —"

[run `python run_agent.py --provider sim --resume last`]

"— and it picks up exactly where it left off. No re-investigation, no second
sandbox run — same diagnosis as before. And it asks again, because killing
the process is not the same as answering yes."

[click Approve — same as before, checkout-service recovers]

"Kill it mid-incident, and it still won't let a 'no' become a 'yes' by
accident. That's the harness earning trust, not just running fast."

## What to emphasize per track
- **Harness/DGX:** say the words — sandboxed code execution, human-approval gate,
  subagents, session survival, and (Layer 2) a REAL GitHub revert PR via MCP over
  OAuth. "The harness runs the loop, the sandbox, and the approval pause — we
  didn't rebuild any of it."
- **UI/iPad:** "A stranger can drive this — it shows what the agent is doing, what
  it's waiting on, and asks before the irreversible step, right in the interface."
- **Qodo:** "Every PR went through Qodo; we fixed its findings before merge."

## Failure insurance
- Have the recording ready. If the live run wedges, play it and keep narrating.
- If OpenAI/harness is down, `AUTO_APPROVE=1 python fallback_agent.py` still runs.
- The mock env is deterministic + `/reset`-able, so the demo is repeatable.
