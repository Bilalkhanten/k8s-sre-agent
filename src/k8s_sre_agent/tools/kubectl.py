"""A deliberately tiny, read-only wrapper around the ``kubectl`` binary.

Defence in depth, innermost first:

1. The ServiceAccount behind ``kubeconfig`` can only get/list/watch (see ``lab/rbac``).
   This is the control that actually matters.
2. Only the verbs in :data:`ALLOWED_VERBS` can be run, and flags that change identity or
   target (``--as``, ``--token``, ``--server`` ...) are refused.
3. The process is started without a shell and with a minimal environment, so ambient
   credentials (``KUBECONFIG``, cloud tokens) cannot leak into the agent's kubectl.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from k8s_sre_agent.tools.base import ToolError

ALLOWED_VERBS = frozenset({"get", "describe", "logs"})
FORBIDDEN_FLAG_PREFIXES = (
    "--kubeconfig",
    "--as",
    "--token",
    "--server",
    "--context",
    "--cluster",
    "--user",
    "--insecure-skip-tls-verify",
    "--certificate-authority",
    "--client-certificate",
    "--client-key",
    "--username",
    "--password",
    "--exec",
    "-s",
)
MAX_ERROR_CHARS = 600


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float, dict[str, str]], RunResult]


def _subprocess_runner(argv: Sequence[str], timeout: float, env: dict[str, str]) -> RunResult:
    try:
        proc = subprocess.run(  # argv list, no shell, verbs allowlisted
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"kubectl timed out after {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise ToolError("kubectl binary not found on PATH") from exc
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


class Kubectl:
    """Runs allowlisted kubectl verbs as the identity in ``kubeconfig`` - never anyone else."""

    def __init__(
        self,
        kubeconfig: Path,
        *,
        binary: str = "kubectl",
        timeout: float = 20.0,
        runner: Runner | None = None,
    ) -> None:
        if not kubeconfig.is_file():
            raise FileNotFoundError(
                f"{kubeconfig}: agent kubeconfig not found (run `make agent-kubeconfig`)"
            )
        self._kubeconfig = kubeconfig.resolve()
        self._binary = binary
        self._timeout = timeout
        self._runner = runner or _subprocess_runner

    def run(self, verb: str, *args: str) -> str:
        """Run ``kubectl <verb> <args...>`` and return stdout; raise :class:`ToolError`."""
        if verb not in ALLOWED_VERBS:
            raise ToolError(f"kubectl verb {verb!r} is not permitted (read-only verbs only)")
        for arg in args:
            if arg.startswith(FORBIDDEN_FLAG_PREFIXES):
                raise ToolError(f"kubectl flag {arg.split('=')[0]!r} is not permitted")

        argv = [self._binary, f"--kubeconfig={self._kubeconfig}", verb, *args]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        result = self._runner(argv, self._timeout, env)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()[:MAX_ERROR_CHARS]
            raise ToolError(f"kubectl {verb} failed: {message or 'no error output'}")
        return result.stdout
