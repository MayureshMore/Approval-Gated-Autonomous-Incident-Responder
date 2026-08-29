# Person A — Agent & Harness (owner: Mayuresh)

> **This file is the handoff doc.** It is kept current as work lands, so if a
> session runs out of context or usage you can open a fresh one, paste
> "Read PERSON_A_AGENT.md and continue", and lose nothing.
>
> **Status: A1–A6 built and green. 97 tests passing. Layer 0 + Layer 1 + subagents
> demo end-to-end today with no API key. A2 (TrueFoundry) is CODE-COMPLETE and
> waiting only on an API key.** Remaining: fire A2 against the live gateway,
> A6 live PR (needs `gh` + a repo), A7 Slack.
> _Last updated: 2026-08-29._

## Mission
Own everything the agent DOES. Win the DGX/Harness track by making the harness
visibly do the work: sandboxed code, human-approval gate, subagents, real
MCP/OAuth actions.

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
.venv/bin/python -m pytest                  # 83 tests
```

**Providers** — `truefoundry` (**primary**, see below), `sim` (no key,
deterministic, the demo safety net), `openai`, `anthropic`. Same loop, same gate,
same sandbox for all four; only token generation differs.

### TrueFoundry AI Gateway (the primary runtime)

The gateway is **OpenAI-compatible**, so this was a configuration job, not a
rewrite — `TrueFoundryProvider` subclasses `OpenAIProvider`. Verified against
`pacific.truefoundry.cloud`: `POST /api/llm/chat/completions` and
`GET /api/llm/models` both route and return 401 for a bad token, so the base URL
is right and only a key is missing.

```bash
cp .env.example .env        # then fill in TRUEFOUNDRY_API_KEY
export TRUEFOUNDRY_API_KEY=...
export TRUEFOUNDRY_BASE_URL=https://pacific.truefoundry.cloud/api/llm

python run_agent.py --list-models                    # what this tenant exposes
export TRUEFOUNDRY_MODEL=openai-main/gpt-4o          # a real id from that list
python run_agent.py --provider truefoundry --selftest        # expect PASS
AGENT_BUS_URL=http://localhost:8500 \
  python run_agent.py --provider truefoundry --subagents     # the demo
