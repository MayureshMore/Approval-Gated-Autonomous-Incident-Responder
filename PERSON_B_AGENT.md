# Person B — Progress & Handoff (owner: Zeel)

This file is a living status log for the Interface/Environment/Demo track.
Kept up to date after each milestone so work can resume in a fresh session
(new context window, different machine, different tool) without re-deriving
everything. Read PERSON_B_INTERFACE.md first for the mission/task list this
tracks against. **Read PERSON_A_AGENT.md too — it has a dedicated "Handoff to
Zeel" section at the bottom** listing exactly what changed on his side that
touches this one; most of it is now addressed (see B3 below).

## Repo / git status
- Remote: `origin` → https://github.com/MayureshMore/Approval-Gated-Autonomous-Incident-Responder.git
- Local git lives inside `incident-responder/`, matching the remote's root
  layout exactly (no extra nesting) — Person A flagged this as a thing to
  check; already correct on this side.
- Working tree fully synced with `origin/master` as of commit `bb3dd09`
  ("Session persistence (Layer 4), .env secrets, gateway diagnosis") —
  includes Person A's `agent/session.py`, `agent/env.py`,
  `agent/providers/truefoundry.py`, `.env.example`, and the new
  `EVENT_CONTRACT.md` addendum. `ui/dashboard.html` stays on Zeel's version
  (superset of remote — remote hasn't touched it). `.gitignore` = Person A's
  version + one added `*.log` line.
- Synced with Person A's work through `aa3dc9e` (PR #4, the blog), which
  includes the `Makefile`, `scripts/demo.sh`, `BLOG.md`, `LICENSE` and his two
  fixes to `ui/dashboard.html` (XSS escaping, approval card after a reset).

### ⚠️ `git push` on its own does nothing here — use `git push -u origin master`
Local `master` has **no upstream configured** (`git config --get-regexp
'^branch\.master'` returns nothing), so a bare `git push` can report
*"Everything up-to-date"* while leaving every local commit unpushed. This is a
leftover from wiring the repo with `git reset --mixed origin/master`, which
moves the branch pointer but never sets tracking.

**Push with `git push -u origin master`** — the `-u` sets the upstream once and
plain `git push` behaves normally afterwards. To confirm a push actually landed:

```bash
git fetch origin && git log --oneline origin/master..HEAD    # empty == pushed
```

Before starting new work in a future session: `git fetch origin && git log
origin/master -5` to check whether Person A has pushed again, and re-sync (see
this session's transcript for the `--mixed` reset + per-file
`git show origin/master:<path> > <path>` pattern) before making further changes.

## Environment notes (session-specific — may not apply elsewhere)
- Windows machine. `python` alias is NOT on PATH (opens MS Store); use `py`.
- Port 8000 is occupied by an unrelated `httpd` process on this machine (not
  part of this project). **mock_env runs on port 8001 instead**, via
  `MOCK_ENV_URL=http://localhost:8001` — every command below needs that env
  var on this machine specifically.
- No headless-browser tool (`chromium-cli`, Playwright) is installed. UI
  changes are verified functionally: Node scripts in the session scratchpad
  load the dashboard's real inline `<script>`, stub minimal DOM, and execute
  `render()` etc. against real or synthetic event payloads. This catches
  runtime errors and confirms computed values, but **not pixels** — a real
  look in a browser at tablet width is still outstanding.
- MSYS/git-bash on Windows mangles `git show origin/master:path` (colons get
  turned into semicolons) unless prefixed with `MSYS_NO_PATHCONV=1`. Bit twice
  by this — prefix any `git show <ref>:<path>` with it.

## Status by task (PERSON_B_INTERFACE.md order)

- **B1 — Run it: DONE, verified three times.** Latest: fresh
  `PYTHONPATH=. MOCK_ENV_URL=http://localhost:8001 AGENT_BUS_URL=http://localhost:8500
  py run_agent.py --provider sim --subagents` against the *updated*
  `approval_server.py` (see B3) — 3 parallel subagents, real sandboxed
  diagnostic (score 0.76), approval pause, approved via `POST /decision` with
  `request_id`, rollback executed, checkout-service healthy (v1.4.1, 0.4%
  error rate, 240ms p99). `run_agent.py --selftest` → PASS (once pointed at
  :8001). Full test suite: `PYTHONPATH=. py -m pytest -q` → **113/113 passed**
  (Person A's suite grew from 83 to 113 with the session-persistence work).

- **B2 — Polish dashboard: substantially done.** In `ui/dashboard.html`:
  - Run timer, before→after metrics delta card with sparklines, pulsing
    approval-card border, tablet breakpoint (900px) + bigger touch targets —
    all from the prior session, still in place and verified again this
    session against a fresh run.
  - Rendering for `subagent_started`/`subagent_finished` (shows `verdict`),
    `sandbox_exec` (code shown in a labeled box — Person A's explicit highest-
    priority ask), `error`, and now **`run_resumed`** (renders as a visually
    distinct timeline divider: "Run resumed at step N" + still-pending
    actions). Verified with a synthetic-event test
    (`scratchpad/verify_resume_and_resilience.js`) since no real resume was
    triggered this session — confirmed it renders correctly and the approval
    card correctly stays visible across the resume marker when the gated
    action is still undecided.
  - **Still open:** real visual/tablet check in an actual browser.

- **B3 — Event contract sync: addressed everything in Person A's handoff.**
  Working through his "Handoff to Zeel" section in `PERSON_A_AGENT.md` line by
  line:
  1. Repo layout — already correct (see Repo/git status above).
  2. **Duplicate `approval_decision` event — fixed properly this session.**
     Previously deduped by `action` name only (a earlier-session fix). Person
     A asked for `request_id`-based dedup instead, and pointed out the real
     underlying cause: the bus's own `/decision` handler never stored or
     echoed the `request_id` a decision was made for, so the agent's own
     stale-decision guard (`agent/approval.py`'s `_await_ui`) had nothing to
     check against. Fixed on **both sides**:
     - `approval_server.py`: `POST /decision` now stores and echoes
       `request_id`; `GET /decision` naturally returns it (already spreads
       the stored dict).
     - `ui/dashboard.html`: `sendDecision()` now takes and sends the pending
       approval's `request_id`; the approve/reject button handlers pass
       `pending.request_id` through; timeline dedup keys on `request_id` when
       present, falling back to `action` for the older `fallback_agent.py`
       path (which never sends one).
     Verified live: reset, ran a fresh real agent, approved with the real
     `request_id`, confirmed both the server-echoed and the agent's own
     `approval_decision` events carry the *same* `request_id`, and the
     dashboard correctly renders only one row for it.
  3. New event kinds (`sandbox_exec`, `subagent_started/finished`,
     `run_resumed`, `error`) — all rendered (see B2).
  4. Session-survival demo beat ("card must stay on screen when events stop
     arriving") — **found and fixed a real bug**: `fetchEvents()` returned
     `[]` on a total fetch failure, indistinguishable from a genuine empty
     event log, and `render([])` actively wiped the timeline and hid the
     approval card. Now `fetchEvents()` returns `null` on failure (distinct
     from a real empty array), and the poll loop skips `render()` entirely
     when that happens — prior on-screen state (including a pending approval
     card) survives a network blip or the agent process dying mid-run.
     Verified with a synthetic test simulating a failed fetch after an
     established state.
  5. Small bus ask (`GET /decision` echo `request_id`) — done as part of #2.
  6. Confirmed `mock_env/`, `approval_server.py` (now touched, appropriately),
     `ui/dashboard.html`, `DEMO_SCRIPT.md` — all still B's files as expected.
  **Nothing outstanding from Person A's list.**

- **B4 — Environment: no changes.** `mock_env/main.py` untouched; still
  matches TOOL_CONTRACT.md exactly (confirmed via live runs).

- **B5 — Qodo: blocked on Mayuresh — two things needed from him.**
  1. **Install the Qodo Merge GitHub App on the repo.** This requires repo
     *admin* permission — Zeel's GitHub token (`zeelpatel2`) has `push` but
     `admin: false` on `MayureshMore/Approval-Gated-Autonomous-Incident-Responder`
     (confirmed via `gh api repos/.../--jq .permissions`), so **only Mayuresh
     can do this**, or he needs to grant Zeel admin/maintainer first.
     - Real install links (verified via web search, not guessed):
       - Free, for open-source repos: https://github.com/marketplace/qodo-merge-pro-for-open-source
       - Paid tier: https://github.com/marketplace/qodo-merge-pro
     - Install flow: open the link → "Install" → pick the org/account
       (Mayuresh's) → choose "Only select repositories" → pick this repo →
       confirm permissions. Reviews start appearing on new PRs within minutes,
       no CI/CD config or API key needed for the managed App path.
  2. **Make the repo public first.** Checked via
     `gh api repos/.../--jq .private` → currently **`true` (private)**. Two
     reasons this needs fixing, not just for Qodo: (a) the free
     "for-open-source" Qodo tier requires a public repo, and (b)
     `CLAUDE.md` states outright **"Repo MUST stay open source"** as a
     hackathon rule — a private repo may not satisfy the judging criteria at
     all, independent of Qodo. Flag this to Mayuresh alongside the Qodo ask;
     making it public also needs repo admin, so it's the same conversation.
  Once both are done: open a small real PR (even a trivial one) to confirm
  Qodo actually comments on it, fix whatever it flags, then merge — that's
  what B5's "run every PR through it, fix findings before merge" means in
  practice. Nothing else for B5 can proceed until the repo is public and the
  app is installed.

  **Update, same session:** repo is now public (confirmed via
  `gh api .../--jq .private` → `false`). Mayuresh said he installed Qodo, so
  we opened a real test PR to confirm:
  **https://github.com/MayureshMore/Approval-Gated-Autonomous-Incident-Responder/pull/1**
  ("Dashboard: show which provider ran the agent" — a small real feature,
  branch `feature/dashboard-provider-badge`, not a throwaway). **After 15+
  minutes, zero activity**: `gh api .../issues/1/comments` → 0, `.../pulls/1/reviews`
  → 0, `gh pr checks 1` → "no checks reported", timeline shows only the
  `committed` event. This strongly suggests the GitHub App either wasn't
  actually selected for *this* repo during install (easy to pick the wrong
  repo in the picker), or was installed on a different account than
  `MayureshMore`, or the webhook isn't firing for some other reason.
  **Second update, same session — Mayuresh confirmed the repo IS listed under
  the Qodo installation, re-checked anyway, still zero activity.** Re-ran the
  same checks (`gh api .../issues/1/comments` → 0, `.../pulls/1/reviews` → 0,
  `gh pr checks 1` → "no checks reported", timeline still only shows
  `committed`) *after* Mayuresh confirmed repo access was correct. So it's
  **not** a repo-selection mistake — something else is broken: could be the
  webhook delivery failing silently, the app's PR-review trigger not firing
  on this account/plan tier, a delay far longer than the "minutes" the docs
  promise, or an app-side issue unrelated to anything either of us configured.
  **Decision: deprioritized per Zeel's call.** Not spending more time
  debugging a third-party app's webhook from the outside with no visibility
  into its logs. **What Mayuresh should check when he has a minute** (since he
  has the admin access this needs): GitHub repo Settings → Webhooks → look for
  a Qodo/PR-Agent webhook entry and check its "Recent Deliveries" tab for
  failed deliveries with an error code — that's the one piece of diagnostic
  info neither of us can see from outside. PR #1 stays open for whenever it's
  revisited; no need to open a new one. **Everything else in this doc
  proceeded without waiting on this.**

- **B6 — Demo: script updated, not yet recorded.** Updated `DEMO_SCRIPT.md`
  this session: added the exact copy-pasteable start command next to
  "[start the run]", a callout that three subagents run in parallel, and a
  full new "Bonus beat — session survival (Layer 4)" section with the
  narration + exact commands for the kill/list-runs/resume sequence —
  written from the real verified run above, not speculatively. Also added
  "session survival" to the Harness/DGX talking points. Actual video/screen
  recording still needs to happen — that's on Zeel (needs a screen + mic,
  which I can't do from here). Person A's advice stands: record the
  base flow first since `--provider sim` is a reliable, API-key-free green
  run right now; the resume beat is a strong optional closer if there's time
  in the 90s target.

- **B7 — Blog: NOT STARTED.**

## QA pass — clean setup + adversarial testing (2026-08-29)

Set the project up from scratch in a fresh `.venv` on Windows/Python 3.13 and
tested it as an outsider would. Suite went **136 → 150 passing**. Five real
defects found and fixed; everything below was reproduced before being changed.

### What was fixed, at a glance
| # | Fix | Severity | Where | Commit |
|---|---|---|---|---|
| 1 | Approval gate failed **open** across runs — a second run executed a rollback with no human, logged as `by:"human"` | **critical** | `approval_server.py` | `835f5b6` |
| 2 | Dashboard stuck on INVESTIGATING after a rejected run | demo-visible | `ui/dashboard.html` | `d392103` |
| 3 | "Resolved" banner leaked into the next run after `/reset` | demo-visible | `ui/dashboard.html` | `d392103` |
| 4 | Environment healed on **any** rollback version, incl. the bad one | correctness | `mock_env/main.py` | `3c0b8e0` |
| 5 | `scripts/demo.sh` unrunnable in Windows/WSL clones (CRLF) | portability | `.gitattributes` | `96e99a6` |
| + | Bus returned HTTP 500 on bad JSON; accepted a decision with no `action` | robustness | `approval_server.py` | `835f5b6` |

Tests added: `tests/test_approval_server.py` (11, incl. the fail-open
regression — confirmed to fail against the pre-fix code), 3 in
`tests/test_contracts.py`, and 2 in `tests/test_demo_script.py` made
cross-platform.

### 1. Approval gate failed OPEN across runs — *the most serious finding*
Approve a rollback, then start a second run **without** resetting the bus, and
the new run executed the rollback **with nobody at the keyboard**, recording
`by: "human"` for a decision no human made. Directly contradicts the project's
headline claim. Reproduced end to end, twice.

*Cause:* `GET /decision` matched on **action name only**. Action names are not
unique across runs, so the previous run's approval satisfied the new run's gate.
The agent does carry a stale-id guard (`ApprovalGate._seen`), but that set lives
in process memory and is empty in a freshly started process — so the check had
to move to the bus to be worth anything.

*Fix:* `approval_server.py` now only hands a decision back to the gate it was
actually made for (matching on `request_id`). Callers that send no id (the older
`fallback_agent.py` loop) are answered as before. Re-ran the exact scenario: the
second run now pauses and waits, production untouched. Locked down by
`tests/test_approval_server.py::test_a_decision_is_not_reused_by_a_later_gate` —
verified it **fails** against the old code, so it is a real regression test.

### 2. Dashboard stuck on "INVESTIGATING" after a rejected run
The only terminal-status branch required `status === "healthy"`, so a rejected
run — one of the five headline claims — left the badge reading INVESTIGATING
forever while the agent had already stood down. Now shows **STOOD DOWN** with an
explanation that production was deliberately left untouched, plus a neutral
FINISHED state for any other ending.

### 3. "Resolved" banner leaked across runs
`resolved` had `.show` added but never removed, so after a `/reset` the previous
run's green "✓ Resolved" banner stayed on screen while the next run was still
investigating. Same stale-state family as the approval-card bug Mayuresh fixed
in section 9 of `PERSON_A_AGENT.md`; the banner is now set explicitly on every
render. Verified with two consecutive runs and a reset between them.

### 4. The environment rewarded a *wrong* rollback
`test_restart_does_not_fix_a_bad_deploy` guards the choice of **tool**, but
nothing guarded the choice of **version**: `/rollback` healed the service for any
string at all. Rolling back to `v9.9.9-never-shipped`, or to the bad deploy
`v1.4.2` itself, both reported **healthy** — quietly rewarding a wrong diagnosis
and hollowing out "the scenario forces real reasoning". `mock_env` now only heals
on a version that exists in deploy history and differs from the running one, and
returns an actionable message otherwise. Three tests added to
`tests/test_contracts.py`. The demo path (`v1.4.1`) is unchanged.

### 5. `scripts/demo.sh` was broken in any Windows/WSL clone
Git for Windows defaults to `core.autocrlf=true`, so the launcher checked out
with CRLF, and bash cannot parse a CRLF script (`syntax error near unexpected
token $'do\r'`). Proved it with a fresh `git clone`. Added `.gitattributes`
pinning `*.sh` and `Makefile` to LF. macOS/Linux were never affected — which is
exactly why it went unnoticed.

Also hardened `approval_server.py` inputs: malformed JSON returned **HTTP 500**
(unhandled traceback) and `POST /decision` with no `action` was silently accepted
and recorded under the key `None`, rendering as an approval belonging to no gate.
Both now return 400.

### 6. Text file I/O used the locale codec, not UTF-8 (found after PR #5)
`tests/test_dashboard.py` failed with **13 UnicodeDecodeErrors** on Windows:
`open(DASHBOARD)` with no `encoding=` falls back to the locale codec — cp1252
here — and the dashboard is UTF-8 (the header emoji). Same shape as the CRLF
bug: invisible on macOS, fatal on Windows.

Swept the repo and gave every text-mode `open()` an explicit `encoding="utf-8"`
(`agent/bus.py`, `core.py`, `env.py`, `registry.py`, `session.py`,
`fallback_agent.py`, three test modules). Most were latent rather than broken —
their non-ASCII happens to be cp1252-compatible — but `agent/bus.py` and
`agent/session.py` write JSON, which is UTF-8 by definition, and a single `✓` in
an agent message is enough to raise `UnicodeEncodeError` mid-run (confirmed by
probe). Suite went **202 passed + 13 errors → 215 passed**.

### Verified working (no change needed)
- **Mayuresh's XSS fix holds.** Injected `<img src=x onerror=...>` into every
  model-controlled field: zero live payloads in the HTML sink, 17 correctly
  escaped, and the `if n<len(` row-corruption case neutralised.
- **Rejection path**: production untouched, `destructive_executed: []`, agent
  explicitly stands down.
- **Layer 4 resume works on Windows**: killed at the gate, `--list-runs` showed
  `step=6 pending=['rollback_service']`, `--resume last` continued with **zero**
  re-investigation (1 sandbox run total across kill+resume).
- **Contract conformance**: every `TOOL_CONTRACT.md` endpoint returns the
  documented shape; `restart`/`scale` still cannot fix the incident.
- **`scripts/demo.sh` end to end** (with `MOCK_PORT`/`PYTHON` overrides): starts
  both servers, resets, runs subagents, pauses, serves the dashboard, accepts the
  approval, recovers the service. Its port-in-use and `AUTO_APPROVE`-stripping
  guards both fire correctly.
- Dashboard survives malformed events without throwing (skips them).

### Open, for Mayuresh — two things I could not settle from here
1. **Launcher cleanup on Ctrl-C is unverified, not broken.** I could not test the
   `trap cleanup EXIT INT TERM` honestly on Windows: a backgrounded bash under
   msys has no controlling terminal, so it never gets a real Ctrl-C, and
   `Stop-Process -Force` is a hard kill that by design skips traps. Worth a
   10-second check on the Mac you will demo from: `make demo`, Ctrl-C at the
   approval card, then `lsof -ti:8000 -ti:8500` should print nothing.
2. **`SessionStore.save()` checkpoint failures on Windows** — intermittent
   `[WinError 5] Access is denied` on the `os.replace(tmp, target)`, ~4 times
   across my runs, 0 in others. The atomic-write pattern is right; on Windows a
   sync client or AV briefly holding the target makes `os.replace` fail, and this
   repo lives in a OneDrive folder. It is caught and logged rather than fatal, and
   resume still worked every time I tested it, so I did **not** patch your file —
   a short retry around the replace would close it if you think it is worth it.
   macOS will not hit this.

## Next steps (in priority order)
1. **Record the demo (B6)** — script is ready (`DEMO_SCRIPT.md`, includes the
   resume beat), the flow is fully verified twice over. This is the top
   priority now that Qodo is parked.
2. Manually eyeball the dashboard in a browser at tablet width — the one
   verification this session's tooling can't do.
3. Draft blog (B7).
4. **Qodo (B5), deprioritized, not forgotten:** whenever Mayuresh has a
   minute, ask him to check GitHub repo Settings → Webhooks → the Qodo
   webhook's "Recent Deliveries" for a failure code — that's the only
   diagnostic neither of us can see from outside. Re-check PR #1 once
   there's something to check.

## Layer 4 resume demo — verified for real this session (not just synthetic)
Ran the full sequence Person A documented, for real, end-to-end:
1. Reset, started `run_agent.py --provider sim --subagents`, let it reach
   `awaiting_approval` (real sandboxed diagnostic ran once, score 0.76).
2. Killed the actual OS process (found via
   `Get-CimInstance Win32_Process -Filter "Name='py.exe' or Name='python.exe'"`
   since backgrounded jobs don't persist as shell jobs across separate tool
   calls in this environment — matched on the full command line to avoid
   hitting the two long-running demo servers). Confirmed the bus's `/events`
   was completely unaffected (still 22 events, last one `awaiting_approval`)
   — approval_server is a separate process, so killing the agent can't touch
   it.
3. `run_agent.py --list-runs` → `f22a7979 running step=6 provider=sim
   pending=['rollback_service']`, exactly as documented.
4. `run_agent.py --provider sim --resume last` → emitted `run_resumed
   {step:6, pending:['rollback_service']}`, then a **new** `awaiting_approval`
   for the same action with a **different `request_id`** than the pre-kill
   one. Confirmed only one `sandbox_exec`/`run_diagnostic` total across the
   whole kill+resume — no re-investigation, exactly as claimed.
5. Approved using the current (post-resume) `request_id` — both resulting
   `approval_decision` events (bus + agent) carried that same current id, the
   rollback executed, checkout-service ended healthy. This is the live
   version of the exact stale-decision scenario the `request_id` guard exists
   for, and it worked correctly.
6. Re-ran the dashboard's real `render()` against this actual 32-event
   stream (not a synthetic one) — `run_resumed` divider renders correctly,
   dedup correctly collapsed the duplicate `approval_decision` pair, delta
   card/sparklines/timer all correct, zero thrown errors.
**This demo beat is ready to record as-is.**

## Files touched in the QA pass
- `approval_server.py` — stale-decision fail-open fix (`request_id` matching);
  400s instead of a 500 / silent `None` key on bad input.
- `ui/dashboard.html` — STOOD DOWN / FINISHED terminal states; banner no longer
  leaks across runs.
- `mock_env/main.py` — `/rollback` only heals on a known, different version.
- `tests/test_approval_server.py` (new) — 11 tests for the bus, including the
  fail-open regression.
- `tests/test_contracts.py` — 3 tests for wrong-version rollbacks.
- `tests/test_demo_script.py` — exec-bit check now asserts on git's tracked mode
  (catches "set locally, never committed", which the old check missed) and the
  bash syntax check runs repo-relative so it works on every platform.
- `.gitattributes` (new) — pins `*.sh` and `Makefile` to LF.

## Files touched this session
- `DEMO_SCRIPT.md` (this pass) — added the exact start command, a subagents
  callout, and the full "Bonus beat — session survival" section with the
  verified resume-demo narration/commands; added "session survival" to the
  Harness/DGX talking points.
- `ui/dashboard.html` (on branch `feature/dashboard-provider-badge`, PR #1) —
  small provider badge in the header (shows `sim`/`truefoundry`/etc. from
  `run_started.provider`), opened as the Qodo test PR.
- `approval_server.py` — `request_id` storage/echo fix (B3.2/B3.5).
- `ui/dashboard.html` — `run_resumed` rendering, `request_id`-based dedup,
  `sendDecision`/button wiring for `request_id`, fetch-failure resilience fix.
- `.env.example`, `agent/env.py`, `agent/providers/truefoundry.py`,
  `agent/session.py`, `tests/test_session.py`, `tests/test_truefoundry.py`,
  and updates to `CLAUDE.md`, `PERSON_A_AGENT.md`, `README.md`,
  `agent/bus.py`, `agent/core.py`, `agent/providers/{anthropic,base,openai,sim}.py`,
  `agent_prompt.md`, `fallback_agent.py`, `run_agent.py`, `tool_schemas.py` —
  all straight syncs from `origin/master`, no manual edits.
- `scratchpad/verify_dashboard.js`, `scratchpad/verify_resume_and_resilience.js`
  — throwaway verification scripts, not part of the repo.
