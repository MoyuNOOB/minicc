from langchain_core.tools import tool
from langgraph.types import Command
from langchain_core.messages import ToolMessage
from typing import Annotated
from src.graph.state import Todo
from ..decorators import log_io
from .prompt import WRITE_TODOS_DESCRIPTION
from langchain.tools import ToolRuntime
from src.graph.state import DeepAgentState

import logging
logger = logging.getLogger(__name__)


def are_todos_equal(todos1: list[Todo], todos2: list[Todo]) -> bool:
    """
    Compare two todo lists to check if they are identical.

    Args:
        todos1: First todo list
        todos2: Second todo list

    Returns:
        True if todos are identical, False otherwise
    """
    if len(todos1) != len(todos2):
        return False

    # Create dictionaries for O(1) lookup by ID
    todos1_dict = {todo.id: todo for todo in todos1}
    todos2_dict = {todo.id: todo for todo in todos2}

    # Check if all IDs exist in both lists
    if set(todos1_dict.keys()) != set(todos2_dict.keys()):
        return False

    # Compare each todo by ID
    for todo_id in todos1_dict:
        todo1 = todos1_dict[todo_id]
        todo2 = todos2_dict[todo_id]

        if (todo1.content != todo2.content or
            todo1.status != todo2.status):
            return False

    return True

@tool(description=WRITE_TODOS_DESCRIPTION)
@log_io
def write_todos(
    todos: Annotated[list[Todo], "The updated todo list"],
    runtime: ToolRuntime,
) -> Command:
    # Get current todos from state
    tool_call_id = runtime.tool_call_id or ""
    configurable = (
        runtime.config.get("configurable", {})
        if runtime.config and hasattr(runtime.config, "get")
        else {}
    )
    conversation_id = configurable.get("thread_id")
    logger.info(f"conversation_id={conversation_id} new Todos: {todos}")
    try:
        state: DeepAgentState = runtime.state or {}
        current_todos = state.get("todos", [])
        if are_todos_equal(current_todos, todos):
            logger.info(f"conversation_id={conversation_id} Incoming todos match existing state; no changes detected.")

        normalized_todos: list[Todo] = []
        current_task = ""  # Will remain empty if no in_progress task is found
        in_progress_seen = False
        reset_task_ids: list[str] = []

        # Ensure only a single task is marked as in progress
        for todo in todos:
            if todo.status == "in_progress":
                if not in_progress_seen:
                    # First in_progress task - set as current_task
                    in_progress_seen = True
                    current_task = todo.id
                    normalized_todos.append(todo)
                else:
                    # Reset additional in-progress items back to pending
                    reset_task_ids.append(todo.id)
                    normalized_todos.append(todo.model_copy(update={"status": "pending"}))
            else:
                normalized_todos.append(todo)

        if reset_task_ids:
            logger.info(
                f"conversation_id={conversation_id} Multiple tasks marked in_progress; resetting extras to pending: {reset_task_ids}",
            )

        # Prepare different messages based on whether todos changed
        if reset_task_ids:
            reset_ids_str = ", ".join(reset_task_ids)
            base_message = (
                "Todos updated. Only one task can be in progress at a time. "
                f"Reset the following tasks to pending to maintain the constraint: {reset_ids_str}."
            )
        else:
            base_message = (
                "Todos have been modified successfully. Ensure that you continue to use "
                "the todo list to track your progress. Please proceed with the current "
                "tasks if applicable."
            )

        return Command(
            update={
                "todos": normalized_todos,
                "current_task": current_task,
                "messages": [ToolMessage(base_message, tool_call_id=tool_call_id)],
                "todo_changed": True
            }
        )
    except Exception as e:
        logger.error(f"conversation_id={conversation_id} Failed to write todos: {e}")
        return Command(
            update={
                "messages": [ToolMessage(f"Failed to write todos: {e}", tool_call_id=tool_call_id)]
            }
        )
