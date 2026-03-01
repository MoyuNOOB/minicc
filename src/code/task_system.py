"""任务系统：持久化任务图（DAG）与 deepagents 可调用工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VALID_STATUS = {"pending", "in_progress", "completed"}


@dataclass
class TaskManager:
    """基于文件的任务图管理器。"""

    tasks_dir: Path

    def __post_init__(self) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    def _task_path(self, task_id: int) -> Path:
        return self.tasks_dir / f"task_{task_id}.json"

    def _max_id(self) -> int:
        max_id = 0
        for file in self.tasks_dir.glob("task_*.json"):
            suffix = file.stem.split("_", 1)[-1]
            if suffix.isdigit():
                max_id = max(max_id, int(suffix))
        return max_id

    def _save(self, task: dict[str, Any]) -> None:
        self._task_path(int(task["id"])).write_text(
            json.dumps(task, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, task_id: int) -> dict[str, Any]:
        path = self._task_path(task_id)
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _iter_tasks(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for file in sorted(self.tasks_dir.glob("task_*.json")):
            tasks.append(json.loads(file.read_text(encoding="utf-8")))
        tasks.sort(key=lambda item: int(item.get("id", 0)))
        return tasks

    def _ensure_task_exists(self, task_id: int) -> None:
        if not self._task_path(task_id).exists():
            raise ValueError(f"Task {task_id} not found")

    def _normalize_ids(self, values: list[int] | None) -> list[int]:
        if not values:
            return []
        unique_ids = sorted({int(v) for v in values})
        for task_id in unique_ids:
            self._ensure_task_exists(task_id)
        return unique_ids

    def _clear_dependency(self, completed_id: int) -> None:
        for task in self._iter_tasks():
            blocked_by = task.get("blockedBy", [])
            if completed_id in blocked_by:
                task["blockedBy"] = [item for item in blocked_by if item != completed_id]
                self._save(task)

    def _sync_reverse_edges(self, task_id: int, blocked_by: list[int], blocks: list[int]) -> None:
        all_tasks = {int(task["id"]): task for task in self._iter_tasks()}
        if task_id not in all_tasks:
            return

        current = all_tasks[task_id]
        current["blockedBy"] = blocked_by
        current["blocks"] = blocks
        self._save(current)

        for other_id, other_task in all_tasks.items():
            if other_id == task_id:
                continue

            other_blocked_by = [int(v) for v in other_task.get("blockedBy", [])]
            other_blocks = [int(v) for v in other_task.get("blocks", [])]
            changed = False

            should_block_other = other_id in blocks
            has_block_other = task_id in other_task.get("blockedBy", [])
            if should_block_other and not has_block_other:
                other_blocked_by.append(task_id)
                changed = True
            if not should_block_other and has_block_other:
                other_blocked_by = [v for v in other_blocked_by if v != task_id]
                changed = True

            should_be_blocked_by_other = other_id in blocked_by
            has_reverse_block = task_id in other_task.get("blocks", [])
            if should_be_blocked_by_other and not has_reverse_block:
                other_blocks.append(task_id)
                changed = True
            if not should_be_blocked_by_other and has_reverse_block:
                other_blocks = [v for v in other_blocks if v != task_id]
                changed = True

            if changed:
                other_task["blockedBy"] = sorted(set(other_blocked_by))
                other_task["blocks"] = sorted(set(other_blocks))
                self._save(other_task)

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        blocks: list[int] | None = None,
        owner: str = "",
    ) -> dict[str, Any]:
        if not subject.strip():
            raise ValueError("subject is required")

        task_id = self._next_id
        self._next_id += 1

        task = {
            "id": task_id,
            "subject": subject.strip(),
            "description": description.strip(),
            "status": "pending",
            "blockedBy": [],
            "blocks": [],
            "owner": owner.strip(),
        }
        self._save(task)

        normalized_blocked_by = self._normalize_ids(blocked_by)
        normalized_blocks = self._normalize_ids(blocks)
        self._sync_reverse_edges(task_id, normalized_blocked_by, normalized_blocks)
        return self._load(task_id)

    def update(
        self,
        task_id: int,
        status: str | None = None,
        add_blocked_by: list[int] | None = None,
        add_blocks: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
        remove_blocks: list[int] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        current = self._load(task_id)

        normalized_status: str | None = None
        if status is not None:
            normalized_status = status.strip().lower()
            if normalized_status not in VALID_STATUS:
                raise ValueError(f"Invalid status: {normalized_status}. Use one of {sorted(VALID_STATUS)}")

        blocked_by = set(int(v) for v in current.get("blockedBy", []))
        blocks = set(int(v) for v in current.get("blocks", []))

        for value in self._normalize_ids(add_blocked_by):
            if value != task_id:
                blocked_by.add(value)
        for value in self._normalize_ids(add_blocks):
            if value != task_id:
                blocks.add(value)

        for value in (remove_blocked_by or []):
            blocked_by.discard(int(value))
        for value in (remove_blocks or []):
            blocks.discard(int(value))

        self._sync_reverse_edges(task_id, sorted(blocked_by), sorted(blocks))
        task = self._load(task_id)

        if normalized_status is not None:
            task["status"] = normalized_status
        if owner is not None:
            task["owner"] = owner.strip()
        self._save(task)

        if task.get("status") == "completed":
            self._clear_dependency(task_id)
            task = self._load(task_id)

        return task

    def get(self, task_id: int) -> dict[str, Any]:
        return self._load(task_id)

    def list_all(self) -> dict[str, Any]:
        tasks = self._iter_tasks()
        ready = [t for t in tasks if t.get("status") == "pending" and not t.get("blockedBy")]
        blocked = [t for t in tasks if t.get("status") == "pending" and t.get("blockedBy")]
        in_progress = [t for t in tasks if t.get("status") == "in_progress"]
        completed = [t for t in tasks if t.get("status") == "completed"]
        return {
            "tasks": tasks,
            "ready": ready,
            "blocked": blocked,
            "in_progress": in_progress,
            "completed": completed,
            "count": len(tasks),
        }


def _to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_task_tools(task_manager: TaskManager) -> list:
    """构建 deepagents 可直接注册的任务工具函数列表。"""

    def task_create(
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        blocks: list[int] | None = None,
        owner: str = "",
    ) -> str:
        """Create a persistent task with optional dependencies.

        Args:
            subject: Task title.
            description: Optional details.
            blocked_by: Task IDs this task depends on.
            blocks: Task IDs that depend on this task.
            owner: Optional owner tag.
        """
        try:
            return _to_json(
                task_manager.create(
                    subject=subject,
                    description=description,
                    blocked_by=blocked_by,
                    blocks=blocks,
                    owner=owner,
                )
            )
        except Exception as exc:
            return f"Error: {exc}"

    def task_update(
        task_id: int,
        status: str | None = None,
        add_blocked_by: list[int] | None = None,
        add_blocks: list[int] | None = None,
        remove_blocked_by: list[int] | None = None,
        remove_blocks: list[int] | None = None,
        owner: str | None = None,
    ) -> str:
        """Update task status/owner and dependency edges.

        Status transitions:
            pending -> in_progress -> completed
        """
        try:
            return _to_json(
                task_manager.update(
                    task_id=task_id,
                    status=status,
                    add_blocked_by=add_blocked_by,
                    add_blocks=add_blocks,
                    remove_blocked_by=remove_blocked_by,
                    remove_blocks=remove_blocks,
                    owner=owner,
                )
            )
        except Exception as exc:
            return f"Error: {exc}"

    def task_get(task_id: int) -> str:
        """Get one task by id."""
        try:
            return _to_json(task_manager.get(task_id))
        except Exception as exc:
            return f"Error: {exc}"

    def task_list() -> str:
        """List all tasks, including ready/blocked/completed groups."""
        try:
            return _to_json(task_manager.list_all())
        except Exception as exc:
            return f"Error: {exc}"

    task_create.__name__ = "task_create"
    task_update.__name__ = "task_update"
    task_get.__name__ = "task_get"
    task_list.__name__ = "task_list"
    return [task_create, task_update, task_get, task_list]
