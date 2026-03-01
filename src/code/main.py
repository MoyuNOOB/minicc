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
from src.code.background_tasks import BackgroundManager, build_background_tools
from src.code.context_compact import build_context_compactor
from src.code.task_system import TaskManager, build_task_tools
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
TASKS_DIR = WORKDIR / ".tasks"
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
compactor = build_context_compactor(llm, WORKDIR)
task_manager = TaskManager(TASKS_DIR)
task_tools = build_task_tools(task_manager)
background_manager = BackgroundManager(WORKDIR)
background_tools = build_background_tools(background_manager)

agent_tools = [*task_tools, *background_tools]
if internet_search is not None:
    agent_tools.append(internet_search)

agent = create_deep_agent(
    model=llm,
    tools=agent_tools,
    system_prompt=SYSTEM_PROMPT_UNIFIED.format(
        workdir=WORKDIR,
        tools=(
            "- write_todos: manage todo list\n"
            "- ls/read_file/write_file/edit_file/glob/grep: file operations\n"
            "- execute: run shell commands\n"
            "- task: dispatch focused work to subagents\n"
            "- task_create/task_update/task_list/task_get: persistent DAG task system\n"
            "- background_run/background_check: run long commands asynchronously\n"
            "- internet_search: search web/news/finance via Tavily\n"
            "- skill tools: provided by deepagents skills middleware"
        ),
        tool_names=(
            "write_todos, ls, read_file, write_file, edit_file, glob, grep, execute, "
            "task, task_create, task_update, task_list, task_get, "
            "background_run, background_check, internet_search, skill"
        ),
        subagent_descriptions=SUBAGENT_DESCRIPTIONS,
        skill_descriptions=SKILL_DESCRIPTIONS,
        input="{input}",
        agent_scratchpad="{agent_scratchpad}",
    ),
    subagents=DEEPAGENT_SUBAGENTS,
    skills=["/src/skills"],
    backend=BACKEND,
)


def render_compact_status() -> str:
    """渲染上下文压缩配置状态。"""
    return (
        "[compact] "
        f"threshold={compactor.threshold} "
        f"keep_recent={compactor.keep_recent_tool_results} "
        f"source_chars={compactor.max_summary_source_chars} "
        f"dir={compactor.transcript_dir}"
    )


def inject_background_notifications(history: list[dict[str, str]]) -> int:
    """把已完成后台任务结果注入到下一次 LLM 调用上下文。"""
    notifications = background_manager.drain_notifications()
    if not notifications:
        return 0

    lines = []
    for item in notifications:
        lines.append(
            f"[bg:{item['task_id']}] status={item['status']} command={item['command']} result={item['result']}"
        )
    notif_text = "\n".join(lines)
    history.append(
        {
            "role": "user",
            "content": f"<background-results>\n{notif_text}\n</background-results>",
        }
    )
    history.append({"role": "assistant", "content": "Noted background results."})
    return len(notifications)


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
    print("Commands: /skill, /subagent, /status, /compact")
    print(f"Sandbox refresh_each_execute: {SANDBOX_REFRESH_EACH_EXECUTE}")
    print(f"Tasks directory: {TASKS_DIR}")

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

        if user_input.strip().lower() == "/status":
            print("\n" + render_active_selection(selected_skill, selected_subagent))
            print(render_compact_status())
            print()
            continue

        if user_input.startswith("/compact"):
            parts = user_input.split(maxsplit=1)
            focus = parts[1].strip() if len(parts) == 2 else None
            if not history:
                print("No conversation yet. Nothing to compact.")
            else:
                history = compactor.manual_compact(history, focus=focus)
                print("[manual compact] conversation compressed.")
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

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

        injected_count = inject_background_notifications(history)
        if injected_count:
            print(f"[background] injected {injected_count} finished task result(s).")

        history.append({"role": "user", "content": routed_input})
        compactor.micro_compact(history)
        history, auto_compacted = compactor.maybe_auto_compact(history)
        if auto_compacted:
            print("[auto_compact triggered] conversation compressed.")

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
