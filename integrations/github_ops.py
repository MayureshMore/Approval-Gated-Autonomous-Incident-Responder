"""
Real remediation: open an actual revert PR (Layer 2 / DGX #1).

This is what turns the demo from "the agent said it would roll back" into the
event's own bar — *"it opens the pull request"*. It stays behind the same
approval gate as every other destructive tool; `open_revert_pr` is in
agent.registry.REQUIRES_APPROVAL.

Two ways to wire it, in order of preference for the DGX track:
  A) GitHub MCP over OAuth, registered in the harness — the harness does the
     work, which is what the track rewards. The agent calls the MCP create-PR
     tool; this module is then only a fallback.
  B) This module, via the `gh` CLI. Still a real PR, just not via MCP.

Setup for (B):
    gh auth login                       # or export GH_TOKEN
    export GITHUB_REPO=you/incident-demo-repo
    export GITHUB_REVERT_ENABLED=1      # explicit opt-in; see below

Safety posture — deliberate, and worth saying out loud in the pitch:
  * Nothing runs through a shell. Every command is an argv list, so a service
    name or branch can never become shell syntax.
  * All git work happens in a temp clone that is deleted afterwards. The agent
    never touches the checkout it is running from.
  * It opens a PR. It does not merge, force-push, or touch the default branch —
    a human still reviews the revert.
  * Dry-run is the default unless GITHUB_REVERT_ENABLED=1, so a misconfigured
    demo machine cannot surprise anyone with a real PR.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

REPO_ENV = "GITHUB_REPO"
ENABLE_ENV = "GITHUB_REVERT_ENABLED"
DEFAULT_BRANCH = os.environ.get("GITHUB_BASE_BRANCH", "main")
TIMEOUT = 60

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_UNSAFE_REF = re.compile(r"[^A-Za-z0-9._-]+")


def _branch_slug(service: str) -> str:
    """A service name is free text; a git ref is not. Reduce it to a legal ref."""
    slug = _UNSAFE_REF.sub("-", service).strip("-.").lower()[:40]
    return slug or "service"


class GitHubOpsError(RuntimeError):
    pass


def repo() -> str:
    value = os.environ.get(REPO_ENV, "").strip()
    if not value:
        raise GitHubOpsError(f"{REPO_ENV} is not set (expected 'owner/name')")
    if not _REPO_RE.match(value):
        raise GitHubOpsError(f"{REPO_ENV}={value!r} is not a valid 'owner/name'")
    return value


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV) == "1"


def _run(argv: list[str], cwd: Optional[str] = None) -> str:
    """Run a command as argv — never through a shell."""
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, timeout=TIMEOUT)
    if proc.returncode != 0:
        raise GitHubOpsError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed: "
                             f"{(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout.strip()


def preflight() -> dict:
    """Can we actually open a PR right now? Check before the demo, not during it."""
    status = {"gh_installed": bool(shutil.which("gh")), "repo": None,
              "authenticated": False, "enabled": enabled(), "ready": False}
    try:
        status["repo"] = repo()
    except GitHubOpsError as exc:
        status["error"] = str(exc)
        return status
    if not status["gh_installed"]:
        status["error"] = "the `gh` CLI is not installed"
        return status
    try:
        _run(["gh", "auth", "status"])
        status["authenticated"] = True
    except Exception as exc:
        status["error"] = f"gh is not authenticated: {exc}"
        return status
    status["ready"] = status["enabled"]
    if not status["enabled"]:
        status["error"] = f"set {ENABLE_ENV}=1 to open real PRs (currently dry-run)"
    return status


def latest_commit_sha(branch: str = DEFAULT_BRANCH) -> str:
    """SHA of the tip of `branch` — the 'bad deploy' we are reverting."""
    sha = _run(["gh", "api", f"repos/{repo()}/commits/{branch}", "--jq", ".sha"])
    if not _SHA_RE.match(sha):
        raise GitHubOpsError(f"unexpected sha from GitHub: {sha!r}")
    return sha


def open_revert_pr(bad_commit_sha: str, service: str, reason: str,
                   base: str = DEFAULT_BRANCH) -> dict:
    """
    DESTRUCTIVE — must be called only after approval.

    Clone shallow into a temp dir, revert the commit on a new branch, push, and
    open a PR against `base`. The temp clone is always removed.
    """
    if not _SHA_RE.match(bad_commit_sha or ""):
        raise GitHubOpsError(f"refusing to revert a malformed sha: {bad_commit_sha!r}")

    slug = repo()
    branch = f"revert-{_branch_slug(service)}-{bad_commit_sha[:7]}"
    title = f"Revert bad deploy on {service} ({bad_commit_sha[:7]})"
    body = (f"Automated incident remediation, approved by a human on-call.\n\n"
            f"- **Service:** {service}\n- **Reverts:** {bad_commit_sha}\n\n"
            f"**Why:** {reason}\n")

    if not enabled():
        return {"ok": True, "dry_run": True, "service": service, "branch": branch,
                "pr_url": None, "title": title,
                "message": (f"DRY RUN — would open '{title}' on {slug}. "
                            f"Set {ENABLE_ENV}=1 to open it for real.")}

    workdir = tempfile.mkdtemp(prefix="ir-revert-")
    checkout = os.path.join(workdir, "repo")
    try:
        _run(["git", "clone", "--depth", "10", "--branch", base,
              f"https://github.com/{slug}.git", checkout])
        _run(["git", "checkout", "-b", branch], cwd=checkout)
        _run(["git", "-c", "user.name=incident-responder",
              "-c", "user.email=incident-responder@localhost",
              "revert", "--no-edit", bad_commit_sha], cwd=checkout)
        _run(["git", "push", "-u", "origin", branch], cwd=checkout)
        url = _run(["gh", "pr", "create", "--repo", slug, "--base", base,
                    "--head", branch, "--title", title, "--body", body])
        return {"ok": True, "dry_run": False, "service": service, "branch": branch,
                "pr_url": url, "message": f"Opened revert PR for {service}: {url}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def rollback_via_github(service: str, reason: str) -> dict:
    """Agent-facing tool. DESTRUCTIVE — gated by agent.registry.REQUIRES_APPROVAL."""
    try:
        return open_revert_pr(latest_commit_sha(), service, reason)
    except GitHubOpsError as exc:
        # Come back as data so the agent can report the failure and stand down,
        # rather than blowing up the run mid-incident.
        return {"ok": False, "service": service, "error": str(exc),
                "message": f"Could not open a revert PR: {exc}"}


if __name__ == "__main__":
    print(json.dumps(preflight(), indent=2))
