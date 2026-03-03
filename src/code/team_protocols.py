"""Team coordination protocols: shutdown handshake + plan approval FSM."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.code.teams import MessageBus, TeammateManager


PROTOCOLS_FILE = "protocols.json"


@dataclass
class TeamProtocolManager:
    """Persistent request-response protocol manager for team coordination."""

    team_dir: Path
    bus: MessageBus
    teammates: TeammateManager

    def __post_init__(self) -> None:
        self.team_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.team_dir / PROTOCOLS_FILE
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("shutdown_requests", {})
                    data.setdefault("plan_requests", {})
                    return data
            except Exception:
                pass
        return {"shutdown_requests": {}, "plan_requests": {}}

    def _save_state(self) -> None:
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _new_request_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def request_shutdown(self, teammate: str, sender: str = "lead", reason: str = "") -> dict[str, Any]:
        target = (teammate or "").strip()
        if not target:
            raise ValueError("teammate is required")

        req_id = self._new_request_id()
        request = {
            "request_id": req_id,
            "type": "shutdown_request",
            "from": sender,
            "target": target,
            "reason": reason,
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.state["shutdown_requests"][req_id] = request
        self._save_state()

        self.bus.send(
            sender=sender,
            to=target,
            content=reason or "Please shut down gracefully.",
            msg_type="shutdown_request",
            extra={"request_id": req_id},
        )
        return request

    def respond_shutdown(
        self,
        request_id: str,
        approve: bool,
        sender: str,
        reason: str = "",
    ) -> dict[str, Any]:
        req_id = (request_id or "").strip()
        request = self.state["shutdown_requests"].get(req_id)
        if not request:
            raise ValueError(f"shutdown request {req_id} not found")

        request["status"] = "approved" if approve else "rejected"
        request["responder"] = sender
        request["response_reason"] = reason
        request["updated_at"] = time.time()
        self._save_state()

        self.bus.send(
            sender=sender,
            to=request.get("from", "lead"),
            content=reason,
            msg_type="shutdown_response",
            extra={"request_id": req_id, "approve": bool(approve)},
        )

        if approve:
            target = request.get("target")
            if isinstance(target, str) and target:
                try:
                    self.teammates.set_status(target, "shutdown")
                except Exception:
                    pass

        return request

    def submit_plan(self, sender: str, plan: str, to: str = "lead") -> dict[str, Any]:
        body = (plan or "").strip()
        if not body:
            raise ValueError("plan is required")

        req_id = self._new_request_id()
        request = {
            "request_id": req_id,
            "type": "plan_approval_request",
            "from": sender,
            "to": to,
            "plan": body,
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.state["plan_requests"][req_id] = request
        self._save_state()

        self.bus.send(
            sender=sender,
            to=to,
            content=body,
            msg_type="plan_approval_request",
            extra={"request_id": req_id},
        )
        return request

    def review_plan(self, request_id: str, approve: bool, reviewer: str = "lead", feedback: str = "") -> dict[str, Any]:
        req_id = (request_id or "").strip()
        request = self.state["plan_requests"].get(req_id)
        if not request:
            raise ValueError(f"plan request {req_id} not found")

        request["status"] = "approved" if approve else "rejected"
        request["reviewer"] = reviewer
        request["feedback"] = feedback
        request["updated_at"] = time.time()
        self._save_state()

        self.bus.send(
            sender=reviewer,
            to=request.get("from", "unknown"),
            content=feedback,
            msg_type="plan_approval_response",
            extra={"request_id": req_id, "approve": bool(approve)},
        )
        return request

    def list_requests(self, kind: str = "all", status: str | None = None) -> dict[str, Any]:
        shutdown = list(self.state.get("shutdown_requests", {}).values())
        plans = list(self.state.get("plan_requests", {}).values())

        if status:
            shutdown = [item for item in shutdown if item.get("status") == status]
            plans = [item for item in plans if item.get("status") == status]

        if kind == "shutdown":
            return {"kind": "shutdown", "count": len(shutdown), "items": shutdown}
        if kind == "plan":
            return {"kind": "plan", "count": len(plans), "items": plans}
        return {
            "kind": "all",
            "shutdown_count": len(shutdown),
            "plan_count": len(plans),
            "shutdown": shutdown,
            "plans": plans,
        }


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_team_protocol_tools(manager: TeamProtocolManager) -> list:
    """Build deepagents tools for team request-response protocols."""

    def team_shutdown_request(teammate: str, sender: str = "lead", reason: str = "") -> str:
        """Send shutdown request to teammate. Status starts as pending."""
        try:
            req = manager.request_shutdown(teammate=teammate, sender=sender, reason=reason)
            return _to_json(req)
        except Exception as exc:
            return f"Error: {exc}"

    def team_shutdown_response(request_id: str, approve: bool, sender: str = "lead", reason: str = "") -> str:
        """Approve/reject shutdown request by request_id."""
        try:
            req = manager.respond_shutdown(
                request_id=request_id,
                approve=approve,
                sender=sender,
                reason=reason,
            )
            return _to_json(req)
        except Exception as exc:
            return f"Error: {exc}"

    def team_plan_submit(plan: str, sender: str = "lead", to: str = "lead") -> str:
        """Submit plan for approval; creates pending request with request_id."""
        try:
            req = manager.submit_plan(sender=sender, plan=plan, to=to)
            return _to_json(req)
        except Exception as exc:
            return f"Error: {exc}"

    def team_plan_review(request_id: str, approve: bool, reviewer: str = "lead", feedback: str = "") -> str:
        """Review pending plan request and approve/reject it."""
        try:
            req = manager.review_plan(
                request_id=request_id,
                approve=approve,
                reviewer=reviewer,
                feedback=feedback,
            )
            return _to_json(req)
        except Exception as exc:
            return f"Error: {exc}"

    def team_protocol_list(kind: str = "all", status: str | None = None) -> str:
        """List shutdown/plan protocol requests and their FSM statuses."""
        try:
            return _to_json(manager.list_requests(kind=kind, status=status))
        except Exception as exc:
            return f"Error: {exc}"

    team_shutdown_request.__name__ = "team_shutdown_request"
    team_shutdown_response.__name__ = "team_shutdown_response"
    team_plan_submit.__name__ = "team_plan_submit"
    team_plan_review.__name__ = "team_plan_review"
    team_protocol_list.__name__ = "team_protocol_list"
    return [
        team_shutdown_request,
        team_shutdown_response,
        team_plan_submit,
        team_plan_review,
        team_protocol_list,
    ]
