# Ripcord — Approval-Gated Autonomous Incident Responder

An AI on-call agent. It investigates a production alert with read-only tools,
writes and runs its own diagnostic **in a sandbox**, and then **stops and asks a
human before any destructive action** — executing only on approval, then
confirming recovery.

Built for the Agent Harness Hackathon.

**Where TrueFoundry sits, stated precisely.** Every model call goes through the
**TrueFoundry AI Gateway** — routed, authenticated and traced, with each request
tagged `X-TFY-METADATA` so a run on the dashboard maps to its own cost and
latency in TrueFoundry's observability. A preflight validates the configured
model id against the gateway's own catalogue and refuses to start on a mismatch.
Swapping the underlying model is a config change, not a code change.

The gateway is an OpenAI-compatible model router, not an agent runtime — so the
harness layer here (sandboxed execution, the approval gate, subagents, session
survival) is **ours**, built to the contracts in `TOOL_CONTRACT.md` and
`EVENT_CONTRACT.md`. We are not claiming TrueFoundry runs the agent loop; it
routes and observes every model call the loop makes.

---

## See it work in 20 seconds, no setup at all

Open **[`ui/pitch.html`](ui/pitch.html)** in a browser, straight from disk. It
replays a **real** GPT-4o run through the TrueFoundry gateway — the actual 19
events captured off the event bus, not a mockup — and **stops to ask your
approval** exactly where the live agent does. Approve and it recovers; reject and
it stands down and tells you production was deliberately left broken.

No server, no venv, no network, no API key.

## Quick start — 60 seconds, no API key needed

```bash
git clone https://github.com/MayureshMore/Approval-Gated-Autonomous-Incident-Responder.git
cd Approval-Gated-Autonomous-Incident-Responder

make setup     # uv venv --python 3.12 .venv && uv pip install -r requirements.txt
make demo      # starts everything, resets the scenario, runs the agent
```

Then open **http://localhost:8500/**. `make demo` needs no API key — it uses the
deterministic `sim` provider, which drives the *real* gate, sandbox and
environment with only token generation scripted.

**No terminal?** Open `ui/pitch.html` in a browser — it replays a real
recorded gateway run, event by event, and stops to ask your permission exactly
where the live agent does.

