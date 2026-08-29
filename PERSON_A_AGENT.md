# Person A — Agent & Harness (owner: Mayuresh)

> **This file is the handoff doc.** It is kept current as work lands, so if a
> session runs out of context or usage you can open a fresh one, paste
> *"Read PERSON_A_AGENT.md and continue"*, and lose nothing.
>
> **Status: 113 tests passing. Layers 0, 1 and 4 plus subagents demo end-to-end
> today with no API key.** The TrueFoundry gateway provider is code-complete and
> the key authenticates — but **the account has no models provisioned**, which is
> a dashboard step (see A2). Layer 2 (real GitHub PR) is built and tested, not yet
> fired live.
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
`truefoundry` (primary), `sim` (no key, deterministic — the demo safety net),
`openai`, `anthropic`. Same loop, same gate, same sandbox for all four; only token
generation differs.

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
- [~] **A2 — TrueFoundry gateway.** Code complete, 15 tests. **The key
      authenticates but the account exposes zero models.**
      - Symptom: `GET /api/llm/models` → `{"data":[]}`; every completion → 403
        *"User m_more1@u.pacific.edu is not authorized to access model X or model
        does not exist"*.
      - **This is a dashboard step, not a code bug.** Go to
        https://pacific.truefoundry.cloud/gateway-onboarding and add a provider
        account (e.g. OpenAI, using an OpenAI key) so the gateway has a model to
        route to. Then:
        ```bash
        .venv/bin/python run_agent.py --list-models        # must be non-empty
        # put a real id in .env as TRUEFOUNDRY_MODEL, e.g. openai-main/gpt-4o
        .venv/bin/python run_agent.py --provider truefoundry --selftest   # expect PASS
        ```
      - Model ids are `{provider-account}/{model}` — **bare `gpt-4o` 404s.** The
        preflight validates the configured id against the gateway's own list and
        refuses to start on a mismatch, so this surfaces before the demo.
      - Every call sends `X-TFY-METADATA` tagging the run id, so a dashboard run
        traces to its cost and latency in TrueFoundry's observability.
      - **If onboarding can't be completed in time:** demo with `--provider sim`.
        It drives the real gate, real sandbox and real environment; only token
        generation is scripted. Nothing about the safety story is weakened.
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

## 2. One-line fix I need from you (visible in the demo)
`approval_server.py` appends its **own** `approval_decision` event in `POST
/decision`, and my gate emits one too — so the timeline renders **two "approved"
rows per approval**. Harmless, but it looks sloppy on stage. It is your file, so I
have not touched it. Either drop the server-side append, or dedupe by `request_id`
in the dashboard.

## 3. New events — two are worth rendering
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

## 5. Small ask for the bus
Have `GET /decision` echo back the `request_id` it was passed. The agent already
sends it and tolerates its absence, so nothing breaks today — it just closes a
stale-approval edge case if one run ever gates two actions.

## 6. What I did NOT touch
`mock_env/`, `approval_server.py`, `ui/dashboard.html`, `DEMO_SCRIPT.md` are
untouched and still yours. I only added a contract addendum to `EVENT_CONTRACT.md`.

## 7. Your critical path, given where I am
- **B5 (Qodo) is the cheapest track we have and it is not started.** Nearly free
  points; do not skip it.
- **B2 (dashboard polish)** — the `sandbox_exec` box is the highest-value single
  addition.
- **B6 (record the demo twice)** — do this *before* chasing polish. We have a
  working end-to-end run right now; capture it while it is green. `--provider sim`
  needs no API key, so a recording can be made at any time without depending on the
  gateway.
