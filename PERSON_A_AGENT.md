# Person A — Agent & Harness (owner: Mayuresh)

> **This file is the handoff doc.** It is kept current as work lands, so if a
> session runs out of context or usage you can open a fresh one, paste
> *"Read PERSON_A_AGENT.md and continue"*, and lose nothing.
>
> **Status: 113 tests passing. A2 IS LIVE — a full incident response ran on
> GPT-4o through the TrueFoundry gateway, end to end, and recovered the service.**
> Layers 0, 1 and 4 plus subagents also demo with no API key at all. Layer 2 (real
> GitHub PR) is built and tested, not yet fired live.
> _Last updated: 2026-08-29._

## Mission
Own everything the agent DOES. Win the DGX/Harness track by making the harness
visibly do the work: sandboxed code, a human-approval gate, subagents, session
survival, real MCP/OAuth actions.

---

## Run it right now (60 seconds, no API key)

```bash
cd Approval-Gated-Autonomous-Incident-Responder
uv venv --python 3.12 .venv && uv pip install -r requirements.txt   # first time only

# terminal 1 — the breakable prod stack
PYTHONPATH=. .venv/bin/uvicorn mock_env.main:app --port 8000

# terminal 2 — event bus + dashboard
.venv/bin/uvicorn approval_server:app --port 8500     # open http://localhost:8500/

# terminal 3 — the agent
curl -sX POST localhost:8000/reset && curl -sX POST localhost:8500/reset
AGENT_BUS_URL=http://localhost:8500 .venv/bin/python run_agent.py --provider sim --subagents
```
It investigates, runs the sandboxed diagnostic, then **stops**. Click Approve in
the dashboard. checkout-service goes 34% → 0.4% error rate.

```bash
.venv/bin/python run_agent.py --selftest    # verify wiring before you present
.venv/bin/python -m pytest                  # 113 tests
```

### Secrets
All secrets live in **`.env`** (gitignored, `chmod 600`). `run_agent.py` loads it
automatically; existing env vars still win. Never paste a key into chat or a
command line. `--selftest` prints config with secrets masked (`tfy_…8316 (70 chars)`).

### Providers
`truefoundry` (primary, **live on `ms-openai-main/gpt-4o`**), `sim` (no key,
deterministic — the demo safety net), `openai`, `anthropic`. Same loop, same gate,
same sandbox for all four; only token generation differs.

```bash
# the live demo, on a real model through the gateway
AGENT_BUS_URL=http://localhost:8500 .venv/bin/python run_agent.py \
    --provider truefoundry --subagents
```

---

## What I built

| File | What it does |
|---|---|
| `run_agent.py` | CLI. `--provider/--subagents/--github/--selftest/--resume/--list-runs/--list-models` |
| `agent/core.py` | The loop. Orchestration only — deliberately boring. |
| `agent/approval.py` | **The gate.** ui / cli / auto modes. Fails closed. |
| `agent/session.py` | **Layer 4.** Checkpoint + resume a killed run. |
| `agent/bus.py` | Event emission per EVENT_CONTRACT (HTTP + file + console). |
| `agent/registry.py` | One source of truth for tools; `audit()` catches drift. |
| `agent/subagents.py` | 3 parallel read-only investigators, merged. |
| `agent/env.py` | `.env` loader + secret masking. No new dependency. |
| `agent/providers/` | `truefoundry` (primary), `sim`, `openai`, `anthropic`, `base` (protocol). |
| `sandbox/runner.py` | Parent half of code exec + `REFERENCE_DIAGNOSTIC`. |
| `sandbox/bootstrap.py` | Runs **inside** the child; locks the process down. |
| `integrations/github_ops.py` | Real revert PR. Argv-only, temp clone, dry-run default. |
| `tests/` | 113 tests. `test_agent_e2e.py` holds THE invariant. |

Unchanged from the original scaffold: `tools.py`, `tool_schemas.py`,
`diagnostics.py`, `agent_prompt.md`. `fallback_agent.py` still works;
`run_agent.py` supersedes it.

---

## The five claims, and the test that proves each

Say these on stage; every one is checkable, not asserted.

1. **"Nothing destructive runs without a human."**
   `test_no_destructive_call_without_a_prior_approval` walks the event stream and
   fails if a destructive `tool_call` ever precedes its `approval_decision`.
2. **"The diagnostic runs in a real sandbox."**
   `tests/test_sandbox.py` — 15 tests. Network blocked, subprocess blocked, repo
   invisible (`os.listdir()` sees only `diagnostics.py`), API keys absent from the
   env, infinite loop killed, 4 GB allocation dies in the child not the parent.
3. **"It fails closed."**
   Timeout, dead bus, Ctrl-C, non-tty — every one denies. `tests/test_approval.py`.
4. **"A rejection is respected."**
   `test_rejected_run_leaves_production_untouched` — prod stays degraded, and the
   agent stands down instead of retrying.
5. **"Kill it mid-incident and it resumes — without weakening the gate."**
   `test_resume_still_asks_before_the_destructive_action` re-runs the invariant on
   a resumed run. `test_resume_honours_a_rejection_from_before_the_crash` proves you
   cannot turn a "no" into a "yes" by killing the process and retrying.

