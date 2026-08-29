#!/usr/bin/env python3
"""
Ripcord — the incident responder, CLI entrypoint.

    python run_agent.py --provider truefoundry --subagents   # the primary runtime
    python run_agent.py --provider truefoundry --model ms-openai-main/gpt-4o-mini
    python run_agent.py --provider sim                       # no API key needed
    python run_agent.py --selftest                           # verify wiring, run nothing
    python run_agent.py --resume last                        # continue a killed run

Providers
  truefoundry  PRIMARY. TRUEFOUNDRY_API_KEY (+ TRUEFOUNDRY_BASE_URL,
               TRUEFOUNDRY_MODEL). OpenAI-compatible gateway; every call is
               routed and traced by TrueFoundry.
  sim          deterministic scripted run. Demo insurance + CI. Needs no key.
  openai       OPENAI_API_KEY    (MODEL, default gpt-4o)
  anthropic    ANTHROPIC_API_KEY (MODEL, default claude-sonnet-4-5)

Approval
  AGENT_BUS_URL=http://localhost:8500   approve in the dashboard  (demo path)
  unset                                 approve at the terminal
  AUTO_APPROVE=1                        unattended; stamped by="auto"
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.env import describe, load_dotenv

load_dotenv()   # secrets come from .env (gitignored), never from the command line

from agent.approval import ApprovalGate
from agent.bus import EventBus
from agent.core import IncidentAgent
from agent.registry import audit
from agent.session import RUNNING, SessionStore


def make_provider(name: str, run_id: str | None = None, model: str | None = None):
    if name == "truefoundry":
        from agent.providers.truefoundry import TrueFoundryProvider
        return TrueFoundryProvider(run_id=run_id, model=model)
    if name == "sim":
        from agent.providers.sim import SimProvider
        return SimProvider()
    if name == "openai":
        from agent.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(model=model)
    if name == "anthropic":
        from agent.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(model=model)
    raise SystemExit(f"unknown provider '{name}'")


def preflight(require_env: bool = True) -> tuple[list[str], list[str]]:
    """
    Check what would break BEFORE we are standing in front of judges.

    Returns (problems, not_started). The split matters: "the code is wrong" and
    "you haven't started the servers yet" are completely different situations,
    and collapsing both into FAIL means the check you run to reassure yourself
    before a demo is the thing that scares you.
    """
    problems: list[str] = []
    not_started: list[str] = []

    a = audit()
    for key in ("missing_impl", "gated_without_impl"):
        if a[key]:
            problems.append(f"registry: {key} -> {a[key]}")

    from sandbox.runner import run_python
    probe = run_python("RESULT = 2 + 2")
    if probe.get("result") != 4:
        problems.append(f"sandbox not executing: {probe.get('error')}")
    if not run_python("import socket").get("error"):
        problems.append("sandbox network isolation is NOT active")

    if require_env:
        import requests

        import tools
        try:
            if not tools.get_active_alerts():
                problems.append(
                    f"mock env at {tools.BASE_URL} has no active alerts — "
                    f"run: curl -X POST {tools.BASE_URL}/reset")
        except requests.exceptions.ConnectionError:
            # Nothing listening: a setup step, not a defect.
            not_started.append(
                f"mock env is not running at {tools.BASE_URL} — start it with "
                f"`make demo`, or: PYTHONPATH=. .venv/bin/uvicorn mock_env.main:app --port 8000")
        except Exception as exc:
            problems.append(f"mock env at {tools.BASE_URL} is misbehaving: "
                            f"{type(exc).__name__}: {exc}")

    return problems, not_started


def main() -> int:
    p = argparse.ArgumentParser(description="Ripcord incident responder")
    p.add_argument("--provider", default=None,
                   choices=["truefoundry", "sim", "openai", "anthropic"],
                   help="default: the checkpoint's provider when resuming, else "
                        "$PROVIDER, else sim")
    p.add_argument("--subagents", action="store_true",
                   help="fan out parallel read-only investigators first")
    p.add_argument("--github", action="store_true",
                   help="register the real revert-PR tool (Layer 2)")
    p.add_argument("--model", help="override the model id "
                   "(e.g. ms-openai-main/gpt-4o); otherwise taken from .env")
    p.add_argument("--max-steps", type=int, default=16)
    p.add_argument("--approval-timeout", type=int, default=300)
    p.add_argument("--selftest", action="store_true", help="run preflight and exit")
    p.add_argument("--json", action="store_true", help="print the run report as JSON")
    p.add_argument("--list-models", action="store_true",
                   help="list the models this TrueFoundry gateway exposes, then exit")
    p.add_argument("--resume", metavar="RUN_ID",
                   help="continue a killed run ('last' picks the newest unfinished one)")
    p.add_argument("--list-runs", action="store_true", help="show saved runs, then exit")
    p.add_argument("--no-persist", action="store_true",
                   help="do not checkpoint this run to runs/")
    args = p.parse_args()

    store = SessionStore()

    if args.list_runs:
        runs = store.list_runs()
        if not runs:
            print("no saved runs")
        for r in runs:
            pending = f"  pending={r['pending']}" if r["pending"] else ""
            print(f"{r['run_id']}  {r['status']:<9} step={r['step']:<3} "
                  f"provider={r['provider']}{pending}")
        return 0

    if args.list_models:
        from agent.providers.truefoundry import list_models
        from agent.providers.truefoundry import preflight as tfy_preflight
        try:
            for m in list_models():
                print(m)
            return 0
        except Exception as exc:
            print(f"could not list models: {exc}")
            print(json.dumps(tfy_preflight(), indent=2))
            return 1

    # Resolve the checkpoint first: a resumed run's provider comes from the
    # checkpoint unless the operator explicitly overrides it. Restoring an
    # OpenAI conversation into the sim provider would silently continue from
    # sim's own turn state and diverge from the run being resumed.
    state = None
    if args.resume:
        run_id = store.latest() if args.resume == "last" else args.resume
        if not run_id or not store.exists(run_id):
            print(f"nothing to resume ({args.resume!r}). Try --list-runs.")
            return 1
        state = store.load(run_id)
        if state.get("status") != RUNNING:
            print(f"run {run_id} already finished with status={state.get('status')!r}")
            return 1
        saved = state.get("provider")
        if args.provider and saved and args.provider != saved:
            print(f"refusing to resume: run {run_id} was recorded with provider "
                  f"{saved!r}, but --provider {args.provider!r} was given. Its saved "
                  f"conversation is not portable between providers.")
            return 1
        if not args.provider:
            args.provider = saved
        print(f"Resuming {run_id} from step {state.get('step')} on {args.provider} "
              f"(pending: {[c['name'] for c in state.get('pending_calls', [])] or 'none'})")

    if not args.provider:
        args.provider = os.environ.get("PROVIDER", "sim")

    problems, not_started = preflight(require_env=True)
    if args.provider == "truefoundry":
        from agent.providers.truefoundry import preflight as tfy_preflight
        if args.model:
            os.environ["TRUEFOUNDRY_MODEL"] = args.model
        tfy = tfy_preflight()
        if not tfy["ready"]:
            problems.append(f"truefoundry gateway: {tfy.get('error')}")

    if args.selftest:
        report = {"registry": audit(), "problems": problems, "not_started": not_started,
                  "config": {n: describe(n) for n in (
                      "TRUEFOUNDRY_API_KEY", "TRUEFOUNDRY_BASE_URL", "TRUEFOUNDRY_MODEL",
                      "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGENT_BUS_URL",
                      "GITHUB_REPO", "GITHUB_REVERT_ENABLED")}}
        if args.provider == "truefoundry":
            report["truefoundry"] = tfy
        print(json.dumps(report, indent=2))

        if problems:
            print("\nSELFTEST: FAIL — the build is wrong:")
            for pr in problems:
                print("  -", pr)
            return 1
        if not_started:
            # Wiring is sound; something just isn't up yet. Don't cry wolf.
            print("\nSELFTEST: OK (wiring verified) — but a service is not running:")
            for n in not_started:
                print("  -", n)
            return 0
        print("\nSELFTEST: PASS")
        return 0

    if problems or not_started:
        print("Preflight failed:")
        for pr in problems + not_started:
            print("  -", pr)
        return 1

    # A resumed run keeps its run_id, so the dashboard timeline is continuous.
    bus = EventBus(run_id=state["run_id"] if state else None)
    agent = IncidentAgent(
        provider=make_provider(args.provider, run_id=bus.run_id, model=args.model),
        bus=bus,
        gate=ApprovalGate(bus, timeout_s=args.approval_timeout),
        include_github=state.get("include_github", args.github) if state else args.github,
        max_steps=args.max_steps,
        use_subagents=args.subagents,
        session=None if args.no_persist else store,
    )
    report = agent.resume(state) if state else agent.run()

    print("\n" + "=" * 68)
    print(report.final_message or "(no final message)")
    print("=" * 68)
    print(f"provider={report.provider}  steps={report.steps}  "
          f"sandbox_runs={report.sandbox_runs}  "
          f"gated={report.gated_actions}  executed={report.executed_destructive}")
    if not args.no_persist:
        print(f"run_id={report.run_id}  (resume with: --resume {report.run_id})")

    if args.json:
        print(json.dumps({
            "run_id": report.run_id, "provider": report.provider, "steps": report.steps,
            "tool_calls": report.tool_calls, "approvals": report.approvals,
            "sandbox_runs": report.sandbox_runs, "error": report.error,
        }, indent=2, default=str))

    return 1 if report.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
