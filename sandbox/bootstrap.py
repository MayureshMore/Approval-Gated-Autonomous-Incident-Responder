"""
Sandbox bootstrap — runs INSIDE the isolated child process.

Never import this from the parent. `runner.py` execs it with the agent-written
snippet on stdin; everything here is about locking the process down *before*
that snippet gets to run.

Protocol (stdin/stdout, JSON):
  in  : {"code": "<agent python>", "payload": {...}, "limits": {...}}
  out : {"ok": bool, "result": any, "stdout": str, "error": str|null}
"""
import builtins
import io
import json
import os
import sys
import traceback


def _harden(limits: dict) -> None:
    """Drop the process's ability to do anything but compute."""
    # 1. Resource caps — CPU seconds, address space, file size, no forking.
    try:
        import resource
    except ImportError:
        resource = None  # non-POSIX: process isolation + timeout still apply

    if resource is not None:
        cpu = int(limits.get("cpu_seconds", 5))
        mem = int(limits.get("memory_mb", 256)) * 1024 * 1024
        # Applied independently: a kernel that refuses one limit must not cost us
        # the others. macOS in particular rejects RLIMIT_AS and RLIMIT_NPROC.
        for res, val in (
            ("RLIMIT_CPU", (cpu, cpu)),
            ("RLIMIT_FSIZE", (1 << 20, 1 << 20)),   # 1 MB of file writes
            ("RLIMIT_NPROC", (0, 0)),               # no fork/exec
            ("RLIMIT_AS", (mem, mem)),
        ):
            try:
                resource.setrlimit(getattr(resource, res), val)
            except (AttributeError, ValueError, OSError):
                pass

    # 2. No network. The diagnostic reasons over data the agent already fetched;
    #    if it tries to phone home, that's a bug or an exfil attempt — fail loud.
    import socket

    def _blocked(*_a, **_kw):
        raise PermissionError("network access is blocked inside the sandbox")

    socket.socket = _blocked
    socket.create_connection = _blocked
    socket.socketpair = _blocked

    # 3. No subprocesses — belt and braces alongside RLIMIT_NPROC.
    def _no_subprocess(*_a, **_kw):
        raise PermissionError("starting processes is blocked inside the sandbox")

    for name in ("system", "popen", "execv", "execve", "fork", "forkpty", "spawnv"):
        if hasattr(os, name):
            setattr(os, name, _no_subprocess)

    # 3b. Filesystem confinement — the sandbox working directory and nothing
    #     else. Without this the other three measures are theatre: the snippet
    #     cannot open a socket, but it CAN read the repo's .env by absolute path
    #     and print the gateway key straight into its own result. (It could, and
    #     did, until this went in.) Scrubbing the environment is not enough when
    #     the secrets are also on disk.
    #
    #     The guards go on the names a snippet can reach. importlib captured its
    #     own references to _io at interpreter start, so the stdlib still
    #     imports normally — verified, not assumed: test_stdlib_imports_still
    #     _work_under_confinement.
    workdir = os.path.realpath(os.getcwd())

    def _permit(path):
        """Allow only paths inside the sandbox working directory."""
        if isinstance(path, int):
            return path            # an already-open fd, not a new lookup
        try:
            resolved = os.path.realpath(os.fspath(path))
        except (TypeError, ValueError):
            return path
        if resolved != workdir and not resolved.startswith(workdir + os.sep):
            raise PermissionError(
                f"path outside the sandbox is blocked: {os.fspath(path)!r}. "
                "The diagnostic may only touch its own working directory; pass "
                "any data you need in through PAYLOAD.")
        return path

    def _guard(fn):
        def wrapper(path, *a, **kw):
            _permit(path)
            return fn(path, *a, **kw)
        return wrapper

    import io as _io_mod

    builtins.open = _guard(builtins.open)
    _io_mod.open = builtins.open
    _io_mod.FileIO = _guard(_io_mod.FileIO)
    for name in ("open", "listdir", "scandir", "remove", "unlink", "rename",
                 "replace", "rmdir", "mkdir", "makedirs", "chmod", "truncate"):
        if hasattr(os, name):
            setattr(os, name, _guard(getattr(os, name)))

    # 4. Import denylist. Everything else in the stdlib stays available so the
    #    agent can genuinely write useful analysis code.
    denied = {"subprocess", "socket", "ctypes", "multiprocessing", "http",
              "urllib", "requests", "ftplib", "smtplib", "telnetlib", "pty"}
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in denied:
            raise ImportError(f"'{root}' is not importable inside the sandbox")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = guarded_import


def _explain(exc: Exception, code: str, env: dict) -> str:
    """Turn a sandbox exception into something the agent can fix in one shot.

    A bare "TypeError: list indices must be integers or slices, not str" tells
    the model nothing about WHERE it went wrong, and in a live gateway run it
    cost a whole extra sandbox round-trip to guess. Name the line and show it.
    """
    parts = [f"{type(exc).__name__}: {exc}"]

    lineno = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == "<agent-diagnostic>":
            lineno = frame.lineno
    if lineno:
        lines = code.splitlines()
        if 0 < lineno <= len(lines):
            parts.append(f"  at line {lineno}: {lines[lineno - 1].strip()}")

    # Payload shape is the usual culprit: the model reaches into PAYLOAD with a
    # key that is not there, or indexes a list as if it were a dict.
    if isinstance(exc, (TypeError, KeyError, IndexError, AttributeError)):
        payload = env.get("PAYLOAD")
        if isinstance(payload, dict):
            shape = ", ".join(
                f"{k}: {type(v).__name__}"
                + (f"[{len(v)}]" if isinstance(v, (list, dict)) else "")
                for k, v in sorted(payload.items()))
            parts.append(f"  PAYLOAD contains -> {{{shape}}}")
    return "\n".join(parts)


def main() -> None:
    req = json.loads(sys.stdin.read())
    _harden(req.get("limits", {}))

    # `diagnostics` is vendored next to us by the runner so the snippet can use
    # the helpers without reaching outside the sandbox working directory.
    sys.path.insert(0, os.getcwd())

    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured

    env: dict = {"__name__": "__sandbox__", "PAYLOAD": req.get("payload", {}), "RESULT": None}
    out = {"ok": False, "result": None, "stdout": "", "error": None, "hint": None}
    try:
        exec(compile(req["code"], "<agent-diagnostic>", "exec"), env)
        result = env.get("RESULT")
        json.dumps(result)  # must be serialisable to cross the boundary
        out.update(ok=True, result=result)
        if result is None:
            # The snippet ran clean but produced nothing. Almost always a
            # lowercase `result = ...`, which silently vanishes — and "ok" with
            # a null result reads as success, so the model proceeds on no
            # evidence at all. Say what happened instead.
            lowercase = "result" in env and env["result"] is not None
            out["hint"] = (
                "RESULT was never assigned, so this diagnostic returned nothing. "
                + ("You assigned to `result` (lowercase); the sandbox only reads "
                   "`RESULT`. Re-run with RESULT = ..."
                   if lowercase else
                   "Assign your answer to RESULT, e.g. RESULT = {...}, and re-run.")
            )
    except Exception as exc:
        out["error"] = _explain(exc, req["code"], env)
    finally:
        sys.stdout = real_stdout
        out["stdout"] = captured.getvalue()[:4000]

    real_stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
