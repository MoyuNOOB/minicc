"""Autonomous teammate coordination: idle polling, auto-claim, identity reinjection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from src.code.task_system import TaskManager
from src.code.teams import MessageBus, TeammateManager


@dataclass
class AutonomousAgentManager:
    """Coordinate autonomous teammate behavior against inbox + task board."""

    teammates: TeammateManager
    tasks: TaskManager
    bus: MessageBus
    idle_timeout_seconds: int = 60
    poll_interval_seconds: int = 5

    def scan_unclaimed_tasks(self) -> list[dict[str, Any]]:
        """Find pending, unowned, unblocked tasks."""
        all_tasks = self.tasks.list_all().get("tasks", [])
        unclaimed: list[dict[str, Any]] = []
        for task in all_tasks:
            if task.get("status") != "pending":
                continue
            if task.get("owner"):
                continue
            if task.get("blockedBy"):
                continue
            unclaimed.append(task)
        unclaimed.sort(key=lambda item: int(item.get("id", 0)))
        return unclaimed

    def claim_task(self, task_id: int, owner: str) -> dict[str, Any]:
        """Claim a task for a teammate and mark in progress."""
        return self.tasks.claim_task(task_id=task_id, owner=owner)

    def inject_identity_if_compacted(
        self,
        messages: list[dict[str, str]],
        *,
        name: str,
        role: str,
        team_name: str = "team",
    ) -> bool:
        """Re-inject teammate identity when context is too short after compaction."""
        if len(messages) > 3:
            return False

        identity_user = {
            "role": "user",
            "content": (
                f"<identity>You are '{name}', role: {role}, team: {team_name}. "
                "Continue your work and keep ownership consistent.</identity>"
            ),
        }
        identity_assistant = {
            "role": "assistant",
            "content": f"I am {name} ({role}). Continuing.",
        }
        messages.insert(0, identity_user)
        messages.insert(1, identity_assistant)
        return True

    def tick_idle_teammates(self) -> list[dict[str, Any]]:
        """One autonomous scheduler tick: inbox first, then auto-claim from task board."""
        events: list[dict[str, Any]] = []
        members = self.teammates.list_members()

        for member in members:
            name = member.get("name")
            status = member.get("status")
            role = member.get("role")
            if not isinstance(name, str) or not name:
                continue
            if status not in ("idle", "working"):
                continue

            inbox_messages = self.bus.read_inbox(name, drain=False)
            if inbox_messages:
                self.teammates.set_status(name, "working")
                events.append(
                    {
                        "event": "teammate.resume.inbox",
                        "name": name,
                        "role": role,
                        "inbox_count": len(inbox_messages),
                        "ts": time.time(),
                    }
                )
                continue

            if status != "idle":
                continue

            unclaimed = self.scan_unclaimed_tasks()
            if not unclaimed:
                continue

            selected = unclaimed[0]
            claimed = self.claim_task(int(selected["id"]), owner=name)
            self.teammates.set_status(name, "working")
            self.bus.send(
                sender="lead",
                to=name,
                content=f"Auto-claimed task #{claimed['id']}: {claimed.get('subject', '')}",
                msg_type="auto_claim",
                extra={"task_id": claimed["id"]},
            )
            events.append(
                {
                    "event": "teammate.auto_claim",
                    "name": name,
                    "role": role,
                    "task": {"id": claimed["id"], "subject": claimed.get("subject", "")},
                    "ts": time.time(),
                }
            )

        return events


def _to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def inject_autonomous_events(history: list[dict[str, str]], events: list[dict[str, Any]]) -> int:
    """Inject autonomous scheduler events into conversation context."""
    if not events:
        return 0

    history.append(
        {
            "role": "user",
            "content": f"<autonomy-events>\n{_to_json(events)}\n</autonomy-events>",
        }
    )
    history.append({"role": "assistant", "content": "Noted autonomous team events."})
    return len(events)


def build_autonomous_tools(manager: AutonomousAgentManager) -> list:
    """Expose autonomous team capabilities as tools."""

    def idle(reason: str = "") -> str:
        """Signal entering idle phase. Use when waiting for inbox/tasks."""
        return _to_json({"status": "idle", "reason": reason, "ts": time.time()})

    def claim_task(task_id: int, owner: str) -> str:
        """Claim an unowned task for a teammate."""
        try:
            return _to_json(manager.claim_task(task_id=task_id, owner=owner))
        except Exception as exc:
            return f"Error: {exc}"

    def auto_scan_unclaimed_tasks(limit: int = 20) -> str:
        """Scan task board for pending, unowned, unblocked tasks."""
        try:
            tasks = manager.scan_unclaimed_tasks()[: max(1, int(limit))]
            return _to_json({"count": len(tasks), "tasks": tasks})
        except Exception as exc:
            return f"Error: {exc}"

    def team_auto_tick() -> str:
        """Run one autonomous scheduler tick for idle teammates."""
        try:
            events = manager.tick_idle_teammates()
            return _to_json({"count": len(events), "events": events})
        except Exception as exc:
            return f"Error: {exc}"

    idle.__name__ = "idle"
    claim_task.__name__ = "claim_task"
    auto_scan_unclaimed_tasks.__name__ = "auto_scan_unclaimed_tasks"
    team_auto_tick.__name__ = "team_auto_tick"
    return [idle, claim_task, auto_scan_unclaimed_tasks, team_auto_tick]
