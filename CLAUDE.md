# CLAUDE.md — Incident Responder Agent

## What we are building (one sentence)
An AI agent that investigates a production alert using **read-only** tools in a
sandbox, forms a diagnosis, proposes a remediation, and **pauses for human
approval before any destructive action** — then executes on approval.

Hackathon: Agent Harness Hackathon (TrueFoundry). Deadline **18:00 today**. Repo MUST stay open source.

## Winning thesis (read this)
The DGX/Harness track is judged on *"the harness doing the work, not sitting
underneath a thin wrapper,"* and the harness's headline feature is *"any MCP
server, including ones behind OAuth."* The event's bar for an agent is literally
*"it opens the pull request, queries the database, runs the script, sends the message."*

Therefore: mock TELEMETRY is fine, but the agent's ACTIONS must become REAL to
place at the top. We keep the mock env for metrics/logs and make remediation real
via MCP-over-OAuth. This is the difference between a toy and a contender.

## Build in layers — each layer is a complete, demoable submission
**Never start a layer until the previous one demos cleanly. Mock is always the fallback.**

- **Layer 0 (by 13:00) — MUST EXIST:** fully mock loop. alert → investigate
  (metrics/logs/deploys) → diagnose → propose rollback → **approval pause** → execute → recovered.
- **Layer 1 — Sandbox code exec (DGX #2):** agent WRITES a diagnostic snippet and
  runs it in the sandbox (`diagnostics.py`) instead of only calling canned tools.
  Self-contained, no OAuth. Do this first — biggest credibility jump for least risk.
- **Layer 2 — Real GitHub revert PR (DGX #1):** rollback becomes a REAL revert PR
  on a throwaway repo via GitHub MCP/OAuth, gated by approval. Matches the bar verbatim.
- **Layer 3 — Real Slack notify/approve:** post incident summary + proposed fix to a
  real Slack channel via Slack MCP; approve there if possible.
- **Layer 4 (cheap extras if ahead):** 10-sec session-persistence demo (kill tab
  mid-run, reopen, continues); pull a runbook via web search (route through Bright
  Data → qualifies for a 5th track).

## Harness features we MUST visibly show (this wins DGX)
- [ ] Sandboxed execution — agent-written diagnostic code runs isolated (Layer 1)
- [ ] Human approval gate — run PAUSES before destructive action (Layer 0) — money shot
- [ ] Subagents — parallel investigation across logs/metrics/deploys, merged
- [ ] MCP + OAuth — real GitHub (+ Slack) integration (Layers 2–3)
- [ ] Session survival across reconnect (Layer 4)

## The demo scenario (deterministic — do NOT improvise)
- `checkout-service` degraded: error rate ~34%, p99 ~4200ms.
- Root cause: deploy **v1.4.2** shipped ~15 min before the alert.
- Correct fix: **rollback to v1.4.1**. Restart alone will NOT fix it (forces real reasoning).
- After fix: error rate <1%, latency <300ms. `POST /reset` re-arms the scenario.

## Architecture
```
[ Agent on TrueFoundry ] --tools--> [ tools.py ] --HTTP--> [ mock_env (telemetry + mock actions) ]
        |                         [ diagnostics.py runs in SANDBOX ]
        |                         [ integrations/github_ops.py -> REAL revert PR (Layer 2) ]
        |                         [ integrations/slack_ops.py   -> REAL message   (Layer 3) ]
   [ UI dashboard ] <-- steps, WAITING-FOR-APPROVAL card, approve/reject, after-state
```

## Team split (2 people) — see PERSON_A_AGENT.md / PERSON_B_INTERFACE.md
- **Mayuresh — Agent/harness (PERSON_A_AGENT.md):** harness wiring, tool
  registration, approval gate, sandbox diagnostics, GitHub/Slack integration, subagents.
- **Zeel — Interface/env/demo (PERSON_B_INTERFACE.md):** mock_env, approval_server,
  `ui/dashboard.html`, Qodo, demo recording, blog draft.
- Seams between them are frozen: TOOL_CONTRACT.md + EVENT_CONTRACT.md.

## Cut-lines (decide now, don't argue at 16:00)
1. Layer 2/3 (real integrations) not working by 15:30 → ship Layer 0+1. A real
   sandbox demo + clean approval gate still contends.
2. Subagents flaky → cut. Clean serial investigation beats broken parallel.
3. UI behind → the CLI + `fallback_agent.py` still demos the approval gate.

## Track targets (one build, up to 5 tracks)
- Harness/DGX — sandbox + approval + subagents + real MCP/OAuth actions.
- UI/iPad — the dashboard. MOST winnable; do not under-invest.
- Qodo/Mac Mini — run every PR through Qodo, fix findings. REQUIRED for that track.
- Blog/keyboard — write up what you built + what broke.
- Bright Data/AirPods — only if Layer 4 web-search-a-runbook lands.

## Run commands
```bash
cd mock_env && pip install -r requirements.txt && uvicorn main:app --reload --port 8000
# fallback demo (needs OPENAI_API_KEY):
export OPENAI_API_KEY=... && python fallback_agent.py
# UI: open ui/dashboard.html (reads events from the agent's events feed)
```

## Qodo (required for Code Quality track)
Open a PR per change, run the Qodo review agent, fix findings before merge. Don't merge red. Keep PRs small.
