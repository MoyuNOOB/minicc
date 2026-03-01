"""Controller 层：负责交互命令解析与输入路由拼装。"""

from typing import Callable


def render_active_selection(selected_skill: str | None, selected_subagent: str | None) -> str:
    """渲染当前激活的 skill/subagent 状态。

    Args:
        selected_skill: 当前选中的 skill 名称；为空表示自动选择。
        selected_subagent: 当前选中的 subagent 名称；为空表示自动选择。

    Returns:
        用于终端展示的一行状态文本。
    """
    skill_text = selected_skill or "(auto)"
    subagent_text = selected_subagent or "(auto)"
    return f"[active] skill={skill_text} | subagent={subagent_text}"


def build_routed_input(user_text: str, selected_skill: str | None, selected_subagent: str | None) -> str:
    """根据当前选择生成发送给 agent 的最终输入文本。

    当用户显式选择了 skill/subagent 时，会在输入前注入控制提示，指导 agent 优先采用。

    Args:
        user_text: 用户原始任务文本。
        selected_skill: 当前选中的 skill。
        selected_subagent: 当前选中的 subagent。

    Returns:
        可直接写入会话历史的路由后输入文本。
    """
    hints: list[str] = []
    if selected_skill:
        hints.append(
            f"- Preferred skill: {selected_skill}. If applicable, load and follow this skill first."
        )
    if selected_subagent:
        hints.append(
            f"- Preferred subagent: {selected_subagent}. Use task with subagent_type=\"{selected_subagent}\" for primary execution."
        )

    if not hints:
        return user_text

    return (
        "<controller>\n"
        + "\n".join(hints)
        + "\n</controller>\n\n"
        + "User request:\n"
        + user_text
    )


def parse_selection_command(
    user_input: str,
    selected_skill: str | None,
    selected_subagent: str | None,
    *,
    handle_skill_command: Callable,
    handle_subagent_command: Callable,
    skills: dict[str, str],
    skill_aliases: dict[str, str],
    subagent_by_name: dict,
) -> tuple[str | None, str | None, str | None, bool]:
    """解析 `/skill`、`/subagent`、`/status` 命令并更新选择状态。

    Args:
        user_input: 用户当前输入。
        selected_skill: 进入解析前的 skill 选择。
        selected_subagent: 进入解析前的 subagent 选择。
        handle_skill_command: skill 命令处理函数。
        handle_subagent_command: subagent 命令处理函数。
        skills: 可用 skill 字典。
        skill_aliases: skill 别名映射。
        subagent_by_name: subagent 名称到配置的映射。

    Returns:
        四元组 `(selected_skill, selected_subagent, task_text, handled)`：
        - `selected_skill`: 更新后的 skill 选择。
        - `selected_subagent`: 更新后的 subagent 选择。
        - `task_text`: 若命令后跟任务则返回任务文本，否则为 `None`。
        - `handled`: 是否已被命令处理（`True` 表示主循环不应继续普通调用）。
    """
    stripped = user_input.strip()
    if not stripped.startswith("/"):
        return selected_skill, selected_subagent, stripped, False

    parts = stripped.split(maxsplit=2)
    command = parts[0].lower()

    if command == "/skill":
        selected_skill, task_text, handled = handle_skill_command(
            parts,
            selected_skill,
            selected_subagent,
            skills,
            skill_aliases,
            render_active_selection,
        )
        return selected_skill, selected_subagent, task_text, handled

    if command == "/subagent":
        selected_subagent, task_text, handled = handle_subagent_command(
            parts,
            selected_skill,
            selected_subagent,
            subagent_by_name,
            render_active_selection,
        )
        return selected_skill, selected_subagent, task_text, handled

    if command == "/status":
        print("\n" + render_active_selection(selected_skill, selected_subagent))
        return selected_skill, selected_subagent, None, True

    print("Unknown command. Use /skill, /subagent, or /status.")
    return selected_skill, selected_subagent, None, True
