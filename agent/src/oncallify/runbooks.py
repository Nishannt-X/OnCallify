"""Runs an allowlisted action's runbook and parses its JSON result.

Contract shared by the bash and PowerShell implementations:

* arguments: ``--app NAME [--commit SHA] [--size N] [--dry-run]``
  (PowerShell: ``-App NAME [-Commit SHA] [-Size N] [-DryRun]``)
* stdout: exactly one JSON object on the last line:
  ``{"ok", "action", "dry_run", "changed", "detail", "before", "after"}``
* exit code: 0 ok, 2 usage, 3 precondition failed, 4 execution failed

The dry run is executed by the same runbook with the same privileges as the real run,
so its answer is the truth about what the real run would do (a lesson from Beacon:
a dry run only tells the truth to the caller that will execute).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oncallify import actions

EXIT_CODES = {0: "ok", 2: "usage", 3: "precondition_failed", 4: "execution_failed"}


class KillSwitchEngaged(RuntimeError):
    pass


@dataclass(slots=True)
class RunbookResult:
    ok: bool
    code: str
    changed: bool
    detail: str
    dry_run: bool
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    stderr: str = ""


def _flags(params: dict[str, Any], windows: bool) -> list[str]:
    out: list[str] = []
    for key, value in params.items():
        out += [f"-{key.capitalize()}" if windows else f"--{key}", str(value)]
    return out


class RunbookExecutor:
    def __init__(self, runbook_dir: Path, env: dict[str, str], *, killswitch_file: Path,
                 timeout_s: float = 90.0, windows: bool | None = None):
        self.runbook_dir = runbook_dir
        self.env = env
        self.killswitch_file = killswitch_file
        self.timeout_s = timeout_s
        self.windows = platform.system() == "Windows" if windows is None else windows

    def killswitch_engaged(self) -> bool:
        return self.killswitch_file.exists()

    def command(self, action: str, params: dict[str, Any], dry_run: bool) -> list[str]:
        actions.get(action)  # refuses anything not allowlisted
        if self.windows:
            script = self.runbook_dir / "windows" / f"{action.replace('_', '-')}.ps1"
            shell = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
            cmd = [shell, "-NoProfile", "-NonInteractive", "-File", str(script), *_flags(params, True)]
            return cmd + (["-DryRun"] if dry_run else [])
        script = self.runbook_dir / "linux" / f"{action.replace('_', '-')}.sh"
        return ["bash", str(script), *_flags(params, False), *(["--dry-run"] if dry_run else [])]

    def run(self, action: str, params: dict[str, Any], *, dry_run: bool) -> RunbookResult:
        clean = actions.validate(action, params)
        if not dry_run and self.killswitch_engaged():
            raise KillSwitchEngaged(f"kill switch is engaged ({self.killswitch_file}); refusing to execute")
        cmd = self.command(action, clean, dry_run)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s,
                                  env={**os.environ, **self.env})
        except subprocess.TimeoutExpired:
            return RunbookResult(False, "timeout", False, f"runbook exceeded {self.timeout_s:.0f}s", dry_run,
                                 exit_code=-1)
        last = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.strip().startswith("{")), "")
        try:
            body = json.loads(last) if last else {}
        except json.JSONDecodeError:
            body = {}
        code = EXIT_CODES.get(proc.returncode, "execution_failed")
        if not body:
            return RunbookResult(False, "bad_output", False, "runbook printed no JSON result", dry_run,
                                 exit_code=proc.returncode, stderr=proc.stderr[-2000:])
        return RunbookResult(
            ok=bool(body.get("ok")) and proc.returncode == 0,
            code=code,
            changed=bool(body.get("changed")),
            detail=str(body.get("detail", ""))[:1000],
            dry_run=dry_run,
            before=dict(body.get("before") or {}),
            after=dict(body.get("after") or {}),
            exit_code=proc.returncode,
            stderr=proc.stderr[-2000:],
        )
