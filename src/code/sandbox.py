from __future__ import annotations

import os
import re
import resource
import subprocess
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class SimpleSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """A minimal local backend for command execution.

    Notes:
    - File tools (`read_file`, `write_file`, etc.) operate on `root_dir`.
    - `execute` runs directly in `root_dir` without temp snapshot isolation.
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
        """初始化沙箱后端实例。

        Args:
            root_dir: 文件工具作用的根目录。
            virtual_mode: 是否使用虚拟文件模式（由父类处理）。
            timeout: 单条命令超时时间（秒）。
            max_output_bytes: 输出最大字节数，超出会截断。
            cpu_time_limit_seconds: 子进程 CPU 时间上限（秒）。
            memory_limit_mb: 子进程内存上限（MB）。
            file_size_limit_mb: 子进程可写文件大小上限（MB）。

        Returns:
            None。
        """
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode)
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._cpu_time_limit_seconds = cpu_time_limit_seconds
        self._memory_limit_bytes = memory_limit_mb * 1024 * 1024
        self._file_size_limit_bytes = file_size_limit_mb * 1024 * 1024
        self._sandbox_id = "simple-local-workspace"
        self._sandbox_base = self.cwd
        self._workspace = self.cwd
        self._workspace.mkdir(parents=True, exist_ok=True)

    @property
    def id(self) -> str:
        """返回当前沙箱实例 ID。

        Returns:
            唯一沙箱 ID 字符串。
        """
        return self._sandbox_id

    def _is_command_blocked(self, command: str) -> bool:
        """判断命令是否命中阻断策略。

        Args:
            command: 待执行命令。

        Returns:
            `True` 表示命中黑名单策略，不允许执行。
        """
        lowered = command.lower()
        return any(re.search(pattern, lowered) for pattern in self._BLOCKED_PATTERNS)

    def _refresh_workspace(self) -> None:
        """确保工作区目录存在。

        Args:
            None。

        Returns:
            None。
        """
        self._workspace.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        """Resolve tool file path and normalize sandbox-root absolute paths.

        If the model passes an absolute path under `self.cwd`, rewrite it to a
        root-relative virtual path so files are created directly under workspace
        instead of nesting full absolute path fragments.
        """
        path = Path(key)
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(self.cwd)
            except ValueError:
                pass
            else:
                key = str(relative)
        return super()._resolve_path(key)

    def _build_env(self) -> dict[str, str]:
        """构建子进程执行环境变量。

        Args:
            None。

        Returns:
            传给子进程的环境变量字典。
        """
        path_value = os.environ.get("PATH", "/usr/bin:/bin")
        return {
            "PATH": path_value,
            "HOME": str(self._sandbox_base),
            "TMPDIR": str(self._workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _preexec_limits(self) -> None:
        """在子进程执行前设置资源限制。

        Args:
            None。

        Returns:
            None。
        """
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
        """在工作区目录内执行命令并返回统一结果。

        Args:
            command: 待执行的 shell 命令。

        Returns:
            `ExecuteResponse`，包含输出、退出码和是否截断。
        """
        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(output="Error: Command must be a non-empty string.", exit_code=1, truncated=False)

        if self._is_command_blocked(command):
            return ExecuteResponse(
                output="Error: Command blocked by sandbox policy.",
                exit_code=126,
                truncated=False,
            )

        try:
            if not self._workspace.exists():
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