```

Model ids are `{provider-account}/{model}` — **`gpt-4o` alone will 404**. The
preflight checks the configured model against the gateway's list and refuses to
start with a wrong one, so you find out before you are on stage, not during.

Every call carries an `X-TFY-METADATA` header tagging the run id, so a run on the
dashboard is traceable to its cost and latency in TrueFoundry's observability —
worth showing the judges alongside the timeline.

---

## What I built (and where it lives)

| File | What it does |
|---|---|
| `run_agent.py` | CLI entrypoint. `--provider/--subagents/--github/--selftest`. |
| `agent/core.py` | The loop. Orchestration only — deliberately boring. |
| `agent/approval.py` | **The gate.** ui / cli / auto modes. Fails closed. |
| `agent/bus.py` | Event emission per EVENT_CONTRACT (HTTP + file + console). |
| `agent/registry.py` | One source of truth for tools; `audit()` catches drift. |
| `agent/subagents.py` | 3 parallel read-only investigators, merged. |
| `agent/providers/` | `truefoundry` (primary), `sim` (scripted), `openai`, `anthropic`, `base` (protocol). |
| `sandbox/runner.py` | Parent half of code exec + `REFERENCE_DIAGNOSTIC`. |
| `sandbox/bootstrap.py` | Runs **inside** the child; locks the process down. |
| `integrations/github_ops.py` | Real revert PR. Argv-only, temp clone, dry-run default. |
| `tests/` | 97 tests. `test_agent_e2e.py` holds THE invariant. |

Unchanged from the original scaffold: `tools.py`, `tool_schemas.py`,
`diagnostics.py`, `agent_prompt.md`. `fallback_agent.py` still works; `run_agent.py`
supersedes it.

---

## The four claims, and the test that proves each

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

The correlation score in `DEMO_SCRIPT.md` (**0.76**) is pinned by
`test_demo_scenario_scores_high`. If you change the scenario timings, that test
tells you the script needs updating.

---

## Task ledger

- [x] **A1 — Prove the loop.** Done, and generalised: `run_agent.py` beats the
      original `fallback_agent.py` (structured events, run report, preflight).
- [~] **A2 — TrueFoundry gateway.** Code complete: `agent/providers/truefoundry.py`,
      wired into `run_agent.py`, 14 tests, preflight + `--list-models`.
      **Waiting only on `TRUEFOUNDRY_API_KEY`.** The provider protocol paid for
      itself — the port was one subclass, and the loop, gate and sandbox were
      untouched. First thing to do when the key lands: `--list-models`, then
      `--selftest`, then a live run.
- [x] **A3 — Approval gate.** `agent/approval.py`. Emits `awaiting_approval`,
      blocks, polls, resumes. Request-ids stop a stale decision approving a later
      action.
- [x] **A4 — Sandbox (Layer 1).** Real process isolation, not a claim. The agent
      writes the snippet; we execute it; it cites the score.
- [x] **A5 — Subagents.** 3 lanes in parallel, merged into the evidence pack.
      Read-only by design — concurrency and destructive actions don't mix, and
      keeping the gate on one serial path is what makes it trustworthy.
- [~] **A6 — Real GitHub PR (Layer 2).** Code done, hardened, tested (23 tests).
      **Not yet fired for real** — needs `gh` installed + a throwaway repo:
      ```bash
      brew install gh && gh auth login
      export GITHUB_REPO=you/incident-demo GITHUB_REVERT_ENABLED=1
      .venv/bin/python integrations/github_ops.py   # preflight → {"ready": true}
      .venv/bin/python run_agent.py --provider sim --github
      ```
      Dry-run until `GITHUB_REVERT_ENABLED=1`, so a misconfigured machine can't
      surprise anyone. **For the DGX track, prefer GitHub MCP over OAuth in the
      harness** — "the harness is doing the work" is the judged sentence; this
      module is the fallback if MCP setup stalls.
- [ ] **A7 — Slack (Layer 3, optional).** Not started. Mirror `github_ops.py`:
      argv-only, dry-run default, gate the tool by adding it to
      `registry.REQUIRES_APPROVAL`.

---

## Tell Zeel (contract changes — all additive, nothing renamed)

The seven `EVENT_CONTRACT.md` kinds are untouched and every required field is
still emitted (enforced by `tests/test_contracts.py`). I added **four optional
kinds** the dashboard may ignore safely, plus optional fields:

- `subagent_started {subagent, task}` / `subagent_finished {subagent, findings}`
  — worth rendering as three parallel lanes; it looks great and it's the
  subagent evidence for the DGX track.
- `sandbox_exec {code, payload_keys}` — **render this one.** Showing the code the
  agent wrote, in a box labelled "executed in sandbox", is the strongest visual
  we have.
- `error {message}` — a run that failed.
- Every event now carries `run_id`; `awaiting_approval`/`approval_decision` carry
  `request_id`; `run_started` carries `provider`, `approval_mode`, `tools`.

**One ask:** have `GET /decision` echo back the `request_id` it was given. The
agent already sends it and tolerates its absence, so nothing breaks today — it
just closes a stale-approval edge case if we ever gate two actions in one run.

---

## Where the bodies are buried

- **It is TrueFoundry, not "TrueForge".** The original plan docs had the name
  wrong throughout; corrected repo-wide. It is an LLM *gateway* (an
  OpenAI-compatible proxy), not an agent harness SDK — which is why A2 turned out
  to be a config change. Adjust the pitch accordingly: the harness story is our
  own sandbox + approval gate + subagents, with TrueFoundry as the routed,
  traced, model-agnostic front door.
- **Python 3.12, not 3.14.** The pinned deps have no 3.14 wheels. `.venv` is 3.12.
- **`PYTHONPATH=.` when starting mock_env** — `uvicorn mock_env.main:app` needs it.
- **Sim provider is not a mock of the loop.** It drives the real gate, the real
  sandbox and the real environment; only token generation is scripted. That's why
  it is safe to demo from and to test with.
- **`RLIMIT_AS` and `RLIMIT_NPROC` silently fail on macOS.** Each rlimit is applied
  independently so the CPU cap still lands; the wall-clock timeout in the parent
  is the real backstop.
- **Restart must never fix the incident.** `test_restart_does_not_fix_a_bad_deploy`
  guards it — it's what forces the agent to actually reason.

## If you are resuming cold
1. `.venv/bin/python run_agent.py --selftest` → expect `PASS`.
2. `.venv/bin/python -m pytest` → expect 97 passed.
3. Do a full dashboard run (commands at the top). If that's green, the demo is safe.
4. Then: A2 live (needs `TRUEFOUNDRY_API_KEY`) or A6 live (needs `gh` + a repo).