The correlation score in `DEMO_SCRIPT.md` (**0.76**) is pinned by
`test_demo_scenario_scores_high`. Change the scenario timings and that test tells
you the script needs updating.

---

## Layer 4 — the session-survival demo (10 seconds, very cheap win)

```bash
# 1. start a run; let it reach the approval card
AGENT_BUS_URL=http://localhost:8500 .venv/bin/python run_agent.py --provider sim --subagents

# 2. Ctrl-C it (or kill -9) while it is waiting

# 3. show the saved state
.venv/bin/python run_agent.py --list-runs
#    3733ef33  running   step=6   provider=sim  pending=['rollback_service']

# 4. resume — same run_id, so the dashboard timeline is continuous
AGENT_BUS_URL=http://localhost:8500 .venv/bin/python run_agent.py --provider sim --resume last
```
It picks the pending rollback back up at step 6 (no re-investigation, no second
sandbox run) and asks again. State is plain JSON in `runs/<run_id>.json` — open it
mid-demo to show there is nothing up our sleeve.

---

## Task ledger

- [x] **A1 — Prove the loop.** Done, and generalised: `run_agent.py` beats the
      original `fallback_agent.py` (structured events, run report, preflight).
- [x] **A2 — TrueFoundry gateway. LIVE AND VERIFIED.** A full incident response
      ran on GPT-4o through the gateway and recovered the service.
      - **Verified config** (already in `.env`):
        ```
        TRUEFOUNDRY_BASE_URL=https://pacific.truefoundry.cloud/api/llm
        TRUEFOUNDRY_MODEL=ms-openai-main/gpt-4o
        ```
        `https://gateway.truefoundry.ai` also works and returns the same two
        models; ours is kept because it is the one we have run against.
      - **The `ms-` prefix is not optional.** Ids are `ms-openai-main/gpt-4o` and
        `ms-openai-main/gpt-4o-mini`. `openai-main/gpt-4o` and bare `gpt-4o` both
        403. Preflight validates the id against the gateway's own list and refuses
        to start on a mismatch, so this cannot bite you on stage.
      - Every call sends `X-TFY-METADATA` tagging the run id, so a dashboard run
        traces to its cost and latency in TrueFoundry's observability.
      - `--model ms-openai-main/gpt-4o-mini` overrides per run (cheaper rehearsals).
      - **The earlier 403 was an unprovisioned account**, fixed by adding the
        `ms-openai-main` provider account in the dashboard. Preflight now names
        that failure explicitly if it recurs.
      - **Fallback stays valid:** `--provider sim` needs no key and drives the real
        gate, sandbox and environment. If the gateway wobbles mid-demo, switch
        providers and nothing about the safety story is weakened.
- [x] **A3 — Approval gate.** Emits `awaiting_approval`, blocks, polls, resumes.
      Request-ids stop a stale decision approving a later action.
- [x] **A4 — Sandbox (Layer 1).** Real process isolation, not a claim.
- [x] **A5 — Subagents.** 3 lanes in parallel, merged into the evidence pack.
      Read-only by design — concurrency and destructive actions don't mix, and
      keeping the gate on one serial path is what makes it trustworthy.
- [~] **A6 — Real GitHub PR (Layer 2).** Built, hardened, 23 tests. **Not yet
      fired live** — needs `gh` + a throwaway repo:
      ```bash
      brew install gh && gh auth login
      # then in .env:  GITHUB_REPO=you/incident-demo   GITHUB_REVERT_ENABLED=1
      .venv/bin/python integrations/github_ops.py      # preflight → {"ready": true}
      .venv/bin/python run_agent.py --provider sim --github
      ```
      Dry-run until `GITHUB_REVERT_ENABLED=1`, so a misconfigured machine cannot
      surprise anyone. **For the DGX track, prefer GitHub MCP over OAuth in the
      harness** — "the harness is doing the work" is the judged sentence; this
      module is the fallback if MCP setup stalls.
- [x] **A8 — Session survival (Layer 4).** Done. See above.
- [ ] **A7 — Slack (Layer 3, optional).** Not started. Mirror `github_ops.py`:
      argv-only, dry-run default, gate it by adding the tool to
      `registry.REQUIRES_APPROVAL`.

---

## Where the bodies are buried

- **It is TrueFoundry, not "TrueForge".** The original plan had the name wrong
  throughout; corrected repo-wide. It is an LLM **gateway** (an OpenAI-compatible
  proxy), not an agent-harness SDK — which is why A2 was a config change, not a
  port. **Adjust the pitch:** the harness story is *ours* (sandbox + approval gate
  + subagents + session survival), with TrueFoundry as the routed, traced,
  model-agnostic front door. Do not claim TrueFoundry runs the agent loop; a judge
  will check.
- **Python 3.12, not 3.14.** The pinned deps have no 3.14 wheels. `.venv` is 3.12.
- **`PYTHONPATH=.` when starting mock_env** — `uvicorn mock_env.main:app` needs it.
- **The sim provider is not a mock of the loop.** It drives the real gate, sandbox
  and environment; only token generation is scripted. That is why it is safe to
  demo from and to test with.
