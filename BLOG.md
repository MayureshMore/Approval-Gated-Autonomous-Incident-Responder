# We built an AI on-call engineer that asks permission

*Ripcord — Agent Harness Hackathon, one day, two people.*

The pitch is one sentence: an agent that investigates a production alert on its
own, works out what broke, and then **stops and asks a human before touching
anything**.

The interesting part wasn't making it work. It was everything that tried to make
it *look* like it worked while quietly not.

---

## What it does

A critical alert fires: `checkout-service`, 34% error rate, p99 at 4200ms. The
agent picks it up and, in about twenty seconds:

1. **Three subagents sweep in parallel** — metrics, logs, deploys — and their
   findings merge into one evidence pack.
2. **It writes a diagnostic and runs it in a sandbox.** Not a canned tool call:
   the model authors Python, and we execute it in an isolated process.
3. **It forms a diagnosis** — deploy `v1.4.2` shipped 12 minutes before the
   alert and is named in the error logs.
4. **It proposes a rollback — and stops.** The run blocks. A card appears in the
   dashboard with the action, the arguments, and the reasoning.
5. You click **Approve**. The rollback executes. Error rate drops to 0.4%.

The scenario is built so a restart *cannot* fix it. Only correctly correlating
the bad deploy and rolling back works. That's deliberate — it forces real
reasoning instead of rewarding a lucky guess, and there's a test that fails if
anyone ever makes a restart sufficient.

---

## The design decision everything else hangs off

**Fail closed.**

The approval gate has four ways to not get an answer: the request times out, the
approval bus is unreachable, the operator hits Ctrl-C, or there's no terminal to
prompt in. Every one of them **denies**.

There is no path in the code where "we couldn't ask" becomes "go ahead". That
sounds obvious written down. It is not what you get by default — the natural
shape of a polling loop is to keep going when the poll fails, and the natural
shape of a timeout is to fall through.

A rejection is also final. The agent doesn't retry, and it doesn't quietly
substitute a different destructive action. It stands down and says so.

---

## The sandbox is a real boundary, not a claim

Anyone can say "we run the code in a sandbox." We wanted it to be checkable, so
each property is a test:

- the network is dead — `socket` won't import
- `subprocess` won't import, and `os.system` is gone
- `os.listdir('.')` sees exactly one file: the diagnostics module
- `OPENAI_API_KEY` does not exist in the environment
- an infinite loop is killed by a wall-clock timeout
- a 4GB allocation dies in the child, and the parent is still standing

Fifteen tests hold that line. When a judge asks "is it really sandboxed?", the
answer is `pytest tests/test_sandbox.py`, not a paragraph.

---

## The moment that made it feel real

We built a reference diagnostic — correlate deploy timing against alert time,
check whether the error logs name the version, produce a score.

On the first live run through the gateway, GPT-4o ignored it. It wrote its own:
parsed the timestamps with `datetime`, computed `time_to_alert_minutes = 12.0`,
and returned `highly_correlated: true`. We executed that, and it cited the result
in its diagnosis.

That's the difference between a demo and a system. The agent wasn't replaying our
snippet with different numbers — it decided what to compute.

---

## Then we killed it mid-incident

Session survival was the cheap extra we almost cut. It turned out to be the
feature that exposed the most.

The agent checkpoints after every model turn and every tool result. So you can
kill the process while it's parked at the approval gate, run `--resume last`, and
it picks the pending rollback back up at step 6 — no re-investigation, no second
sandbox run, same `run_id`, one continuous timeline in the dashboard.

The property we cared about most: **resume does not weaken the gate.** There's a
test that re-runs the whole "nothing destructive without approval" invariant on a
resumed run, and another that proves a denial recorded *before* the crash
survives the restart. You can't turn a "no" into a "yes" by killing the process
and trying again.

---

## What broke

### The invariant that had to be a test, not a promise

The core claim — "nothing destructive happens without a human" — is worth exactly
as much as your ability to demonstrate it. So it's a test that walks the event
stream in order and fails if a destructive `tool_call` ever appears before an
`approval_decision(approved=true)` for that action.

It has caught real regressions. It's also the thing we point at when someone asks
how we know.

### The dashboard bug that would have killed the demo

The approval card is the entire pitch. It stopped appearing.

`decided` was a module-level `Set` in the dashboard, accumulated across renders
and never cleared. After one approval, `!decided.has(action)` stayed false for the
life of the browser tab. And the demo script tells you to `POST /reset` before
every run.

So: rehearse once, reset, do the real take — **no approval card at all.** The
money shot, gone silently, with nothing in any log to explain it.

