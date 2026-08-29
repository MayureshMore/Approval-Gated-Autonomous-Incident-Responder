#!/usr/bin/env python3
"""
Ripcord — the incident responder, CLI entrypoint.

    python run_agent.py --provider truefoundry --subagents   # the primary runtime
    python run_agent.py --provider sim                       # no API key needed
    python run_agent.py --selftest                           # verify wiring, run nothing

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

from agent.approval import ApprovalGate
from agent.bus import EventBus
from agent.core import IncidentAgent
from agent.registry import audit


def make_provider(name: str, run_id: str | None = None):
    if name == "truefoundry":
        from agent.providers.truefoundry import TrueFoundryProvider
        return TrueFoundryProvider(run_id=run_id)
    if name == "sim":
        from agent.providers.sim import SimProvider
        return SimProvider()
    if name == "openai":
        from agent.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "anthropic":
        from agent.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    raise SystemExit(f"unknown provider '{name}'")


def preflight(require_env: bool = True) -> list[str]:
    """Check what would break BEFORE we are standing in front of judges."""
    problems: list[str] = []

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
        import tools
        try:
            alerts = tools.get_active_alerts()
            if not alerts:
                problems.append(f"mock env at {tools.BASE_URL} has no active alerts — POST /reset")
        except Exception as exc:
            problems.append(f"mock env unreachable at {tools.BASE_URL}: {exc}")

    return problems


def main() -> int:
    p = argparse.ArgumentParser(description="Ripcord incident responder")
    p.add_argument("--provider", default=os.environ.get("PROVIDER", "sim"),
                   choices=["truefoundry", "sim", "openai", "anthropic"])
    p.add_argument("--subagents", action="store_true",
                   help="fan out parallel read-only investigators first")
    p.add_argument("--github", action="store_true",
                   help="register the real revert-PR tool (Layer 2)")
    p.add_argument("--max-steps", type=int, default=16)
    p.add_argument("--approval-timeout", type=int, default=300)
    p.add_argument("--selftest", action="store_true", help="run preflight and exit")
    p.add_argument("--json", action="store_true", help="print the run report as JSON")
    p.add_argument("--list-models", action="store_true",
                   help="list the models this TrueFoundry gateway exposes, then exit")
    args = p.parse_args()

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

    problems = preflight(require_env=True)
    if args.provider == "truefoundry":
        from agent.providers.truefoundry import preflight as tfy_preflight
        tfy = tfy_preflight()
        if not tfy["ready"]:
            problems.append(f"truefoundry gateway: {tfy.get('error')}")

    if args.selftest:
        report = {"registry": audit(), "problems": problems}
        if args.provider == "truefoundry":
            report["truefoundry"] = tfy
        print(json.dumps(report, indent=2))
        print("\nSELFTEST:", "FAIL" if problems else "PASS")
        return 1 if problems else 0
    if problems:
        print("Preflight failed:")
        for pr in problems:
            print("  -", pr)
        return 1

    bus = EventBus()
    agent = IncidentAgent(
        provider=make_provider(args.provider, run_id=bus.run_id),
        bus=bus,
        gate=ApprovalGate(bus, timeout_s=args.approval_timeout),
        include_github=args.github,
        max_steps=args.max_steps,
        use_subagents=args.subagents,
    )
    report = agent.run()

    print("\n" + "=" * 68)
    print(report.final_message or "(no final message)")
    print("=" * 68)
    print(f"provider={report.provider}  steps={report.steps}  "
          f"sandbox_runs={report.sandbox_runs}  "
          f"gated={report.gated_actions}  executed={report.executed_destructive}")

    if args.json:
        print(json.dumps({
            "run_id": report.run_id, "provider": report.provider, "steps": report.steps,
            "tool_calls": report.tool_calls, "approvals": report.approvals,
            "sandbox_runs": report.sandbox_runs, "error": report.error,
        }, indent=2, default=str))

    return 1 if report.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