- **`RLIMIT_AS` and `RLIMIT_NPROC` silently fail on macOS.** Each rlimit is applied
  independently so the CPU cap still lands; the parent's wall-clock timeout is the
  real backstop.
- **Restart must never fix the incident.** `test_restart_does_not_fix_a_bad_deploy`
  guards it — it is what forces the agent to actually reason.
- **`runs/` and `.env` are gitignored.** Session state is per-machine; secrets never
  leave the laptop.

## If you are resuming cold
1. `.venv/bin/python run_agent.py --selftest` → expect `PASS` (mock env must be up).
2. `.venv/bin/python -m pytest` → expect **113 passed**.
3. Do a full dashboard run (commands at the top). If that is green, the demo is safe.
4. Then: A2 live (needs gateway onboarding) or A6 live (needs `gh` + a repo).

---
---

# → Handoff to Zeel (Person B)

Everything below is what changed on my side that touches yours. **No frozen
contract was broken** — `tests/test_contracts.py` fails the build if any documented
field or event kind ever stops being emitted. Full detail is in the addendum at the
bottom of `EVENT_CONTRACT.md`.

## 1. Repo layout — check this before you push
The project now sits at the **repo root** (not nested in `incident-responder/`), so
`git clone && cd && run` works with no extra hop and the README renders on GitHub.
If you have a local repo pointing at the same remote with a different layout, our
histories will disagree about where every file lives. **Sync with me before pushing.**

## 2. ~~Duplicate approval_decision~~ — DONE (fd656a1), verified
You fixed it by round-tripping `request_id` through `POST /decision` and deduping
on `e.request_id||e.action` in the dashboard. I ran my agent against your updated
`approval_server.py` end to end: both decision events now carry the same
`request_id`, so they collapse to one row. Nothing further needed.

## 3. New events — you already render these (fd656a1). Kept here as the reference.
All additive; the dashboard may ignore any of them without breaking.

```
sandbox_exec       { code: str, payload_keys: [str] }
subagent_started   { subagent: "metrics"|"logs"|"deploys", task: str }
subagent_finished  { subagent: str, findings: object }
run_resumed        { scenario: str, step: int, pending: [str] }
error              { message: str }
```

- **`sandbox_exec` — please render this.** Show the code the agent wrote in a box
  labelled *"executed in sandbox"*. It is the single strongest visual we have for
  the harness track, and it is the moment in `DEMO_SCRIPT.md` where the pitch says
  *"it doesn't guess"*.
- **`subagent_started` / `subagent_finished`** — three lanes that fill in as they
  land. Reads well and is the subagent evidence for judging.
- **`run_resumed`** — needed for the Layer 4 demo (below). A divider in the
  timeline saying *"run resumed at step N"* is enough.

Optional fields added to existing kinds, all safe to ignore: every event now
carries `run_id`; `awaiting_approval`/`approval_decision` carry `request_id`;
`approval_decision` carries `reason`; `run_started` carries `provider`,
`approval_mode`, `tools`; `run_finished` carries `steps`, `destructive_executed`,
`error`.

## 4. New demo beat you should design for — session survival
I can now kill the agent mid-incident and resume it. The dashboard is what makes
this land:
1. Run reaches the **Waiting for approval** card.
2. I kill the agent process. **The card must stay on screen** — do not clear the
   timeline when events stop arriving.
3. I resume; a `run_resumed` event arrives with the *same* `run_id`, and the run
   continues in the same timeline.

If the dashboard wipes state on a polling gap, this demo dies. Worth checking.

## 5. ~~Echo request_id from GET /decision~~ — DONE (fd656a1)
Confirmed working against my gate.

## 6. What I did NOT touch
`mock_env/`, `approval_server.py`, `ui/dashboard.html`, `DEMO_SCRIPT.md` are
untouched and still yours. I only added a contract addendum to `EVENT_CONTRACT.md`.

## 7. Your critical path, given where I am
- **B6 (record the demo twice) is now the top priority.** The gateway is live and
  the whole loop is green *right now* — capture it while it is. Record the real
  one (`--provider truefoundry --subagents`) and a `--provider sim` one as
  insurance; sim needs no API key, so it can never be blocked by the gateway.
- **B5 (Qodo) is the cheapest track we have and it is not started.** Nearly free
  points; do not skip it.
- **Layer 4 beat** — worth adding to the recording: kill the agent at the approval
  card, `--resume last`, watch it continue in the same timeline. Ten seconds, and
  it is a harness feature nobody else will have.

## 8. Live gateway config (if you want to run the real agent yourself)
Working, verified. Put your own PAT in `.env` (gitignored):
```
TRUEFOUNDRY_API_KEY=<your PAT>
TRUEFOUNDRY_BASE_URL=https://pacific.truefoundry.cloud/api/llm
TRUEFOUNDRY_MODEL=ms-openai-main/gpt-4o
```
The `ms-` prefix is required. `--provider sim` still needs no key at all.
