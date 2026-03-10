"""后台任务系统：异步执行命令并在后续轮次注入结果通知。"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@dataclass
class BackgroundTaskRecord:
    """后台任务状态记录。"""

    task_id: str
    command: str
    status: str
    started_at: float
    timeout_seconds: int
    return_code: int | None = None
    finished_at: float | None = None
    output_preview: str = ""


@dataclass
class BackgroundManager:
    """后台任务管理器。"""

    workdir: Path
    default_timeout: int = 300
    max_output_chars: int = 50000
    preview_chars: int = 500
    _tasks: dict[str, BackgroundTaskRecord] = field(default_factory=dict)
    _notification_queue: list[dict[str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _is_dangerous(self, command: str) -> bool:
        dangerous = ["rm -rf /", "shutdown", "reboot", "> /dev/"]
        return any(token in command for token in dangerous)

    def run(self, command: str, timeout_seconds: int | None = None) -> str:
        """启动后台任务线程并立即返回。"""
        cmd = (command or "").strip()
        if not cmd:
            return "Error: command is required"
        if self._is_dangerous(cmd):
            return "Error: Dangerous command blocked"

        timeout = int(timeout_seconds or self.default_timeout)
        task_id = str(uuid.uuid4())[:8]
        record = BackgroundTaskRecord(
            task_id=task_id,
            command=cmd,
            status="running",
            started_at=time.time(),
            timeout_seconds=timeout,
        )

        with self._lock:
            self._tasks[task_id] = record

        thread = threading.Thread(target=self._execute, args=(task_id,), daemon=True)
        thread.start()
        return _json(
            {
                "message": f"Background task {task_id} started",
                "task_id": task_id,
                "status": "running",
                "command": cmd,
                "timeout_seconds": timeout,
            }
        )

    def _execute(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            command = record.command
            timeout = record.timeout_seconds

        status = "completed"
        return_code: int | None = None
        output = ""

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return_code = result.returncode
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                status = "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
            output = f"Error: Timeout ({timeout}s)"
        except Exception as exc:
            status = "failed"
            output = f"Error: {exc}"

        output = output[: self.max_output_chars] if output else "(no output)"
        preview = output[: self.preview_chars]

        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.status = status
            record.return_code = return_code
            record.finished_at = time.time()
            record.output_preview = preview
            self._notification_queue.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "command": record.command,
                    "result": preview,
                }
            )

    def check(self, task_id: str | None = None) -> str:
        """查询后台任务状态。"""
        with self._lock:
            if task_id:
                record = self._tasks.get(task_id)
                if record is None:
                    return f"Error: task {task_id} not found"
                payload = self._record_to_dict(record)
                return _json(payload)

            tasks = [self._record_to_dict(record) for record in self._tasks.values()]
            tasks.sort(key=lambda item: item["started_at"])
            summary = {
                "count": len(tasks),
                "running": sum(1 for item in tasks if item["status"] == "running"),
                "completed": sum(1 for item in tasks if item["status"] == "completed"),
                "failed": sum(1 for item in tasks if item["status"] == "failed"),
                "timeout": sum(1 for item in tasks if item["status"] == "timeout"),
                "tasks": tasks,
            }
            return _json(summary)

    def _record_to_dict(self, record: BackgroundTaskRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "command": record.command,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "timeout_seconds": record.timeout_seconds,
            "return_code": record.return_code,
            "output_preview": record.output_preview,
        }

    def drain_notifications(self) -> list[dict[str, str]]:
        """排空通知队列，用于注入到下一轮对话。"""
        with self._lock:
            if not self._notification_queue:
                return []
            drained = list(self._notification_queue)
            self._notification_queue.clear()
            return drained


def build_background_tools(manager: BackgroundManager) -> list:
    """构建 deepagents 可注册的后台任务工具。"""

    def background_run(command: str, timeout_seconds: int = 300) -> str:
        """Run a shell command in background and return a task id immediately."""
        return manager.run(command=command, timeout_seconds=timeout_seconds)

    def background_check(task_id: str | None = None) -> str:
        """Check one background task by id, or all tasks when task_id is omitted."""
        return manager.check(task_id=task_id)

    background_run.__name__ = "background_run"
    background_check.__name__ = "background_check"
    return [background_run, background_check]

