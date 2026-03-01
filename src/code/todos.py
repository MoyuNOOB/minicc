"""Todo 相关解析与渲染工具。"""

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TodoRenderState:
    """Todo 渲染状态。

    Attributes:
        last_todos: 上一次已渲染的 todo 列表，用于避免重复输出。
    """

    last_todos: list[dict[str, Any]] = field(default_factory=list)


def todos_updates_from_messages(messages: list, start_index: int = 0) -> list[tuple[int, list[dict[str, Any]]]]:
    """从消息列表中提取 todo 更新事件。

    支持两类来源：
    1) `write_todos/todowrite` 工具调用参数中的 `todos`；
    2) 文本内容里 `Updated todo list to ...` 的回显。

    Args:
        messages: Agent 消息列表。
        start_index: 起始扫描下标，仅扫描该下标及之后的消息。

    Returns:
        形如 `(message_index, todos)` 的更新事件列表。
    """
    updates: list[tuple[int, list[dict[str, Any]]]] = []
    for idx, message in enumerate(messages or []):
        if idx < start_index:
            continue

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        if tool_calls:
            for call in tool_calls:
                call_name = (call.get("name") or "").lower()
                if call_name not in ("write_todos", "todowrite"):
                    continue
                args = call.get("args", {})
                if isinstance(args, dict) and isinstance(args.get("todos"), list):
                    updates.append((idx, args["todos"]))

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if not isinstance(content, str):
            continue
        marker = "Updated todo list to "
        if marker in content:
            raw = content.split(marker, 1)[-1].strip()
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, list):
                    updates.append((idx, parsed))
            except Exception:
                pass
    return updates


def render_todos(todos: list[dict[str, Any]]) -> None:
    """将 todo 列表格式化打印到终端。

    Args:
        todos: todo 字典列表，通常包含 `status/content/activeForm` 字段。

    Returns:
        None。函数直接执行打印副作用。
    """
    if not todos:
        print("\nTodos: (empty)")
        return
    print("\nTodos:")
    for item in todos:
        status = item.get("status", "unknown")
        content = item.get("content", "")
        active_form = item.get("activeForm") or item.get("active_form") or ""
        row = f"- [{status}] {content}"
        if active_form and status == "in_progress":
            row += f" <- {active_form}"
        print(row)