`make demo-live` runs the identical demo on a real GPT-4o **through the
TrueFoundry gateway** — that is the one to watch if you want to see the gateway
carrying the run. `make demo-resume` replays the session-survival beat. Ctrl-C
stops everything and releases the ports.

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
`--provider truefoundry` routes through the gateway (below); `--provider openai` and
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
.venv/bin/python -m pytest                 # 227 tests
```

---

## What makes this more than a chat wrapper

**A human approval gate that fails closed.** Timeout, unreachable bus, Ctrl-C, no
terminal — every one of them *denies*. There is no path where "we couldn't ask"
becomes "go ahead". A rejection is final: the agent stands down rather than
retrying or substituting another destructive action.

**A sandbox that is a real boundary.** The agent writes a diagnostic; we execute
it in a separate process with a scrubbed environment, a throwaway working
directory, a CPU cap and a wall-clock kill. Inside it the network is dead,
`subprocess` will not import, `OPENAI_API_KEY` does not exist, and the
filesystem is confined to the sandbox's own directory — it cannot read this
repo, `/etc/passwd`, or the `.env` holding the gateway key, and cannot write
anywhere else. That last part was a real hole: until we went looking, a
diagnostic could `open('.env').read()` and hand the live key back in its own
result. Thirty-two tests hold the line now, including those escapes verbatim.

**Reasoning that is forced, not staged.** The scenario is built so a restart
*cannot* fix the incident — only correctly correlating the bad deploy and rolling
back does. A test keeps it that way.

**Provider independence.** `agent/providers/base.py` is a three-method protocol.
Porting to a new runtime is one class; the loop, the gate and the sandbox are
untouched.

## What is real, and what is synthetic

The first question worth asking about any agent demo.

| Component | Real? | Detail |
|---|---|---|
| The incident | **Synthetic** | `mock_env/` generates the telemetry, logs and deploy history. Deliberately: it is reproducible, `/reset`-able, and there is no real production to break. A flight simulator, not a fake cockpit. |
| The model | **Real** | Live GPT-4o through the TrueFoundry gateway. Real tokens, real cost, real latency. |
| The diagnostic code | **Real** | Genuinely model-authored Python. We do not template it. |
| The sandbox | **Real** | Separate OS process, scrubbed env, CPU cap, wall-clock kill, import denylist, filesystem confinement. |
| The correlation maths | **Real** | Actual datetime arithmetic over the payload the agent assembled itself. |
| The approval gate | **Real** | HTTP bus, real human decision, and it fails closed. |
| GitHub revert PR | **Dry-run** | Real code via the `gh` CLI, gated behind `GITHUB_REVERT_ENABLED=1`. Not fired against a live repo. |

> The incident is simulated. The agent's behaviour is not. Every safety property
> we claim is enforced by the operating system or by a test you can run right now.

## The claims, and the test that proves each

| Claim | Test |
|---|---|
| Nothing destructive runs without a human | `test_no_destructive_call_without_a_prior_approval` — walks the event stream, fails if a destructive call ever precedes its approval |
| The diagnostic really is sandboxed | `tests/test_sandbox.py` — network, subprocess, filesystem confinement, secrets, CPU |
| A human is never asked to approve something unexplained | `test_every_gated_tool_demands_a_reason`, `test_silent_destructive_call_still_gets_an_explained_card` |
| One approval opens exactly one gate | `test_a_decision_is_not_reused_by_a_later_gate` — an approval cannot leak into the next run |
| The gate fails closed | `tests/test_approval.py` — timeout, dead bus, interrupt, non-tty |
| A rejection is respected | `test_rejected_run_leaves_production_untouched` |
| Kill it mid-incident and it resumes, gate intact | `test_resume_still_asks_before_the_destructive_action`, `test_resume_honours_a_rejection_from_before_the_crash` |
| The seam with the dashboard holds | `tests/test_contracts.py` — every field in `TOOL_CONTRACT.md` / `EVENT_CONTRACT.md` |

## How we tested it

227 tests are the floor, not the story.

- **Adversarial.** Ten sandbox escape attempts, including `io.FileIO` and
  `os.open` bypasses that sidestep `builtins.open`. Ten blocked, and real
  analysis still runs.
- **Failure injection.** Killed the bus mid-approval, killed the environment
  mid-run, `kill -9` on the agent. No tracebacks reach the operator — failures
  come back as data the model can react to.
- **Concurrency.** Two agents on one bus: one approval opens exactly one gate.
- **Mutation testing.** We broke five load-bearing assertions on purpose and
  confirmed each one caught it. A test that cannot fail is not a test.
- **Hostile input.** Twelve events carrying `<script>` and `<img onerror>` in
  every field, rendered through the dashboard. Zero injections survived.
- **Fresh-clone rehearsal**, and five-plus live gateway runs hitting 0.76 every
  time.

## The bugs we found on ourselves

We went looking rather than waiting to be caught. **The suite was green through
every one of these** — which is the point.

**Three that broke the core claim.**

1. **The approval gate failed open between runs.** Approve a rollback once; start
   a second run without resetting the bus, and it executed with nobody at the
   keyboard — recording `by: "human"` for a decision no human made. `GET
   /decision` matched on action name, and action names are not unique across
   runs. Fixed on the bus, which is the only place it works: the agent's own
   stale-id guard lives in process memory and is empty in a fresh process.
2. **The sandbox could read any file on the machine.** It blocked sockets,
   subprocesses and dangerous imports — but nothing stopped `open('.env').read()`.
   We proved it by doing it: a diagnostic handed back our **live TrueFoundry API
   key** in its own result, and could write anywhere on disk. Scrubbing the
   environment is not enough when the secrets are also on disk. Every
   path-opening entry point is now confined to the sandbox directory.
3. **The approval card was blank on every live run** — it read *"no rationale
   given"*. The rationale came only from prose in the same turn as the tool call,
   and GPT-4o fires the call with empty text, so a human was being asked to
   authorise a production rollback **with nothing to read**. It looked correct in
   simulation, because the scripted provider always narrates. `reason` is now a
   required parameter on every gated tool, with a fallback to the sandbox verdict.

**Three where the system claimed something untrue.** It blamed a human for a
denial no human made (a dead bus produced *"rejected by the on-call human"*). It
reported `executed` for an action that failed — approval is not execution. And a
test was passing for the wrong reason: `assert not ok` on a 4 GB allocation
passed because a bytearray is not JSON-serialisable, not because any limit fired.
macOS rejects every memory rlimit, so we corrected the *claim* rather than the
test.

**Plus** an environment that healed on *any* version string (rewarding a wrong
diagnosis), `/scale` accepting `-5` replicas, `limit=0` returning everything, XSS
via agent-written Python in the timeline, and a timeline with no scroll container
that pushed the approval button off-screen mid-run.

**Qodo found seven more** across pull requests — including a launcher that would
have auto-approved everything while the UI still claimed to be gating. All fixed
before merge.

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
| `ui/dashboard.html` | The dashboard: filterable timeline, approval card, before→after metrics |
| `ui/pitch.html` | Self-contained walkthrough — replays a **real** GPT-4o run event by event, and pauses for your approval. Open it straight from disk, no server needed |
| `tests/` | 227 tests |
| `CLAUDE.md` | The plan: layers, cut-lines, tracks |
| `TOOL_CONTRACT.md` / `EVENT_CONTRACT.md` | Frozen seams between the two workstreams |
| `PERSON_A_AGENT.md` / `PERSON_B_INTERFACE.md` | Per-owner briefs and live status |
| `fallback_agent.py` | The original single-file loop, kept as insurance |

## Status

**Demoable end to end with no API key**, via `--provider sim`: the mock loop, the
sandboxed diagnostic, parallel subagents, and session survival. The TrueFoundry
gateway is **live and verified** — full incident responses have run on GPT-4o
through it, reaching the same 0.76 correlation and the same recovery.

Layer 2 (a real GitHub revert PR) is written, hardened and tested, but has not
been fired against a live repo. It stays dry-run until `GITHUB_REVERT_ENABLED=1`,
and it goes through the `gh` CLI rather than MCP.

**Known limits, stated rather than buried.** There is no memory cap on macOS —
it rejects `RLIMIT_AS`, `RLIMIT_DATA` and `RLIMIT_RSS` alike, so what protects
the parent is process isolation plus the wall-clock kill, not an address-space
limit. And a decision posted to the bus with no `request_id` still satisfies any
pending gate for that action; that is a deliberate trade so a stale cached
dashboard cannot deadlock a live run, and the shipped dashboard always sends a
real id.

`PERSON_A_AGENT.md` and `PERSON_B_AGENT.md` carry the full engineering ledger,
including every bug we found on ourselves and how.

## License / provenance
MIT — see [LICENSE](LICENSE). Open source, as the hackathon requires.
Mock telemetry is synthetic; no real production system is involved, and the
`.env` holding gateway credentials is gitignored and never committed.
