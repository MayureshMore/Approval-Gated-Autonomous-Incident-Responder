"""
The real-remediation path (Layer 2). No network here: we assert on the argv we
would run, because that is exactly where an injection or a wrong flag would hide.
"""
import re

import pytest

from integrations import github_ops as gh


@pytest.fixture
def repo_env(monkeypatch):
    monkeypatch.setenv(gh.REPO_ENV, "octocat/incident-demo")
    monkeypatch.delenv(gh.ENABLE_ENV, raising=False)


# --- guardrails ------------------------------------------------------------
def test_dry_run_is_the_default(repo_env):
    out = gh.open_revert_pr("a" * 40, "checkout-service", "bad deploy")
    assert out["ok"] and out["dry_run"] is True and out["pr_url"] is None


def test_dry_run_touches_nothing(repo_env, monkeypatch):
    monkeypatch.setattr(gh, "_run", lambda *a, **k: pytest.fail("dry run must not execute"))
    gh.open_revert_pr("b" * 40, "checkout-service", "reason")


def test_missing_repo_is_an_error(monkeypatch):
    monkeypatch.delenv(gh.REPO_ENV, raising=False)
    with pytest.raises(gh.GitHubOpsError):
        gh.repo()


@pytest.mark.parametrize("bad", ["not-a-repo", "a/b/c", "owner/name; rm -rf /", "../../etc"])
def test_malformed_repo_is_rejected(monkeypatch, bad):
    monkeypatch.setenv(gh.REPO_ENV, bad)
    with pytest.raises(gh.GitHubOpsError):
        gh.repo()


@pytest.mark.parametrize("bad", ["", "zzz", "abc; touch /tmp/pwned", "$(whoami)", "a" * 41])
def test_malformed_sha_is_refused(repo_env, monkeypatch, bad):
    monkeypatch.setenv(gh.ENABLE_ENV, "1")
    with pytest.raises(gh.GitHubOpsError):
        gh.open_revert_pr(bad, "checkout-service", "reason")


# --- what we would actually run --------------------------------------------
def test_commands_are_argv_lists_never_shell_strings(repo_env, monkeypatch):
    monkeypatch.setenv(gh.ENABLE_ENV, "1")
    seen = []

    def fake_run(argv, cwd=None):
        seen.append(argv)
        assert isinstance(argv, list), "a shell string is an injection waiting to happen"
        assert argv[0] in ("git", "gh")
        return "https://github.com/octocat/incident-demo/pull/1"

    monkeypatch.setattr(gh, "_run", fake_run)
    out = gh.open_revert_pr("c" * 40, "checkout-service", "v1.4.2 regression")

    assert out["ok"] and out["dry_run"] is False
    assert out["pr_url"].endswith("/pull/1")
    flat = [" ".join(a) for a in seen]
    assert any(f.startswith("git clone --depth 10") for f in flat)
    assert any("revert --no-edit" in f for f in flat)
    assert any(f.startswith("gh pr create") for f in flat)


def test_it_opens_a_pr_and_never_merges_or_force_pushes(repo_env, monkeypatch):
    monkeypatch.setenv(gh.ENABLE_ENV, "1")
    seen = []
    monkeypatch.setattr(gh, "_run", lambda argv, cwd=None: seen.append(argv) or "url")
    gh.open_revert_pr("d" * 40, "checkout-service", "reason")

    flat = " ".join(" ".join(a) for a in seen)
    for forbidden in ("pr merge", "--force", "-f ", "push origin main", "reset --hard"):
        assert forbidden not in flat, f"revert flow must never {forbidden!r}"


@pytest.mark.parametrize("hostile", [
    "checkout; rm -rf /", "../../../etc/passwd", "svc $(whoami)", "a b\tc", "--upload-pack=evil",
])
def test_hostile_service_name_becomes_a_legal_git_ref(repo_env, monkeypatch, hostile):
    """A service name is free text; it must never shape a command or a ref."""
    monkeypatch.setenv(gh.ENABLE_ENV, "1")
    seen = []
    monkeypatch.setattr(gh, "_run", lambda argv, cwd=None: seen.append(argv) or "url")
    gh.open_revert_pr("e" * 40, hostile, "reason")

    branch = next(a[a.index("-b") + 1] for a in seen if "-b" in a)
    assert re.fullmatch(r"[A-Za-z0-9._-]+", branch), f"illegal git ref: {branch!r}"
    assert not branch.startswith("-"), "a ref starting with - would parse as a flag"


def test_branch_slug_is_stable_and_readable(repo_env):
    assert gh._branch_slug("checkout-service") == "checkout-service"
    assert gh._branch_slug("Checkout Service!") == "checkout-service"


def test_never_clones_into_the_working_directory(repo_env, monkeypatch, tmp_path):
    import os
    monkeypatch.setenv(gh.ENABLE_ENV, "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh, "_run", lambda argv, cwd=None: "url")
    gh.open_revert_pr("f" * 40, "checkout-service", "reason")
    assert list(tmp_path.iterdir()) == [], "the repo checkout must not be polluted"


# --- failures come back as data --------------------------------------------
def test_tool_returns_an_error_dict_instead_of_raising(repo_env, monkeypatch):
    monkeypatch.setattr(gh, "latest_commit_sha",
                        lambda *a, **k: (_ for _ in ()).throw(gh.GitHubOpsError("gh not authed")))
    out = gh.rollback_via_github("checkout-service", "reason")
    assert out["ok"] is False and "gh not authed" in out["error"]


def test_preflight_explains_what_is_missing(monkeypatch):
    monkeypatch.delenv(gh.REPO_ENV, raising=False)
    assert gh.preflight()["ready"] is False and "error" in gh.preflight()
