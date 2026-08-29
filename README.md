# Ripcord — Approval-Gated Autonomous Incident Responder

An AI on-call agent. It investigates a production alert with read-only tools,
writes and runs its own diagnostic **in a sandbox**, and then **stops and asks a
human before any destructive action** — executing only on approval, then
confirming recovery.

Built for the Agent Harness Hackathon. Primary runtime = TrueFoundry harness; the
repo also ships a self-contained path so there is always a working demo.

---

## Quick start — 60 seconds, no API key needed

```bash
git clone https://github.com/MayureshMore/Approval-Gated-Autonomous-Incident-Responder.git
cd Approval-Gated-Autonomous-Incident-Responder

make setup     # uv venv --python 3.12 .venv && uv pip install -r requirements.txt
make demo      # starts everything, resets the scenario, runs the agent
```

Then open **http://localhost:8500/**. `make demo` needs no API key — it uses the
deterministic `sim` provider. `make demo-live` runs the same thing on a real
model through the TrueFoundry gateway, and `make demo-resume` replays the
session-survival beat. Ctrl-C stops everything and releases the ports.

<details>
<summary>Running the three processes by hand instead</summary>

```bash
# terminal 1 — the breakable prod stack
PYTHONPATH=. .venv/bin/uvicorn mock_env.main:app --port 8000

# terminal 2 — event bus + dashboard  →  open http://localhost:8500/
.venv/bin/uvicorn approval_server:app --port 8500

# terminal 3 — the agent
AGENT_BUS_URL=http://localhost:8500 .venv/bin/python run_agent.py --provider sim --subagents
```
</details>

Watch the dashboard: three subagents sweep metrics/logs/deploys in parallel, the
agent runs a diagnostic in the sandbox, then hits a **Waiting for approval** card
and *stops*. Click **Approve** — checkout-service goes from a 34% error rate to
0.4%.

Reset between runs:
`curl -sX POST localhost:8000/reset && curl -sX POST localhost:8500/reset`

**Providers.** `--provider sim` is deterministic and needs no API key — it drives
the *real* gate, sandbox and environment, with only token generation scripted.
`--provider truefoundry` is the primary runtime (below); `--provider openai` and
`--provider anthropic` run the identical loop against those APIs directly.

### Running on the TrueFoundry AI Gateway

The gateway is OpenAI-compatible, so every model call is routed, authenticated
and **traced** by TrueFoundry, and swapping the underlying model is a config
change rather than a code change.

```bash
cp .env.example .env        # then paste your TrueFoundry PAT into TRUEFOUNDRY_API_KEY

python run_agent.py --list-models                    # what your tenant exposes
python run_agent.py --provider truefoundry --selftest
AGENT_BUS_URL=http://localhost:8500 python run_agent.py --provider truefoundry --subagents
```

Secrets live in `.env` (gitignored); `run_agent.py` loads it automatically and
`--selftest` prints config with keys masked.

Gateway model ids are `{provider-account}/{model}` — a bare `gpt-4o` will 403. The
preflight validates the configured id against the gateway's own list and refuses
to start on a mismatch, so a bad id surfaces before the demo rather than during
it. `--model <id>` overrides per run. Each request carries an `X-TFY-METADATA`
header tagging the run id, so a run on the dashboard traces to its cost and
latency in TrueFoundry's observability.

```bash
.venv/bin/python run_agent.py --selftest   # verify wiring before you present
.venv/bin/python -m pytest                 # 127 tests
```

---

## What makes this more than a chat wrapper

**A human approval gate that fails closed.** Timeout, unreachable bus, Ctrl-C, no
terminal — every one of them *denies*. There is no path where "we couldn't ask"
becomes "go ahead". A rejection is final: the agent stands down rather than
retrying or substituting another destructive action.

**A sandbox that is a real boundary.** The agent writes a diagnostic; we execute
it in a separate process with a scrubbed environment, a throwaway working
directory, resource limits and a wall-clock kill. Inside it, the network is dead,
`subprocess` will not import, `os.listdir()` sees only `diagnostics.py`, and
`OPENAI_API_KEY` does not exist. Fifteen tests hold that line.

