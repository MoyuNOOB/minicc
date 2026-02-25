from __future__ import annotations

import os
import re
import resource
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class SimpleSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """A minimal local sandbox backend for command execution.

    Notes:
    - File tools (`read_file`, `write_file`, etc.) still operate on `root_dir`.
    - `execute` runs inside an isolated temp workspace snapshot of `root_dir`.
    - This is a lightweight sandbox, not a full OS/container isolation boundary.
    """

    _BLOCKED_PATTERNS = [
        r"(^|\s)sudo(\s|$)",
        r"(^|\s)su(\s|$)",
        r"(^|\s)doas(\s|$)",
        r"(^|\s)(shutdown|reboot|halt|poweroff)(\s|$)",
        r"(^|\s)(diskutil|launchctl|systemctl|service)(\s|$)",
        r"(^|\s)(mount|umount|mkfs)(\s|$)",
        r"rm\s+-rf\s+/",
        r"dd\s+if=",
    ]

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        virtual_mode: bool = True,
        timeout: float = 30.0,
        max_output_bytes: int = 100_000,
        cpu_time_limit_seconds: int = 10,
        memory_limit_mb: int = 512,
        file_size_limit_mb: int = 16,
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode)
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._cpu_time_limit_seconds = cpu_time_limit_seconds
        self._memory_limit_bytes = memory_limit_mb * 1024 * 1024
        self._file_size_limit_bytes = file_size_limit_mb * 1024 * 1024
        self._sandbox_id = f"simple-sandbox-{uuid.uuid4().hex[:8]}"
        self._sandbox_base = Path(tempfile.gettempdir()) / self._sandbox_id
        self._workspace = self._sandbox_base / "workspace"
        self._sandbox_base.mkdir(parents=True, exist_ok=True)

    @property
    def id(self) -> str:
        return self._sandbox_id

    def _is_command_blocked(self, command: str) -> bool:
        lowered = command.lower()
        return any(re.search(pattern, lowered) for pattern in self._BLOCKED_PATTERNS)

    def _refresh_workspace(self) -> None:
        if self._workspace.exists():
            shutil.rmtree(self._workspace)

        ignore = shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".sandbox",
        )
        shutil.copytree(self.cwd, self._workspace, symlinks=False, ignore=ignore)

    def _build_env(self) -> dict[str, str]:
        path_value = os.environ.get("PATH", "/usr/bin:/bin")
        return {
            "PATH": path_value,
            "HOME": str(self._sandbox_base),
            "TMPDIR": str(self._sandbox_base),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _preexec_limits(self) -> None:
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (self._cpu_time_limit_seconds, self._cpu_time_limit_seconds),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self._file_size_limit_bytes, self._file_size_limit_bytes),
        )
        try:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (self._memory_limit_bytes, self._memory_limit_bytes),
            )
        except (ValueError, OSError):
            pass

    def execute(self, command: str) -> ExecuteResponse:
        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(output="Error: Command must be a non-empty string.", exit_code=1, truncated=False)

        if self._is_command_blocked(command):
            return ExecuteResponse(
                output="Error: Command blocked by sandbox policy.",
                exit_code=126,
                truncated=False,
            )

        try:
            self._refresh_workspace()
            result = subprocess.run(  # noqa: S602
                command,
                shell=True,
                cwd=str(self._workspace),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=self._build_env(),
                preexec_fn=self._preexec_limits,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: Command timed out after {self._timeout:.1f} seconds.",
                exit_code=124,
                truncated=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ExecuteResponse(output=f"Error executing command in sandbox: {exc}", exit_code=1, truncated=False)

        output_parts: list[str] = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            stderr_lines = result.stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines if line)

        output = "\n".join(output_parts).strip() or "<no output>"
        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes] + f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True

        if result.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

        return ExecuteResponse(output=output, exit_code=result.returncode, truncated=truncated)