We found it by replaying the page's own logic in a script, not by staring at the
code. Fixed by deriving `decided` from the event log each render and keeping a
separate optimistic set, keyed by request id, purely for the instant hide on
click.

### The agent writes Python. Python is full of `<`.

Timeline rows interpolated tool arguments into HTML unescaped. Normally a
nitpick. But `run_diagnostic`'s arguments carry *the code the agent wrote*.

`if n<len(deploys):` makes the browser parse `<len(deploys):</code>` as a tag and
swallow the rest of the row. A model-chosen `to_version` of
`<img src=x onerror=alert(1)>` executes.

One of our real runs already contained `time_to_alert < 15`. It survived only
because of the space before the digit.

### The one where the deadline nearly won

Right at the end we added a one-command demo launcher, because typing six
commands into three terminals in front of judges is how demos die.

It had five bugs. We know because this was the first PR **Qodo** actually
reviewed — and it found all five. Every one was real, and we reproduced each
before fixing:

1. The launcher **inherited `AUTO_APPROVE=1`**, so if you'd ever set it for a
   recording, every destructive action would self-approve *while the dashboard
   still showed an approval card*. The exact failure mode the whole project
   exists to prevent, introduced by the convenience script.
2. `"${@:---provider sim --subagents}"` expands to **one argv element**, so the
   documented no-argument invocation was broken. `make demo` worked only because
   it passes arguments explicitly.
3. The port guard used `nc`, which isn't a declared prerequisite — and
   `! missing_command` returns *success*, so every port read as free.
4. `demo-resume` forced the `sim` provider, which would restore a gateway run's
   conversation into the wrong implementation.
5. Ctrl-C kills the approval server too, so a resumed run met a bus with **empty
   memory** — the dashboard would start at `run_resumed` with no history,
   destroying the exact continuity that feature exists to show. We'd verified
   session survival twice and never hit it, because we'd only ever killed the
   *agent*, never the launcher.

Number 5 is the one that stings. We'd tested the feature. We just hadn't tested
the way we were about to demo it.

Worth saying how close we came to not having that review at all. Qodo sat silent
for twenty-five minutes across two PRs with the GitHub App confirmed installed,
and we nearly dropped the track. Installing the App is only *half* the setup —
you also have to connect a Git provider inside the Qodo dashboard, and without
that its backend never processes the webhook. Once connected it responded in ten
seconds. Every PR after that went through it, findings fixed before merge.

### The bug we only found by rehearsing the demo

The last thing we did was run the whole thing from a fresh clone, as a judge
would. Most of it passed. Then this:

Our pitch says *"it writes a diagnostic and runs it in a sandbox to score the
correlation: 0.76."* On the live gateway that **never happened**. The tool
description named the helper functions but never gave their **signatures**, so
the model guessed — three failed attempts in one run:

```
deploy_times=...          → unexpected keyword argument
(positional, positional)  → missing required argument 'error_logs'
(..., error_logs=[...])   → TypeError: fromisoformat: argument must be str
```

It then fell back to log evidence and finished the run *successfully*. That is
why this survived every earlier test: the run worked, the headline feature
didn't. Fixed by generating the signatures from the source with `ast` — never
importing the sandbox module into the parent process — plus a worked example,
with a test asserting that example runs verbatim in the sandbox.

Worse was hiding behind it. With subagents enabled, the model passed a
subagent's findings *summary* where a list of log lines belonged. Iterating a
dict yields its keys, so nothing matched the version and the incident scored
**0.36 instead of 0.76** — no exception, just a plausible wrong number on screen
while you narrate the right one. A wrong answer that looks right is a worse
failure than a crash.

We had fifteen tests proving the sandbox was real. Every one of them ran *our*
reference snippet, never the model's.

---

## What we'd tell someone starting this tomorrow

**Make every claim checkable.** "It's sandboxed", "it always asks", "it resumes
safely" — each of those is a sentence in a pitch and a test in the repo. The
tests are what let you say them without hedging.

**Test the path you'll actually demo.** Our worst bug survived two rounds of
verification because we tested the feature and not the workflow.

**Convenience code is where safety regressions hide.** The launcher's job was to
reduce risk. It shipped with a bug that would have bypassed the approval gate
entirely.

**Write the fallback first.** There's a deterministic provider that needs no API
key and drives the *real* gate, the *real* sandbox and the *real* environment —
only token generation is scripted. It's how the tests run in CI, and it means a
dead network can't take the demo with it.

---

## Try it

```bash
git clone https://github.com/MayureshMore/Approval-Gated-Autonomous-Incident-Responder.git
cd Approval-Gated-Autonomous-Incident-Responder
make setup && make demo
```

Open http://localhost:8500/. No API key needed. Watch it stop and ask you.

MIT licensed. 161 tests.
