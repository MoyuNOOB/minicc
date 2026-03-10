"""主循环辅助函数：状态展示与后台通知注入。"""

from __future__ import annotations

from typing import Any


def render_compact_status(compactor: Any) -> str:
    """渲染上下文压缩配置状态。"""
    return (
        "[compact] "
        f"threshold={compactor.threshold} "
        f"keep_recent={compactor.keep_recent_tool_results} "
        f"source_chars={compactor.max_summary_source_chars} "
        f"dir={compactor.transcript_dir}"
    )


def inject_background_notifications(history: list[dict[str, str]], background_manager: Any) -> int:
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

