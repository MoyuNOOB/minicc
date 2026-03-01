"""消息渲染与工具调用流处理。"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.code.todos import TodoRenderState, render_todos, todos_updates_from_messages


@dataclass
class ToolRenderState:
    """工具调用渲染去重状态。

    Attributes:
        printed_skill_calls: 已输出过的 skill 调用 ID。
        printed_subagent_calls: 已输出过的 subagent/task 调用 ID。
    """

    printed_skill_calls: set[str] = field(default_factory=set)
    printed_subagent_calls: set[str] = field(default_factory=set)


def normalize_content(content: object) -> str | None:
    """把消息内容归一化为字符串。

    Args:
        content: 可能是字符串，或 deepagents 的分段内容列表。

    Returns:
        提取后的纯文本；无法提取时返回 `None`。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(text for text in texts if text)
    return None


def extract_subagent_type(args: object) -> str | None:
    """从 task 工具参数中提取 subagent 类型。

    Args:
        args: 工具调用参数对象。

    Returns:
        子代理名称；无可用字段时返回 `None`。
    """
    if not isinstance(args, dict):
        return None
    return args.get("subagent_type") or args.get("subagent") or args.get("sub_agent")


def render_special_tool_calls(
    messages: list,
    start_index: int,
    state: ToolRenderState,
    subagent_skills: dict[str, list[str]],
) -> None:
    """渲染特殊工具调用（skill/task）信息。

    Args:
        messages: 当前消息列表。
        start_index: 仅从该下标之后开始扫描。
        state: 渲染去重状态。
        subagent_skills: subagent 与技能名列表映射，用于打印提示。

    Returns:
        None。函数直接打印终端输出。
    """
    for message in messages[start_index:]:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        if not tool_calls:
            continue

        for call in tool_calls:
            call_name = (call.get("name") or "").lower()
            call_id = call.get("id")
            args = call.get("args") if isinstance(call, dict) else None

            if "skill" in call_name:
                if call_id and call_id in state.printed_skill_calls:
                    continue
                skill_name = args.get("name") if isinstance(args, dict) else None
                print(f"\n[skill] name: {skill_name or 'unknown'}")
                if call_id:
                    state.printed_skill_calls.add(call_id)

            if call_name == "task":
                if call_id and call_id in state.printed_subagent_calls:
                    continue
                subagent = extract_subagent_type(args)
                skills = subagent_skills.get(subagent, [])
                print(f"\n[task] subagent: {subagent or 'unknown'} | skills: {', '.join(skills) or '(none)'}")
                if call_id:
                    state.printed_subagent_calls.add(call_id)


def print_turn(
    messages: list,
    start_index: int,
    todo_state: TodoRenderState,
    tool_state: ToolRenderState,
    subagent_skills: dict[str, list[str]],
) -> None:
    """打印本轮新增消息，并渲染 todo 与关键工具调用。

    Args:
        messages: 完整消息列表。
        start_index: 本轮新增消息起始下标。
        todo_state: todo 渲染状态容器。
        tool_state: 工具调用渲染状态容器。
        subagent_skills: subagent 到技能列表映射。

    Returns:
        None。函数通过打印完成展示。
    """
    if not isinstance(messages, list):
        print(messages)
        return

    updates = todos_updates_from_messages(messages, start_index)
    updates_by_index: dict[int, list[list[dict[str, Any]]]] = {}
    for idx, todos in updates:
        updates_by_index.setdefault(idx, []).append(todos)

    for idx, message in enumerate(messages):
        if idx < start_index:
            continue
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        text = normalize_content(content)
        if text:
            print(text)
        for todos in updates_by_index.get(idx, []):
            if todos == todo_state.last_todos:
                continue
            todo_state.last_todos = todos
            render_todos(todos)

    render_special_tool_calls(messages, start_index, tool_state, subagent_skills)


def stream_with_retry(
    agent: Any,
    history: list[dict[str, str]],
    start_index: int,
    recursion_limit: int,
    on_messages: Callable[[list, int], None],
) -> list[Any]:
    """以流式方式调用 agent，并对 JSON 解析错误做一次重试。

    Args:
        agent: deepagents agent 实例。
        history: 会话消息历史。
        start_index: 本轮开始打印的消息下标。
        recursion_limit: agent 流调用时的递归限制。
        on_messages: 每次有新增消息时触发的回调。

    Returns:
        最终完整消息列表（用于回写到 `history`）。

    Raises:
        json.JSONDecodeError: 两次尝试仍解析失败时抛出。
        RuntimeError: 未取得有效结果时抛出。
    """
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            printed_index = start_index
            final_messages: list[Any] = history

            for chunk in agent.stream(
                {"messages": history},
                {"recursion_limit": recursion_limit},
                stream_mode="values",
            ):
                if not isinstance(chunk, dict):
                    continue
                messages = chunk.get("messages")
                if not isinstance(messages, list):
                    continue

                if len(messages) > printed_index:
                    on_messages(messages, printed_index)
                    printed_index = len(messages)

                final_messages = messages

            last_error = None
            return final_messages
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.6)
                continue
            raise

    raise last_error or RuntimeError("Empty result from agent stream")
