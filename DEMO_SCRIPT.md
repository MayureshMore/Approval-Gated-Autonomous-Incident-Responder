# Demo script (rehearse twice, record once as fallback)

Target: 90 seconds.

**Start it with `make demo`.** It brings up both servers, waits until they
actually answer, resets the scenario so the run is deterministic, starts the
agent, and cleans everything up on Ctrl-C. Dashboard: http://localhost:8500/

`make demo-live` is the same run on the TrueFoundry gateway. Reset by hand
between takes if you are not using the launcher:
`curl -X POST localhost:8000/reset && curl -X POST localhost:8500/reset`

**Restart the servers after any code change** — uvicorn does not reload, and a
stale server will happily show you the old behaviour.

## The narration
"This is an on-call AI incident responder. A critical alert just fired on
checkout-service.

[start the run — `make demo`]

Three subagents sweep metrics, logs and deploys in parallel — watch them fill in.

Watch it work: it pulls the metrics — 34% error rate, 4-second latency — reads the
error logs, and checks recent deploys. It found that version 1.4.2 shipped 12
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

[run `make runs`  — or `.venv/bin/python run_agent.py --list-runs`]

"Here's the checkpoint — step 6, still waiting on that same rollback. Now I
resume it —"

[run `make demo-resume`]

(`make demo-resume` passes no `--provider` on purpose — the checkpoint decides,
so a resumed gateway run stays on the gateway. It also skips the scenario reset,
which would otherwise wipe the timeline this beat exists to show.)

"— and it picks up exactly where it left off. No re-investigation, no second
sandbox run — same diagnosis as before. And it asks again, because killing
the process is not the same as answering yes."

[click Approve — same as before, checkout-service recovers]

"Kill it mid-incident, and it still won't let a 'no' become a 'yes' by
accident. That's the harness earning trust, not just running fast."

## What to emphasize per track
- **Harness/DGX — read this twice, one of the judges is a TrueFoundry FDE.**
  NEVER say "the harness runs the loop, we didn't rebuild any of it." That is
  false and it is checkable in one `grep`. We use the TrueFoundry **AI Gateway**
  (an OpenAI-compatible model router): every model call is routed, authenticated
  and traced through it, tagged with `X-TFY-METADATA` so a run maps to its own
  cost and latency, and a preflight validates the model id against the gateway's
  catalogue before the run starts. The harness layer — sandboxed execution, the
  approval gate, subagents, session survival — we built.
  Say it like this: *"Every model call routes through the TrueFoundry gateway,
  traced per run. The harness layer on top — sandbox, approval gate, subagents,
  session survival — we implemented to a frozen contract, and each one is backed
  by tests you can run right now."* Then show `pytest`.
  **Do not claim the GitHub revert PR is live.** It is written and tested but
  dry-run, and it goes through the `gh` CLI, not MCP. If asked, say exactly
  that — a judge can read `integrations/github_ops.py` in thirty seconds.
  **We do not use Bright Data at all.** Do not enter that track.
- **UI/iPad:** "A stranger can drive this — it shows what the agent is doing, what
  it's waiting on, and asks before the irreversible step, right in the interface."
- **Qodo:** "Every PR went through Qodo. It found five real bugs on one PR and
  two on another — including an approval-gate bypass — and we fixed all of them
  before merge."

## Failure insurance
- Have the recording ready. If the live run wedges, play it and keep narrating.
- `--provider sim` needs no network and no API key, so a dead venue wifi does
  not stop the demo. That is the default for `make demo`.
- If everything else fails, `AUTO_APPROVE=1 .venv/bin/python fallback_agent.py`
  still runs the original single-file loop.
- The mock env is deterministic + `/reset`-able, so the demo is repeatable.
