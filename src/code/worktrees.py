"""Git worktree manager with task binding and lifecycle events."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.code.task_system import TaskManager


INDEX_FILE = "index.json"
EVENTS_FILE = "events.jsonl"


@dataclass
class WorktreeManager:
    """Manage isolated git worktrees and bind them to task ids."""

    repo_root: Path
    worktrees_dir: Path
    tasks: TaskManager

    def __post_init__(self) -> None:
        self.repo_root = self.repo_root.resolve()
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.worktrees_dir / INDEX_FILE
        self.events_path = self.worktrees_dir / EVENTS_FILE
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not self.events_path.exists():
            self.events_path.write_text("", encoding="utf-8")

    def _load_index(self) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("worktrees"), list):
                return data
        except Exception:
            pass
        return {"worktrees": []}

    def _save_index(self, index: dict[str, Any]) -> None:
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit(self, event: str, **payload: Any) -> dict[str, Any]:
        item = {"event": event, "ts": time.time()}
        item.update(payload)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def _run_git(self, args: list[str]) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as exc:
            return False, f"Error: {exc}"

        output = (result.stdout + result.stderr).strip() or "(no output)"
        if result.returncode != 0:
            return False, output
        return True, output

    def _find_entry(self, name: str, index: dict[str, Any]) -> dict[str, Any] | None:
        for item in index.get("worktrees", []):
            if item.get("name") == name:
                return item
        return None

    def create(
        self,
        name: str,
        task_id: int | None = None,
        branch: str | None = None,
        base_ref: str = "HEAD",
    ) -> dict[str, Any]:
        worktree_name = (name or "").strip()
        if not worktree_name:
            raise ValueError("name is required")

        index = self._load_index()
        if self._find_entry(worktree_name, index):
            raise ValueError(f"Worktree '{worktree_name}' already exists")

        branch_name = (branch or f"wt/{worktree_name}").strip()
        worktree_path = self.worktrees_dir / worktree_name

        self._emit(
            "worktree.create.before",
            worktree={"name": worktree_name, "path": str(worktree_path), "branch": branch_name},
            task={"id": task_id} if task_id is not None else None,
        )

        ok, output = self._run_git(["worktree", "add", "-b", branch_name, str(worktree_path), base_ref])
        if not ok:
            self._emit("worktree.create.failed", worktree={"name": worktree_name}, error=output)
            raise RuntimeError(output)

        now = time.time()
        entry = {
            "name": worktree_name,
            "path": str(worktree_path),
            "branch": branch_name,
            "task_id": task_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        index.setdefault("worktrees", []).append(entry)
        self._save_index(index)

        task_payload: dict[str, Any] | None = None
        if task_id is not None:
            bound = self.tasks.bind_worktree(task_id=task_id, worktree=worktree_name)
            task_payload = {"id": bound["id"], "status": bound.get("status"), "worktree": bound.get("worktree")}

        self._emit("worktree.create.after", worktree=entry, task=task_payload, git_output=output)
        return entry

    def list_worktrees(self, status: str | None = None) -> list[dict[str, Any]]:
        index = self._load_index()
        items = list(index.get("worktrees", []))
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: item.get("name", ""))
        return items

    def run(self, name: str, command: str, timeout_seconds: int = 300) -> dict[str, Any]:
        index = self._load_index()
        entry = self._find_entry((name or "").strip(), index)
        if not entry:
            raise ValueError(f"Worktree '{name}' not found")

        worktree_path = Path(entry["path"]).resolve()
        if not worktree_path.exists():
            raise RuntimeError(f"Worktree path not found: {worktree_path}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return {"name": name, "command": command, "status": "timeout", "output": f"Error: Timeout ({timeout_seconds}s)"}

        output = (result.stdout + result.stderr).strip() or "(no output)"
        if len(output) > 50000:
            output = output[:50000]

        return {
            "name": name,
            "command": command,
            "status": "ok" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "output": output,
        }

    def keep(self, name: str) -> dict[str, Any]:
        index = self._load_index()
        entry = self._find_entry((name or "").strip(), index)
        if not entry:
            raise ValueError(f"Worktree '{name}' not found")

        entry["status"] = "kept"
        entry["updated_at"] = time.time()
        self._save_index(index)
        self._emit("worktree.keep", worktree={"name": entry["name"], "task_id": entry.get("task_id")})
        return entry

    def remove(self, name: str, force: bool = False, complete_task: bool = False) -> dict[str, Any]:
        worktree_name = (name or "").strip()
        index = self._load_index()
        entry = self._find_entry(worktree_name, index)
        if not entry:
            raise ValueError(f"Worktree '{worktree_name}' not found")

        self._emit("worktree.remove.before", worktree={"name": worktree_name}, options={"force": force})

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(entry["path"]))
        ok, output = self._run_git(args)
        if not ok:
            self._emit("worktree.remove.failed", worktree={"name": worktree_name}, error=output)
            raise RuntimeError(output)

        entry["status"] = "removed"
        entry["updated_at"] = time.time()
        self._save_index(index)

        task_payload: dict[str, Any] | None = None
        task_id = entry.get("task_id")
        if isinstance(task_id, int):
            if complete_task:
                updated = self.tasks.update(task_id=task_id, status="completed")
                self.tasks.unbind_worktree(task_id)
                task_payload = {"id": updated["id"], "status": "completed", "worktree": ""}
                self._emit("task.completed", task=task_payload)
            else:
                self.tasks.unbind_worktree(task_id)
                task_payload = {"id": task_id, "status": "in_progress", "worktree": ""}

        self._emit("worktree.remove.after", worktree={"name": worktree_name, "status": "removed"}, task=task_payload)
        return entry

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        lines = [line for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        parsed: list[dict[str, Any]] = []
        for line in lines[-max(1, int(limit)) :]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    parsed.append(item)
            except Exception:
                continue
        return parsed


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_worktree_tools(manager: WorktreeManager) -> list:
    """Build deepagents tools for worktree lifecycle and task isolation."""

    def worktree_create(name: str, task_id: int | None = None, branch: str | None = None, base_ref: str = "HEAD") -> str:
        """Create isolated git worktree and optionally bind task_id."""
        try:
            return _to_json(manager.create(name=name, task_id=task_id, branch=branch, base_ref=base_ref))
        except Exception as exc:
            return f"Error: {exc}"

    def worktree_list(status: str | None = None) -> str:
        """List worktrees from registry index."""
        try:
            items = manager.list_worktrees(status=status)
            return _to_json({"count": len(items), "worktrees": items})
        except Exception as exc:
            return f"Error: {exc}"

    def worktree_run(name: str, command: str, timeout_seconds: int = 300) -> str:
        """Run command inside specific worktree cwd."""
        try:
            return _to_json(manager.run(name=name, command=command, timeout_seconds=timeout_seconds))
        except Exception as exc:
            return f"Error: {exc}"

    def worktree_keep(name: str) -> str:
        """Mark a worktree as kept for future reuse."""
        try:
            return _to_json(manager.keep(name=name))
        except Exception as exc:
            return f"Error: {exc}"

    def worktree_remove(name: str, force: bool = False, complete_task: bool = False) -> str:
        """Remove worktree; optionally complete bound task."""
        try:
            return _to_json(manager.remove(name=name, force=force, complete_task=complete_task))
        except Exception as exc:
            return f"Error: {exc}"

    def worktree_events(limit: int = 200) -> str:
        """Read worktree lifecycle event log."""
        try:
            items = manager.events(limit=limit)
            return _to_json({"count": len(items), "events": items})
        except Exception as exc:
            return f"Error: {exc}"

    worktree_create.__name__ = "worktree_create"
    worktree_list.__name__ = "worktree_list"
    worktree_run.__name__ = "worktree_run"
    worktree_keep.__name__ = "worktree_keep"
    worktree_remove.__name__ = "worktree_remove"
    worktree_events.__name__ = "worktree_events"
    return [worktree_create, worktree_list, worktree_run, worktree_keep, worktree_remove, worktree_events]
