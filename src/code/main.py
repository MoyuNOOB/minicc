"""MiniClaude 主入口：初始化 agent 并驱动交互主循环。"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

from src.code.prompts import SYSTEM_PROMPT_UNIFIED
from src.code.sandbox import SimpleSandboxBackend
from src.code.controller import build_routed_input, parse_selection_command, render_active_selection
from src.code.skills import (
    build_skill_aliases,
    build_skill_descriptions,
    discover_skills,
    handle_skill_command,
)
from src.code.subagents import (
    DEFAULT_SUBAGENTS,
    build_subagent_by_name,
    build_subagent_descriptions,
    handle_subagent_command,
    to_deepagents_subagents,
)
from src.code.todos import TodoRenderState
from src.code.tools import ToolRenderState, print_turn, stream_with_retry


ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

try:
    from src.tools.web_search import internet_search
except Exception:
    internet_search = None

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL_NAME", "kimi-k2-turbo-preview")
SANDBOX_REFRESH_EACH_EXECUTE = os.getenv("SANDBOX_REFRESH_EACH_EXECUTE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
WORKDIR = Path.cwd()
SKILLS_DIR = PROJECT_ROOT / "src" / "skills"
BACKEND = SimpleSandboxBackend(
    root_dir=PROJECT_ROOT,
    virtual_mode=True,
    refresh_each_execute=SANDBOX_REFRESH_EACH_EXECUTE,
)

SKILLS = discover_skills(SKILLS_DIR)
SKILL_ALIASES = build_skill_aliases(SKILLS)

SUBAGENTS = DEFAULT_SUBAGENTS
DEEPAGENT_SUBAGENTS = to_deepagents_subagents(SUBAGENTS)
SUBAGENT_BY_NAME = build_subagent_by_name(SUBAGENTS)
SUBAGENT_SKILLS = {
    "frontend-engineer": ["frontend-style-optimizer"],
    "backend-engineer": ["mcp-builder", "code-reviewer"],
    "test-engineer": ["unit-testing", "smoke-testing", "code-reviewer"],
}
SUBAGENT_DESCRIPTIONS = build_subagent_descriptions(SUBAGENTS)
SKILL_DESCRIPTIONS = build_skill_descriptions(SKILLS)

RECURSION_LIMIT = 50

llm = ChatAnthropic(api_key=API_KEY, base_url=BASE_URL, model=MODEL)

agent = create_deep_agent(
    model=llm,
    tools=[internet_search] if internet_search is not None else [],
    system_prompt=SYSTEM_PROMPT_UNIFIED.format(
        workdir=WORKDIR,
        tools=(
            "- write_todos: manage todo list\n"
            "- ls/read_file/write_file/edit_file/glob/grep: file operations\n"
            "- execute: run shell commands\n"
            "- task: dispatch focused work to subagents\n"
            "- internet_search: search web/news/finance via Tavily\n"
            "- skill tools: provided by deepagents skills middleware"
        ),
        tool_names="write_todos, ls, read_file, write_file, edit_file, glob, grep, execute, task, internet_search, skill",
        subagent_descriptions=SUBAGENT_DESCRIPTIONS,
        skill_descriptions=SKILL_DESCRIPTIONS,
        input="{input}",
        agent_scratchpad="{agent_scratchpad}",
    ),
    subagents=DEEPAGENT_SUBAGENTS,
    skills=["/src/skills"],
    backend=BACKEND,
)


def main() -> None:
    """运行交互式命令行会话。

    流程：读取用户输入 -> 解析控制命令 -> 路由输入 -> 调用 agent 流式执行 -> 打印结果。

    Args:
        None。

    Returns:
        None。用户退出后函数结束。
    """
    print("Mini Claude v5 (deepagents) - interactive. Type 'exit' to quit.\n")
    print(f"Loaded env from: {ENV_PATH}")
    print("Skills source: /src/skills (deepagents managed)")
    print("Commands: /skill, /subagent, /status")
    print(f"Sandbox refresh_each_execute: {SANDBOX_REFRESH_EACH_EXECUTE}")

    history: list[dict[str, str]] = []
    selected_skill: str | None = None
    selected_subagent: str | None = None

    todo_state = TodoRenderState()
    tool_state = ToolRenderState()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() in ("exit", "quit", "q"):
            break

        selected_skill, selected_subagent, task_text, handled = parse_selection_command(
            user_input,
            selected_skill,
            selected_subagent,
            handle_skill_command=handle_skill_command,
            handle_subagent_command=handle_subagent_command,
            skills=SKILLS,
            skill_aliases=SKILL_ALIASES,
            subagent_by_name=SUBAGENT_BY_NAME,
        )
        if handled:
            print()
            continue

        routed_input = build_routed_input(task_text or user_input, selected_skill, selected_subagent)

        history.append({"role": "user", "content": routed_input})

        try:
            start_index = len(history)
            history = stream_with_retry(
                agent,
                history,
                start_index,
                RECURSION_LIMIT,
                lambda messages, printed_index: print_turn(
                    messages,
                    printed_index,
                    todo_state,
                    tool_state,
                    SUBAGENT_SKILLS,
                ),
            )
        except json.JSONDecodeError:
            print("Error: API 返回内容为空或格式错误，请稍后重试。")
        except Exception as exc:
            print(f"Error during agent invoke: {exc}")

        print(render_active_selection(selected_skill, selected_subagent))
        print()


if __name__ == "__main__":
    main()
