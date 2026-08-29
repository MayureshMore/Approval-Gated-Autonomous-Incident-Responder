# Person B — Interface, Environment & Demo (owner: Zeel)

Paste this into Claude Code as your working brief.

## Your mission
Make Ripcord something a stranger can pick up and drive — win the UI/iPad track —
and own the environment, the demo, and the blog. You own everything the human SEES.

## Already built for you (don't rebuild — polish)
`mock_env/` (breakable prod stack), `approval_server.py` (event bus + approval
bridge), `ui/dashboard.html` (timeline + approval card + metrics). The full flow
is tested and works.

## Your files
`mock_env/*`, `approval_server.py`, `ui/dashboard.html`, `DEMO_SCRIPT.md`, blog draft.

## Tasks in order
- **B1 — Run it (do first).** Start mock_env (:8000) + approval_server (:8500),
  open http://localhost:8500/. Drive a run with `AUTO_APPROVE=1 python
  fallback_agent.py` (borrow A's key or use the sim) and watch it render.
- **B2 — Polish the dashboard to the judging sentence.** It must clearly show
  "what it's doing, what it's waiting on, what it did, and ask before the
  irreversible step." Make the approval card impossible to miss; add before→after
  metric deltas; make it look good on a tablet (it's an iPad prize). Optional: a
  small latency/error sparkline, a run timer.
- **B3 — Own the event contract.** Keep the bus + dashboard in sync with what A's
  agent emits (EVENT_CONTRACT.md). If A needs a new event kind, add rendering for it.
- **B4 — Environment.** Keep `/reset` deterministic. Add richer log lines only if
  they make the demo read better. Don't break TOOL_CONTRACT.md shapes.
- **B5 — Qodo.** Wire Qodo on the repo; run every PR through it, fix findings before
  merge. This is the Code Quality track — nearly free, don't skip it.
- **B6 — Demo.** Rehearse DEMO_SCRIPT.md, record the 90-sec run twice as fallback.
- **B7 — Blog.** Draft the write-up: what you built, how it's wired, what broke.

## Contract you MUST honor
- Read events exactly per **EVENT_CONTRACT.md**; POST `/decision` on button clicks.
- Don't rename bus endpoints or the `ui/dashboard.html` path without telling A.
- Keep `mock_env` responses matching **TOOL_CONTRACT.md**.

## Definition of done
Dashboard shows a full run, in-UI approval works against A's live agent, tablet-
friendly, demo recorded, Qodo run clean, blog drafted.

## Ask Claude Code to start with
"Read CLAUDE.md, TOOL_CONTRACT.md, EVENT_CONTRACT.md, approval_server.py,
ui/dashboard.html, mock_env/main.py. Then help me do B1: run the mock env, the
approval server, and the dashboard, and confirm a run renders end to end."
