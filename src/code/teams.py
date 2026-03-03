"""Agent team primitives: teammate roster + JSONL message bus."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEAM_CONFIG_FILE = "config.json"


class MessageBus:
    """Append-only JSONL inbox bus with drain-on-read semantics."""

    def __init__(self, inbox_dir: Path):
        self.inbox_dir = inbox_dir
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def _inbox_path(self, name: str) -> Path:
        return self.inbox_dir / f"{name}.jsonl"

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": msg_type,
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            payload.update(extra)

        path = self._inbox_path(to)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def broadcast(
        self,
        sender: str,
        recipients: list[str],
        content: str,
        msg_type: str = "broadcast",
        extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        for recipient in recipients:
            sent.append(self.send(sender, recipient, content, msg_type=msg_type, extra=extra))
        return sent

    def read_inbox(self, name: str, drain: bool = True) -> list[dict[str, Any]]:
        path = self._inbox_path(name)
        if not path.exists():
            return []

        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        messages: list[dict[str, Any]] = []
        for line in lines:
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    messages.append(parsed)
            except Exception:
                continue

        if drain:
            path.write_text("", encoding="utf-8")
        return messages


@dataclass
class TeammateManager:
    """Persistent team roster manager backed by .team/config.json."""

    team_dir: Path

    def __post_init__(self) -> None:
        self.team_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.team_dir / TEAM_CONFIG_FILE
        self.config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("members"), list):
                    return data
            except Exception:
                pass
        return {"members": []}

    def _save_config(self) -> None:
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_member(self, name: str) -> dict[str, Any] | None:
        for member in self.config.get("members", []):
            if member.get("name") == name:
                return member
        return None

    def spawn(self, name: str, role: str, prompt: str = "") -> dict[str, Any]:
        teammate_name = (name or "").strip()
        teammate_role = (role or "").strip()
        if not teammate_name or not teammate_role:
            raise ValueError("name and role are required")

        existing = self._find_member(teammate_name)
        if existing and existing.get("status") != "shutdown":
            raise ValueError(f"Teammate '{teammate_name}' already exists")

        now = time.time()
        member = {
            "name": teammate_name,
            "role": teammate_role,
            "prompt": prompt.strip(),
            "status": "idle",
            "created_at": now,
            "updated_at": now,
        }
        if existing:
            self.config["members"] = [
                member if item.get("name") == teammate_name else item for item in self.config.get("members", [])
            ]
        else:
            self.config.setdefault("members", []).append(member)
        self._save_config()
        return member

    def set_status(self, name: str, status: str) -> dict[str, Any]:
        member = self._find_member(name)
        if not member:
            raise ValueError(f"Teammate '{name}' not found")
        member["status"] = status
        member["updated_at"] = time.time()
        self._save_config()
        return member

    def list_members(self) -> list[dict[str, Any]]:
        members = list(self.config.get("members", []))
        members.sort(key=lambda item: item.get("name", ""))
        return members

    def active_teammates(self) -> list[str]:
        result: list[str] = []
        for member in self.config.get("members", []):
            status = member.get("status")
            name = member.get("name")
            if isinstance(name, str) and name and status != "shutdown":
                result.append(name)
        return sorted(set(result))


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def inject_lead_inbox_messages(history: list[dict[str, str]], bus: MessageBus, lead_name: str = "lead") -> int:
    """Drain lead inbox and inject into conversation context."""
    inbox_messages = bus.read_inbox(lead_name, drain=True)
    if not inbox_messages:
        return 0

    history.append(
        {
            "role": "user",
            "content": f"<team-inbox>\n{_to_json(inbox_messages)}\n</team-inbox>",
        }
    )
    history.append({"role": "assistant", "content": "Noted team inbox messages."})
    return len(inbox_messages)


def build_team_tools(manager: TeammateManager, bus: MessageBus) -> list:
    """Build deepagents tools for team roster + messaging."""

    def team_spawn(name: str, role: str, prompt: str = "") -> str:
        """Spawn (register) a persistent teammate in the team roster."""
        try:
            member = manager.spawn(name=name, role=role, prompt=prompt)
            return _to_json({"message": f"Spawned teammate '{name}'", "member": member})
        except Exception as exc:
            return f"Error: {exc}"

    def team_list() -> str:
        """List team roster and teammate statuses."""
        try:
            members = manager.list_members()
            return _to_json({"members": members, "count": len(members)})
        except Exception as exc:
            return f"Error: {exc}"

    def team_send(
        to: str,
        content: str,
        sender: str = "lead",
        msg_type: str = "message",
        broadcast: bool = False,
    ) -> str:
        """Send message to one teammate or broadcast to all active teammates."""
        try:
            body = (content or "").strip()
            if not body:
                return "Error: content is required"

            if broadcast:
                recipients = [name for name in manager.active_teammates() if name != sender]
                sent = bus.broadcast(sender=sender, recipients=recipients, content=body, msg_type=msg_type)
                return _to_json({"message": "broadcast sent", "count": len(sent), "items": sent})

            target = (to or "").strip()
            if not target:
                return "Error: to is required when broadcast=false"
            item = bus.send(sender=sender, to=target, content=body, msg_type=msg_type)
            return _to_json({"message": "sent", "item": item})
        except Exception as exc:
            return f"Error: {exc}"

    def team_read_inbox(name: str = "lead", drain: bool = True) -> str:
        """Read a teammate inbox; by default drains messages after reading."""
        try:
            items = bus.read_inbox(name=name, drain=drain)
            return _to_json({"name": name, "count": len(items), "messages": items, "drain": drain})
        except Exception as exc:
            return f"Error: {exc}"

    team_spawn.__name__ = "team_spawn"
    team_list.__name__ = "team_list"
    team_send.__name__ = "team_send"
    team_read_inbox.__name__ = "team_read_inbox"
    return [team_spawn, team_list, team_send, team_read_inbox]
