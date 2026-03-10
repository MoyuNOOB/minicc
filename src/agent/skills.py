"""Skill 发现、索引与命令处理。"""

import re
from pathlib import Path


def discover_skills(skills_dir: Path) -> dict[str, str]:
    """扫描 skills 目录并提取名称与描述。

    规则：读取每个 `*/SKILL.md` 的 front-matter，提取 `name` 和 `description`。

    Args:
        skills_dir: skills 根目录路径。

    Returns:
        `skill_name -> description` 映射。
    """
    skills: dict[str, str] = {}
    if not skills_dir.exists():
        return skills

    for md in sorted(skills_dir.glob("*/SKILL.md")):
        folder_name = md.parent.name
        name = folder_name
        description = ""

        try:
            text = md.read_text()
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            if match:
                for line in match.group(1).splitlines():
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    if key == "name" and value:
                        name = value
                    if key == "description" and value:
                        description = value
        except Exception:
            pass

        skills[name] = description

    return skills


def normalize_skill_name(name: str) -> str:
    """归一化 skill 名称，便于别名匹配。

    Args:
        name: 原始 skill 名称。

    Returns:
        归一化后的名称（去首尾空格、小写、`-` 转 `_`）。
    """
    return name.strip().lower().replace("-", "_")


def build_skill_aliases(skills: dict[str, str]) -> dict[str, str]:
    """为 skill 构建多种名称别名。

    Args:
        skills: `skill_name -> description` 映射。

    Returns:
        `alias -> canonical_skill_name` 映射。
    """
    aliases: dict[str, str] = {}
    for skill_name in skills:
        aliases[skill_name] = skill_name
        aliases[skill_name.lower()] = skill_name
        aliases[normalize_skill_name(skill_name)] = skill_name
    return aliases


def build_skill_descriptions(skills: dict[str, str]) -> str:
    """生成系统提示里使用的 skill 描述文本。

    Args:
        skills: `skill_name -> description` 映射。

    Returns:
        多行文本，每行为一个 skill 的名称与描述。
    """
    return "\n".join(
        f"- {name}: {desc or '(no description)'}" for name, desc in sorted(skills.items())
    ) or "(no skills available)"


def print_skills(skills: dict[str, str], selected_skill: str | None) -> None:
    """打印 skill 列表并标记当前选择。

    Args:
        skills: 可用 skill 映射。
        selected_skill: 当前选中的 skill。

    Returns:
        None。函数直接打印终端输出。
    """
    print("\nSkills:")
    for name, description in sorted(skills.items()):
        marker = "*" if name == selected_skill else " "
        print(f"{marker} {name}: {description or '(no description)'}")
    if not skills:
        print("(none)")


def handle_skill_command(
    parts: list[str],
    selected_skill: str | None,
    selected_subagent: str | None,
    skills: dict[str, str],
    skill_aliases: dict[str, str],
    render_active_selection,
) -> tuple[str | None, str | None, bool]:
    """处理 `/skill` 命令。

    支持：列表查看、清空选择、指定 skill，以及“命令后跟任务”。

    Args:
        parts: 命令分词结果。
        selected_skill: 当前选中的 skill。
        selected_subagent: 当前选中的 subagent（用于展示状态）。
        skills: 可用 skill 映射。
        skill_aliases: alias 到 canonical 名称映射。
        render_active_selection: 状态渲染函数。

    Returns:
        三元组 `(selected_skill, task_text, handled)`：
        - `selected_skill`: 更新后的 skill 选择。
        - `task_text`: 命令后附带的任务文本；没有则为 `None`。
        - `handled`: 是否已被命令消费。
    """
    if len(parts) == 1 or parts[1].lower() in ("list", "ls"):
        print_skills(skills, selected_skill)
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_skill, None, True

    if parts[1].lower() in ("clear", "none"):
        selected_skill = None
        print("Skill selection cleared.")
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_skill, None, True

    skill_input = parts[1]
    skill_name = skill_aliases.get(skill_input) or skill_aliases.get(normalize_skill_name(skill_input))
    if not skill_name:
        print(f"Unknown skill: {skill_input}")
        print_skills(skills, selected_skill)
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_skill, None, True

    selected_skill = skill_name
    print(f"Selected skill: {selected_skill}")

    task_text = parts[2].strip() if len(parts) == 3 else None
    if not task_text:
        print(render_active_selection(selected_skill, selected_subagent))
        return selected_skill, None, True
    return selected_skill, task_text, False
