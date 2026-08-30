# Demo script — the 3-minute submission video

The submission form requires a video, **max 3 minutes**, covering: about the
project, tech stack and architecture, the demo, and (optionally) what we learned.
This script is timed to that. Read it out loud while you screen-record.

## Before you hit record — 60 seconds of setup

```bash
make demo-live          # real GPT-4o through the TrueFoundry gateway
```

It brings up both servers, waits until they actually answer, resets the scenario
so the run is deterministic, starts the agent, and cleans up on Ctrl-C.
Dashboard: **http://localhost:8500/**

- **Have the dashboard filling the screen.** That is the thing being judged.
- **`make demo` is the fallback** — deterministic `sim` provider, no network, no
  API key. It drives the same gate, sandbox and environment; only token
  generation is scripted. Nothing in the narration below changes, so you can
  switch mid-session without restarting your script.
- **Restart the servers after any code change.** uvicorn does not reload, and a
  stale server will show you the old behaviour.
- Reset by hand between takes if you are not using the launcher:
  `curl -X POST localhost:8000/reset && curl -X POST localhost:8500/reset`

---

## [0:00 – 0:25] About the project

> "This is Ripcord — an AI on-call engineer.
>
> Every team now has agents that can *read* production. Almost none let an agent
> *touch* it, because the failure mode isn't a wrong answer — it's a deleted
> database at 2 AM. So you get read-only agents that page a human anyway, or
> unsupervised automation nobody will approve.
>
> The bottleneck isn't model capability. It's trust. Ripcord investigates an
> incident, diagnoses it with code it writes itself, and then stops and asks a
> human before doing anything irreversible."

## [0:25 – 0:55] Tech stack and architecture

> "Every model call routes through the **TrueFoundry AI Gateway**, running GPT-4o,
> traced per run so this run maps to its own token spend and latency.
>
> On top of that we built the harness layer: a **sandbox** that executes the
> agent's own Python in an isolated process, an **approval gate that fails
> closed**, three **parallel subagents**, and **session survival** across process
> death. 227 tests. Every pull request went through **Qodo** before merge."

## [0:55 – 2:15] The demo — the part that matters

Start the run. Narrate what appears:

> "A critical alert just fired on checkout-service — 34% error rate, 4.2 second
> p99 latency.
>
> Three subagents sweep metrics, logs and deploys in parallel.
>
> Now the part that matters: it doesn't guess. It **writes a diagnostic and runs
> it in a sandbox** to score the correlation. Deploy v1.4.2 shipped **12 minutes**
> before the alert and is named in the error logs — correlation **0.76**. That
> number was computed, not estimated.
>
> So it proposes rolling back to v1.4.1. And notice — **it stopped and asked
> me.**"

**Pause here. Read the rationale on the card out loud.** That is the whole
pitch in one screenshot: a human being given the evidence before authorising a
production change.

> "It will not touch production until a person approves. This is the point: the
> speed of automation, with a human on the one action that can't be undone."

[click **Approve**]

> "Rollback executes… and checkout-service is back to healthy. Error rate 0.4%,
> latency 240 milliseconds.
>
> That's 30 minutes of 2 AM investigation in about fifteen seconds — and it asked
> before the one step that could have made things worse."

## [2:15 – 2:50] Learning and growth — do not skip this

This is the strongest 35 seconds in the video. Judges have seen a hundred happy
paths; almost nobody shows them the bugs.

> "Three bugs we found on ourselves, and every one of them passed our test suite.
>
> The approval gate **failed open between runs** — approve a rollback once, and
> the next run executed with nobody at the keyboard, recording it as a human
> decision.
>
> The sandbox **could read any file on the machine**. We proved it by doing it —
> a diagnostic handed back our live API key.
>
> And the approval card was **blank on every real run**. GPT-4o fires tool calls
> with no text, so a human was being asked to authorise a production rollback
> with nothing to read. It only looked right in simulation.
>
> All three are fixed, and each one is now a test."

## [2:50 – 3:00] Close

> "The speed of automation, with a human on the one action that can't be undone —
> and every safety property is a test, not a promise."

---

## Optional beat, only if you are under time

Session survival. Strong, but cut it before you go over 3 minutes.

> "Watch what happens if I lose the agent entirely."

[kill the agent process at the approval card] → `make runs` shows the checkpoint,
step 6, still waiting → `make demo-resume`

> "It picks up exactly where it left off — same diagnosis, no second sandbox run.
> And it asks again, because killing the process is not the same as answering
> yes."

---

## What to say per track

- **TrueForge / DGX — read this twice, one judge is a TrueFoundry FDE.**
  NEVER say "the harness runs the loop, we didn't rebuild any of it." That is
  false and checkable in one `grep` of `requirements.txt`. We use the TrueFoundry
  **AI Gateway** — an OpenAI-compatible model router: every call routed,
  authenticated and traced, tagged `X-TFY-METADATA` per run, with a preflight
  that validates the model id against the gateway's catalogue before the run
  starts. The harness layer above it — sandbox, approval gate, subagents, session
  survival — we built, against frozen contracts, with tests.
  Say it like this: *"Every model call routes through the TrueFoundry gateway,
  traced per run. The harness layer on top we implemented to a frozen contract,
  and each piece is backed by tests you can run right now."* Then run `pytest`.
  227 passing in seven seconds is a stronger moment than any claim about whose
  code it is.
- **Best UI:** "A stranger can drive this. It shows what the agent is doing, what
  it's waiting on, and what it did — and it asks before the irreversible step,
  right in the interface." Mention the filter chips and the `A`/`R` keyboard
  shortcuts if you have a spare beat.
- **Qodo:** "Every PR went through Qodo. It found seven real bugs — including a
  launcher that would have auto-approved everything while the UI still claimed to
  be gating — and we fixed all of them before merge."
- **Do not claim the GitHub revert PR is live.** It is written and tested but
  dry-run, and it goes through the `gh` CLI, not MCP. If asked, say exactly that;
  a judge can read `integrations/github_ops.py` in thirty seconds.
- **We do not use Bright Data at all.** Do not enter that track.

## Failure insurance

- **`ui/pitch.html`** opens from disk with no server, no network and no API key,
  and replays a real captured gateway run that pauses for approval. If the live
  demo wedges, screen-record that instead — it is still a real run.
- `--provider sim` needs no network, so dead venue wifi does not stop you. That
  is what `make demo` uses.
- The mock env is deterministic and `/reset`-able, so the demo is repeatable —
  rehearse the narration once against a real run before the take.
- If everything fails, `AUTO_APPROVE=1 .venv/bin/python fallback_agent.py` still
  runs the original single-file loop.