**Reasoning that is forced, not staged.** The scenario is built so a restart
*cannot* fix the incident — only correctly correlating the bad deploy and rolling
back does. A test keeps it that way.

**Provider independence.** `agent/providers/base.py` is a three-method protocol.
Porting to a new runtime is one class; the loop, the gate and the sandbox are
untouched.

## The claims, and the test that proves each

| Claim | Test |
|---|---|
| Nothing destructive runs without a human | `test_no_destructive_call_without_a_prior_approval` — walks the event stream, fails if a destructive call ever precedes its approval |
| The diagnostic really is sandboxed | `tests/test_sandbox.py` — network, subprocess, filesystem, secrets, CPU, memory |
| The gate fails closed | `tests/test_approval.py` — timeout, dead bus, interrupt, non-tty |
| A rejection is respected | `test_rejected_run_leaves_production_untouched` |
| Kill it mid-incident and it resumes, gate intact | `test_resume_still_asks_before_the_destructive_action`, `test_resume_honours_a_rejection_from_before_the_crash` |
| The seam with the dashboard holds | `tests/test_contracts.py` — every field in `TOOL_CONTRACT.md` / `EVENT_CONTRACT.md` |

## What's where

| Path | Purpose |
|---|---|
| `scripts/demo.sh` | One command to drive the whole demo; cleans up on Ctrl-C |
| `Makefile` | `make demo` / `demo-live` / `demo-resume` / `test` / `reset` |
| `run_agent.py` | CLI entrypoint — `--provider`, `--subagents`, `--github`, `--selftest` |
| `agent/core.py` | The loop: investigate → diagnose → propose → **pause** → execute → verify |
| `agent/approval.py` | The approval gate (ui / cli / auto), fails closed |
| `agent/registry.py` | One source of truth for tools; `audit()` catches drift |
| `agent/subagents.py` | Three parallel read-only investigators, merged |
| `agent/session.py` | Layer 4 — checkpoint and resume a killed run |
| `agent/providers/` | `truefoundry` (primary), `sim`, `openai`, `anthropic` behind one protocol |
| `sandbox/runner.py` | Parent half of agent-written code execution |
| `sandbox/bootstrap.py` | Runs *inside* the child; locks the process down |
| `diagnostics.py` | The correlation maths the agent runs in the sandbox |
| `integrations/github_ops.py` | Real revert PR — argv-only, temp clone, dry-run by default |
| `mock_env/` | The breakable prod stack (telemetry + mock actions) |
| `approval_server.py` | Event bus + in-UI approval bridge + serves the dashboard |
| `ui/dashboard.html` | The dashboard: timeline, approval card, before→after metrics |
| `tests/` | 127 tests |
| `CLAUDE.md` | The plan: layers, cut-lines, tracks |
| `TOOL_CONTRACT.md` / `EVENT_CONTRACT.md` | Frozen seams between the two workstreams |
| `PERSON_A_AGENT.md` / `PERSON_B_INTERFACE.md` | Per-owner briefs and live status |
| `fallback_agent.py` | The original single-file loop, kept as insurance |

## Status

Layers 0 (mock loop), 1 (sandboxed diagnostic) and 4 (session survival), plus
subagents, are **done and demoable** — with no API key at all, via `--provider
sim`. The TrueFoundry gateway is **live and verified**: a full incident response
has run on GPT-4o through it, end to end. Layer 2 (real GitHub revert PR) is
written, hardened and tested but not yet fired against a live repo — it stays
dry-run until `GITHUB_REVERT_ENABLED=1`. See `PERSON_A_AGENT.md` for the ledger.

## License / provenance
MIT — see [LICENSE](LICENSE). Open source, as the hackathon requires.
Mock telemetry is synthetic; no real production system is involved, and the
`.env` holding gateway credentials is gitignored and never committed.
