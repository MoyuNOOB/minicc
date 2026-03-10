"""MiniClaude 主入口：初始化 agent 并驱动交互主循环。"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

from src.agent.prompts import SYSTEM_PROMPT_UNIFIED
from src.exec.sandbox import SimpleSandboxBackend
from src.runtime.controller import build_routed_input, parse_selection_command, render_active_selection
from src.exec.background_tasks import BackgroundManager, build_background_tools
from src.session.context_compact import build_context_compactor
from src.collab.task_system import TaskManager, build_task_tools
from src.collab.teams import MessageBus, TeammateManager, build_team_tools, inject_lead_inbox_messages
from src.collab.team_protocols import TeamProtocolManager, build_team_protocol_tools
from src.collab.auto_agents import AutonomousAgentManager, build_autonomous_tools, inject_autonomous_events
from src.exec.worktrees import WorktreeManager, build_worktree_tools
from src.agent.skills import (
    build_skill_aliases,
    build_skill_descriptions,
    discover_skills,
    handle_skill_command,
)
from src.agent.subagents import (
    DEFAULT_SUBAGENTS,
    build_subagent_by_name,
    build_subagent_descriptions,
    handle_subagent_command,
    to_deepagents_subagents,
)
from src.runtime.session_helpers import inject_background_notifications, render_compact_status
from src.session.todos import TodoRenderState
from src.runtime.stream_runtime import ToolRenderState, print_turn, stream_with_retry
from src.session.history import (
    count_history_messages,
    find_latest_history_file,
    list_history_files,
    load_session_history,
    save_session_history,
)


ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

try:
    from src.tools.web_search import internet_search
except Exception:
    internet_search = None

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL_NAME", "kimi-k2-turbo-preview")
WORKDIR = Path.cwd()
HISTORY_DIR = WORKDIR / ".history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
SANDBOX_ROOT = PROJECT_ROOT / "workspace"
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
SANDBOX_SESSION_ID = f"session-{uuid.uuid4().hex[:8]}"
SANDBOX_WORKDIR = SANDBOX_ROOT / SANDBOX_SESSION_ID
SANDBOX_WORKDIR.mkdir(parents=True, exist_ok=True)
HISTORY_SESSION_FILE = HISTORY_DIR / f"{SANDBOX_SESSION_ID}.jsonl"
SKILLS_DIR = PROJECT_ROOT / "src" / "skills"
TASKS_DIR = WORKDIR / ".tasks"
TEAM_DIR = WORKDIR / ".team"
WORKTREES_DIR = WORKDIR / ".worktrees"
BACKEND = SimpleSandboxBackend(
    root_dir=SANDBOX_WORKDIR,
    virtual_mode=False,
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

RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "200"))

llm = ChatAnthropic(api_key=API_KEY, base_url=BASE_URL, model=MODEL)
compactor = build_context_compactor(llm, WORKDIR)
task_manager = TaskManager(TASKS_DIR)
task_tools = build_task_tools(task_manager)
background_manager = BackgroundManager(WORKDIR)
background_tools = build_background_tools(background_manager)
teammate_manager = TeammateManager(TEAM_DIR)
message_bus = MessageBus(TEAM_DIR / "inbox")
team_tools = build_team_tools(teammate_manager, message_bus)
team_protocol_manager = TeamProtocolManager(TEAM_DIR, message_bus, teammate_manager)
team_protocol_tools = build_team_protocol_tools(team_protocol_manager)
autonomous_manager = AutonomousAgentManager(teammate_manager, task_manager, message_bus)
autonomous_tools = build_autonomous_tools(autonomous_manager)
worktree_manager = WorktreeManager(PROJECT_ROOT, WORKTREES_DIR, task_manager)
worktree_tools = build_worktree_tools(worktree_manager)

agent_tools = [
    *task_tools,
    *background_tools,
    *team_tools,
    *team_protocol_tools,
    *autonomous_tools,
    *worktree_tools,
]
if internet_search is not None:
    agent_tools.append(internet_search)

agent = create_deep_agent(
    model=llm,
    tools=agent_tools,
    system_prompt=SYSTEM_PROMPT_UNIFIED.format(
        workdir=WORKDIR,
        sandbox_workdir=SANDBOX_WORKDIR,
        sandbox_session_id=SANDBOX_SESSION_ID,
        tools=(
            "- write_todos: manage todo list\n"
            "- ls/read_file/write_file/edit_file/glob/grep: file operations\n"
            "- execute: run shell commands\n"
            "- task: dispatch focused work to subagents\n"
            "- task_create/task_update/task_list/task_get: persistent DAG task system\n"
            "- background_run/background_check: run long commands asynchronously\n"
            "- team_spawn/team_list/team_send/team_read_inbox: persistent teammates + mailbox\n"
            "- team_shutdown_request/team_shutdown_response/team_plan_submit/team_plan_review/team_protocol_list: team protocols\n"
            "- idle/claim_task/auto_scan_unclaimed_tasks/team_auto_tick: autonomous teammate scheduling\n"
            "- worktree_create/worktree_list/worktree_run/worktree_keep/worktree_remove/worktree_events: isolated git worktrees bound to tasks\n"
            "- internet_search: search web/news/finance via Tavily\n"
            "- skill tools: provided by deepagents skills middleware"
        ),
        tool_names=(
            "write_todos, ls, read_file, write_file, edit_file, glob, grep, execute, "
            "task, task_create, task_update, task_list, task_get, "
            "background_run, background_check, "
            "team_spawn, team_list, team_send, team_read_inbox, "
            "team_shutdown_request, team_shutdown_response, team_plan_submit, team_plan_review, team_protocol_list, "
            "idle, claim_task, auto_scan_unclaimed_tasks, team_auto_tick, "
            "worktree_create, worktree_list, worktree_run, worktree_keep, worktree_remove, worktree_events, "
            "internet_search, skill"
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
    print("Commands: /skill, /subagent, /status, /compact, /history, /resume, /background, /team, /inbox")
    print(f"Sandbox session_id: {SANDBOX_SESSION_ID}")
    print(f"Sandbox workdir: {SANDBOX_WORKDIR}")
    print(f"History file: {HISTORY_SESSION_FILE}")
    print(f"Tasks directory: {TASKS_DIR}")
    print(f"Team directory: {TEAM_DIR}")
    print(f"Worktrees directory: {WORKTREES_DIR}")
    print(f"Agent recursion_limit: {RECURSION_LIMIT}")

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
            print(render_compact_status(compactor))
            print()
            continue

        if user_input.strip().lower() == "/team":
            members = teammate_manager.list_members()
            print("\nTeam:")
            if not members:
                print("(empty)")
            else:
                for member in members:
                    print(f"- {member.get('name')} [{member.get('status')}] role={member.get('role')}")
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

        if user_input.strip().lower() == "/background":
            print("\nBackground Tasks:")
            print(background_manager.check())
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

        if user_input.strip().lower() == "/inbox":
            inbox = message_bus.read_inbox("lead", drain=True)
            print("\nLead Inbox:")
            if not inbox:
                print("(empty)")
            else:
                for item in inbox:
                    print(f"- [{item.get('type')}] from={item.get('from')} content={item.get('content')}")
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

        if user_input.startswith("/history"):
            parts = user_input.split(maxsplit=1)
            limit = 10
            if len(parts) == 2:
                try:
                    limit = max(1, int(parts[1].strip()))
                except ValueError:
                    print("Usage: /history [limit]")
                    print(render_active_selection(selected_skill, selected_subagent))
                    print()
                    continue

            items = list_history_files(HISTORY_DIR, limit=limit)
            print("\nHistory Sessions:")
            if not items:
                print("(empty)")
            else:
                for index, path in enumerate(items, start=1):
                    mtime = path.stat().st_mtime
                    updated_at = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                    message_count = count_history_messages(path)
                    current_mark = " (current)" if path.resolve() == HISTORY_SESSION_FILE.resolve() else ""
                    print(
                        f"{index}. {path.name}{current_mark} | "
                        f"messages={message_count} | updated={updated_at}"
                    )
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

        if user_input.startswith("/resume"):
            parts = user_input.split(maxsplit=1)
            target = parts[1].strip() if len(parts) == 2 else ""

            latest_file = None
            if not target:
                latest_file = find_latest_history_file(HISTORY_DIR, HISTORY_SESSION_FILE)
            elif target.isdigit():
                items = list_history_files(HISTORY_DIR, limit=max(1, int(target)), exclude=HISTORY_SESSION_FILE)
                position = int(target)
                if 1 <= position <= len(items):
                    latest_file = items[position - 1]
            else:
                candidate = HISTORY_DIR / target
                if candidate.exists() and candidate.is_file() and candidate.resolve() != HISTORY_SESSION_FILE.resolve():
                    latest_file = candidate

            if latest_file is None:
                print("No matching previous session found. Use /history to list sessions.")
            else:
                resumed = load_session_history(latest_file)
                if not resumed:
                    print(f"Found session file but no valid messages: {latest_file}")
                else:
                    history = resumed
                    save_session_history(HISTORY_SESSION_FILE, history)
                    print(f"Resumed {len(history)} messages from: {latest_file}")
            print(render_active_selection(selected_skill, selected_subagent))
            print()
            continue

        if user_input.startswith("/compact"):
            parts = user_input.split(maxsplit=1)
            focus = parts[1].strip() if len(parts) == 2 else None
            if not history:
                print("No conversation yet. Nothing to compact.")
            else:
                history = compactor.manual_compact(history, focus=focus)
                save_session_history(HISTORY_SESSION_FILE, history)
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

        injected_count = inject_background_notifications(history, background_manager)
        if injected_count:
            print(f"[background] injected {injected_count} finished task result(s).")

        team_injected_count = inject_lead_inbox_messages(history, message_bus, lead_name="lead")
        if team_injected_count:
            print(f"[team] injected {team_injected_count} inbox message(s).")

        autonomous_events = autonomous_manager.tick_idle_teammates()
        autonomous_injected_count = inject_autonomous_events(history, autonomous_events)
        if autonomous_injected_count:
            print(f"[autonomy] injected {autonomous_injected_count} scheduler event(s).")

        # 每轮把用户输入写入history
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
            if "Recursion limit" in str(exc):
                print(
                    "Error: Agent reached recursion limit before finishing. "
                    "Try narrowing the task scope, selecting a skill/subagent explicitly, "
                    "or increasing RECURSION_LIMIT in .env."
                )
            print(f"Error during agent invoke: {exc}")

        save_session_history(HISTORY_SESSION_FILE, history)

        print(render_active_selection(selected_skill, selected_subagent))
        print()


if __name__ == "__main__":
    main()
