import ast
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

from prompts import SYSTEM_PROMPT_UNIFIED


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.web_search import internet_search
except Exception:
    internet_search = None

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL_NAME", "kimi-k2-turbo-preview")
WORKDIR = Path.cwd()
SKILLS_DIR = PROJECT_ROOT / "skills"
BACKEND = FilesystemBackend(root_dir=PROJECT_ROOT, virtual_mode=True)

llm = ChatAnthropic(api_key=API_KEY, base_url=BASE_URL, model=MODEL)


def discover_skills(skills_dir: Path) -> dict[str, str]:
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


SKILLS = discover_skills(SKILLS_DIR)


def normalize_skill_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def build_skill_aliases(skills: dict[str, str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for skill_name in skills:
        aliases[skill_name] = skill_name
        aliases[skill_name.lower()] = skill_name
        aliases[normalize_skill_name(skill_name)] = skill_name
    return aliases


SKILL_ALIASES = build_skill_aliases(SKILLS)

SUBAGENTS = [
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
    mapped: list[dict[str, Any]] = []
    for item in subagents:
        converted = dict(item)
        if "system_prompt" not in converted and "prompt" in converted:
            converted["system_prompt"] = converted.pop("prompt")
        mapped.append(converted)
    return mapped


DEEPAGENT_SUBAGENTS = to_deepagents_subagents(SUBAGENTS)

SUBAGENT_BY_NAME = {item["name"]: item for item in SUBAGENTS}
SUBAGENT_SKILLS = {
    "frontend-engineer": ["frontend-style-optimizer"],
    "backend-engineer": ["mcp-builder", "code-reviewer"],
    "test-engineer": ["unit-testing", "smoke-testing", "code-reviewer"],
}
SUBAGENT_DESCRIPTIONS = "\n".join(f"- {item['name']}: {item['description']}" for item in SUBAGENTS)
SKILL_DESCRIPTIONS = "\n".join(
    f"- {name}: {desc or '(no description)'}" for name, desc in sorted(SKILLS.items())
) or "(no skills available)"

RECURSION_LIMIT = 50

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
    skills=["/skills"],
    backend=BACKEND,
)

LAST_TODOS: list[dict[str, Any]] = []
PRINTED_SKILL_CALLS: set[str] = set()
PRINTED_SUBAGENT_CALLS: set[str] = set()


def _normalize_content(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(text for text in texts if text)
    return None


def _todos_updates_from_messages(messages: list, start_index: int = 0) -> list[tuple[int, list[dict[str, Any]]]]:
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


def _render_todos(todos: list[dict[str, Any]]) -> None:
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


def _extract_subagent_type(args: object) -> str | None:
    if not isinstance(args, dict):
        return None
    return args.get("subagent_type") or args.get("subagent") or args.get("sub_agent")


def _render_special_tool_calls(messages: list, start_index: int) -> None:
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
                if call_id and call_id in PRINTED_SKILL_CALLS:
                    continue
                skill_name = args.get("name") if isinstance(args, dict) else None
                print(f"\n[skill] name: {skill_name or 'unknown'}")
                if call_id:
                    PRINTED_SKILL_CALLS.add(call_id)

            if call_name == "task":
                if call_id and call_id in PRINTED_SUBAGENT_CALLS:
                    continue
                subagent = _extract_subagent_type(args)
                skills = SUBAGENT_SKILLS.get(subagent, [])
                print(f"\n[task] subagent: {subagent or 'unknown'} | skills: {', '.join(skills) or '(none)'}")
                if call_id:
                    PRINTED_SUBAGENT_CALLS.add(call_id)


def print_turn(messages: list, start_index: int) -> None:
    global LAST_TODOS

    if not isinstance(messages, list):
        print(messages)
        return

    updates = _todos_updates_from_messages(messages, start_index)
    updates_by_index: dict[int, list[list[dict[str, Any]]]] = {}
    for idx, todos in updates:
        updates_by_index.setdefault(idx, []).append(todos)

    for idx, message in enumerate(messages):
        if idx < start_index:
            continue
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        text = _normalize_content(content)
        if text:
            print(text)
        for todos in updates_by_index.get(idx, []):
            if todos == LAST_TODOS:
                continue
            LAST_TODOS = todos
            _render_todos(todos)

    _render_special_tool_calls(messages, start_index)


def render_active_selection(selected_skill: str | None, selected_subagent: str | None) -> str:
    skill_text = selected_skill or "(auto)"
    subagent_text = selected_subagent or "(auto)"
    return f"[active] skill={skill_text} | subagent={subagent_text}"


def build_routed_input(user_text: str, selected_skill: str | None, selected_subagent: str | None) -> str:
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


def print_skills(selected_skill: str | None) -> None:
    print("\nSkills:")
    for name, description in sorted(SKILLS.items()):
        marker = "*" if name == selected_skill else " "
        print(f"{marker} {name}: {description or '(no description)'}")
    if not SKILLS:
        print("(none)")


def print_subagents(selected_subagent: str | None) -> None:
    print("\nSubagents:")
    for name, item in SUBAGENT_BY_NAME.items():
        marker = "*" if name == selected_subagent else " "
        print(f"{marker} {name}: {item['description']}")
    if not SUBAGENT_BY_NAME:
        print("(none)")


def parse_selection_command(
    user_input: str,
    selected_skill: str | None,
    selected_subagent: str | None,
) -> tuple[str | None, str | None, str | None, bool]:
    stripped = user_input.strip()
    if not stripped.startswith("/"):
        return selected_skill, selected_subagent, stripped, False

    parts = stripped.split(maxsplit=2)
    command = parts[0].lower()

    if command == "/skill":
        if len(parts) == 1 or parts[1].lower() in ("list", "ls"):
            print_skills(selected_skill)
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True

        if parts[1].lower() in ("clear", "none"):
            selected_skill = None
            print("Skill selection cleared.")
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True

        skill_input = parts[1]
        skill_name = SKILL_ALIASES.get(skill_input) or SKILL_ALIASES.get(normalize_skill_name(skill_input))
        if not skill_name:
            print(f"Unknown skill: {skill_input}")
            print_skills(selected_skill)
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True

        selected_skill = skill_name
        print(f"Selected skill: {selected_skill}")

        task_text = parts[2].strip() if len(parts) == 3 else None
        if not task_text:
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True
        return selected_skill, selected_subagent, task_text, False

    if command == "/subagent":
        if len(parts) == 1 or parts[1].lower() in ("list", "ls"):
            print_subagents(selected_subagent)
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True

        if parts[1].lower() in ("clear", "none"):
            selected_subagent = None
            print("Subagent selection cleared.")
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True

        subagent_name = parts[1]
        if subagent_name not in SUBAGENT_BY_NAME:
            print(f"Unknown subagent: {subagent_name}")
            print_subagents(selected_subagent)
            return selected_skill, selected_subagent, None, True

        selected_subagent = subagent_name
        print(f"Selected subagent: {selected_subagent}")

        task_text = parts[2].strip() if len(parts) == 3 else None
        if not task_text:
            print(render_active_selection(selected_skill, selected_subagent))
            return selected_skill, selected_subagent, None, True
        return selected_skill, selected_subagent, task_text, False

    if command == "/status":
        print("\n" + render_active_selection(selected_skill, selected_subagent))
        return selected_skill, selected_subagent, None, True

    print("Unknown command. Use /skill, /subagent, or /status.")
    return selected_skill, selected_subagent, None, True


def stream_with_retry(history: list[dict[str, str]], start_index: int) -> list[Any]:
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            printed_index = start_index
            final_messages: list[Any] = history

            for chunk in agent.stream(
                {"messages": history},
                {"recursion_limit": RECURSION_LIMIT},
                stream_mode="values",
            ):
                if not isinstance(chunk, dict):
                    continue
                messages = chunk.get("messages")
                if not isinstance(messages, list):
                    continue

                if len(messages) > printed_index:
                    print_turn(messages, printed_index)
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


def main() -> None:
    print("Mini Claude v5 (deepagents) - interactive. Type 'exit' to quit.\n")
    print(f"Loaded env from: {ENV_PATH}")
    print("Skills source: /skills (deepagents managed)")
    print("Commands: /skill, /subagent, /status")

    history: list[dict[str, str]] = []
    selected_skill: str | None = None
    selected_subagent: str | None = None

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
        )
        if handled:
            print()
            continue

        routed_input = build_routed_input(task_text or user_input, selected_skill, selected_subagent)

        history.append({"role": "user", "content": routed_input})

        try:
            start_index = len(history)
            history = stream_with_retry(history, start_index)
        except json.JSONDecodeError:
            print("Error: API 返回内容为空或格式错误，请稍后重试。")
        except Exception as exc:
            print(f"Error during agent invoke: {exc}")

        print(render_active_selection(selected_skill, selected_subagent))
        print()


if __name__ == "__main__":
    main()
