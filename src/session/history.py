"""会话历史持久化与恢复。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _to_serializable_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    try:
        dumped = message.model_dump()
        if isinstance(dumped, dict):
            return dumped
    except Exception:
        pass
    role = getattr(message, "type", getattr(message, "role", "assistant"))
    content = getattr(message, "content", "")
    return {"role": role, "content": content}


def save_session_history(path: Path, messages: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for message in messages:
            payload = _to_serializable_message(message)
            file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def load_session_history(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not path.exists():
        return messages
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
    return messages


def list_history_files(history_dir: Path, limit: int = 20, exclude: Path | None = None) -> list[Path]:
    candidates = [path for path in history_dir.glob("*.jsonl") if path.is_file()]
    if exclude is not None:
        candidates = [path for path in candidates if path.resolve() != exclude.resolve()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[: max(limit, 1)]


def find_latest_history_file(history_dir: Path, current_file: Path) -> Path | None:
    items = list_history_files(history_dir, limit=1, exclude=current_file)
    if not items:
        return None
    return items[0]


def count_history_messages(path: Path) -> int:
    return len(load_session_history(path))

