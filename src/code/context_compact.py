"""上下文压缩模块：为 deepagents 会话提供三层压缩策略。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _extract_text(content: Any) -> str:
    """把消息内容归一化为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(text for text in texts if text)
    return str(content)


def _message_role(message: Any) -> str:
    """抽取消息角色（human/ai/tool 等）。"""
    if isinstance(message, dict):
        role = message.get("role")
        return role if isinstance(role, str) else ""

    msg_type = getattr(message, "type", None)
    if isinstance(msg_type, str):
        return msg_type

    role = getattr(message, "role", None)
    return role if isinstance(role, str) else ""


def _message_tool_name(message: Any) -> str:
    """尽量提取工具名称，用于占位文本。"""
    if isinstance(message, dict):
        for key in ("name", "tool_name"):
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
        addl = message.get("additional_kwargs")
        if isinstance(addl, dict):
            value = addl.get("name") or addl.get("tool_name")
            if isinstance(value, str) and value:
                return value
        return "tool"

    for attr in ("name", "tool_name"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value:
            return value

    addl = getattr(message, "additional_kwargs", None)
    if isinstance(addl, dict):
        value = addl.get("name") or addl.get("tool_name")
        if isinstance(value, str) and value:
            return value

    return "tool"


def _set_message_content(message: Any, new_content: str) -> None:
    """原地更新消息内容。"""
    if isinstance(message, dict):
        message["content"] = new_content
        return
    setattr(message, "content", new_content)


def _as_serializable(message: Any) -> dict[str, Any]:
    """把任意消息对象转为可写盘结构。"""
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        try:
            dumped = message.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    return {"role": _message_role(message), "content": _extract_text(getattr(message, "content", ""))}


@dataclass
class ContextCompactor:
    """deepagents 会话压缩器。"""

    llm: Any
    workdir: Path
    threshold: int = 50000
    keep_recent_tool_results: int = 3
    transcript_dirname: str = ".transcripts"
    max_summary_source_chars: int = 80000

    @property
    def transcript_dir(self) -> Path:
        return self.workdir / self.transcript_dirname

    def estimate_tokens(self, messages: list[Any]) -> int:
        """粗略估算 token 数量（约 4 chars/token）。"""
        return len(str(messages)) // 4

    def micro_compact(self, messages: list[Any]) -> None:
        """层1：仅保留最近 N 条工具输出明文，其余替换为占位。"""
        tool_message_indexes: list[int] = []
        nested_tool_results: list[tuple[Any, int]] = []

        for index, message in enumerate(messages):
            role = _message_role(message).lower()
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)

            if role == "tool":
                tool_message_indexes.append(index)

            if role == "user" and isinstance(content, list):
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        nested_tool_results.append((message, part_index))

        if len(tool_message_indexes) > self.keep_recent_tool_results:
            for idx in tool_message_indexes[:-self.keep_recent_tool_results]:
                message = messages[idx]
                raw_content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
                content_text = _extract_text(raw_content)
                if len(content_text) <= 100:
                    continue
                tool_name = _message_tool_name(message)
                _set_message_content(message, f"[Previous: used {tool_name}]")

        if len(nested_tool_results) > self.keep_recent_tool_results:
            for msg, part_index in nested_tool_results[:-self.keep_recent_tool_results]:
                part = msg["content"][part_index]
                content_text = part.get("content")
                if not isinstance(content_text, str) or len(content_text) <= 100:
                    continue
                part["content"] = "[Previous: used tool]"

    def _save_transcript(self, messages: list[Any]) -> Path:
        """保存完整会话到 jsonl。"""
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(json.dumps(_as_serializable(message), ensure_ascii=False, default=str) + "\n")
        return path

    def _summarize_messages(self, messages: list[Any], focus: str | None = None) -> str:
        """调用 LangChain LLM 生成连续性摘要。"""
        raw_text = json.dumps([_as_serializable(msg) for msg in messages], ensure_ascii=False, default=str)
        source = raw_text[: self.max_summary_source_chars]

        focus_text = focus.strip() if isinstance(focus, str) and focus.strip() else ""
        prompt = (
            "Summarize this coding conversation for continuity. "
            "Include: 1) what was accomplished, 2) current project state, "
            "3) key decisions and constraints, 4) pending tasks. "
            "Be concise but preserve critical implementation details."
        )
        if focus_text:
            prompt += f"\nPriority focus: {focus_text}"
        prompt += "\n\nConversation:\n" + source

        response = self.llm.invoke(prompt)
        content = getattr(response, "content", "")
        summary = _extract_text(content).strip()
        return summary or "(summary unavailable)"

    def auto_compact(self, messages: list[Any], focus: str | None = None) -> list[dict[str, str]]:
        """层2：保存转录并摘要替换整个历史。"""
        transcript_path = self._save_transcript(messages)
        summary = self._summarize_messages(messages, focus=focus)
        return [
            {
                "role": "user",
                "content": (
                    f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}"
                ),
            },
            {
                "role": "assistant",
                "content": "Understood. I have the context from the summary. Continuing.",
            },
        ]

    def maybe_auto_compact(self, messages: list[Any], focus: str | None = None) -> tuple[list[Any], bool]:
        """层2触发器：超过阈值时自动压缩。"""
        if self.estimate_tokens(messages) <= self.threshold:
            return messages, False
        compacted = self.auto_compact(messages, focus=focus)
        return compacted, True

    def manual_compact(self, messages: list[Any], focus: str | None = None) -> list[Any]:
        """层3：外部显式触发压缩。"""
        if not messages:
            return messages
        return self.auto_compact(messages, focus=focus)


def build_context_compactor(llm: Any, workdir: Path) -> ContextCompactor:
    """根据环境变量创建压缩器。"""
    threshold = int(os.getenv("CONTEXT_COMPACT_THRESHOLD", "50000"))
    keep_recent = int(os.getenv("CONTEXT_COMPACT_KEEP_RECENT", "3"))
    source_chars = int(os.getenv("CONTEXT_COMPACT_SOURCE_CHARS", "80000"))
    transcript_dirname = os.getenv("CONTEXT_COMPACT_DIR", ".transcripts")
    return ContextCompactor(
        llm=llm,
        workdir=workdir,
        threshold=threshold,
        keep_recent_tool_results=keep_recent,
        transcript_dirname=transcript_dirname,
        max_summary_source_chars=source_chars,
    )

