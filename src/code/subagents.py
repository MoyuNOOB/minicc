"""Subagent 配置与命令处理。"""

from typing import Any


DEFAULT_SUBAGENTS = [
    {
        "name": "frontend-engineer",
        "description": "前端工程师，负责 React/Vue 组件开发、CSS 样式优化、前端性能调优",
        "prompt": "You are a frontend engineer. Focus on component structure, style quality, and frontend performance.",
    },
    {
        "name": "backend-engineer",
        "description": "后端工程师，负责 API 设计、数据层与服务端性能优化",
        "prompt": "You are a backend engineer. Focus on API contracts, data modeling, reliability, and performance.",
    },
    {
        "name": "test-engineer",
        "description": "测试工程师，负责测试用例设计、自动化与质量保障",
        "prompt": "You are a test engineer. Focus on test strategy, automation, and regression protection.",
    },
]


def to_deepagents_subagents(subagents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把本地 subagent 配置转换为 deepagents 需要的字段格式。

    主要规则：若存在 `prompt` 且不存在 `system_prompt`，则重命名为 `system_prompt`。

    Args:
        subagents: 原始 subagent 配置列表。

    Returns:
        转换后的 subagent 配置列表。
    """
    mapped: list[dict[str, Any]] = []
    for item in subagents:
        converted = dict(item)
        if "system_prompt" not in converted and "prompt" in converted:
            converted["system_prompt"] = converted.pop("prompt")
        mapped.append(converted)
    return mapped


def build_subagent_by_name(subagents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按名称构建 subagent 快速索引。

    Args:
        subagents: subagent 配置列表。

    Returns:
        `name -> subagent 配置` 映射。
    """
    return {item["name"]: item for item in subagents}


def build_subagent_descriptions(subagents: list[dict[str, Any]]) -> str:
    """生成系统提示里使用的 subagent 描述文本。

    Args:
        subagents: subagent 配置列表。

    Returns:
        多行文本，每行为一个 subagent 的名称与描述。
    """
    return "\n".join(f"- {item['name']}: {item['description']}" for item in subagents)


def print_subagents(subagent_by_name: dict[str, dict[str, Any]], selected_subagent: str | None) -> None:
    """打印 subagent 列表并标记当前选择。

    Args:
        subagent_by_name: subagent 名称到配置的映射。
        selected_subagent: 当前选中的 subagent。

    Returns:
        None。函数直接打印终端输出。
    """
    print("\nSubagents:")
    for name, item in subagent_by_name.items():
        marker = "*" if name == selected_subagent else " "
        print(f"{marker} {name}: {item['description']}")
    if not subagent_by_name:
        print("(none)")


def handle_subagent_command(
    parts: list[str],
    selected_skill: str | None,
    selected_subagent: str | None,
    subagent_by_name: dict[str, dict[str, Any]],
    render_active_selection,
) -> tuple[str | None, str | None, bool]:
    """处理 `/subagent` 命令。

    支持：列表查看、清空选择、指定 subagent，以及“命令后跟任务”。

    Args:
        parts: 命令分词结果。
        selected_skill: 当前选中的 skill（用于展示状态）。
        selected_subagent: 当前选中的 subagent。
        subagent_by_name: subagent 名称到配置的映射。
        render_active_selection: 状态渲染函数。

    Returns:
        三元组 `(selected_subagent, task_text, handled)`：
        - `selected_subagent`: 更新后的 subagent 选择。
        - `task_text`: 若命令后带任务文本则返回，否则为 `None`。
        - `handled`: 是否已被命令消费。
    """
    if len(parts) == 1 or parts[1].lower() in ("list", "ls"):
        print_subagents(subagent_by_name, selected_subagent)
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_subagent, None, True

    if parts[1].lower() in ("clear", "none"):
        selected_subagent = None
        print("Subagent selection cleared.")
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_subagent, None, True

    subagent_name = parts[1]
    if subagent_name not in subagent_by_name:
        print(f"Unknown subagent: {subagent_name}")
        print_subagents(subagent_by_name, selected_subagent)
        return selected_subagent, None, True

    selected_subagent = subagent_name
    print(f"Selected subagent: {selected_subagent}")

    task_text = parts[2].strip() if len(parts) == 3 else None
    if not task_text:
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_subagent, None, True
    return selected_subagent, task_text, False
