"""Generate concise, deterministic titles for a conversation's first question."""

from __future__ import annotations

import re
import unicodedata


DEFAULT_RESEARCH_SESSION_TITLE = "新会话"
AUTO_TITLE_PLACEHOLDERS = frozenset({
    DEFAULT_RESEARCH_SESSION_TITLE,
    "新对话",
    "新的会话",
    "new chat",
    "untitled",
})
AUTO_TITLE_MAX_LENGTH = 24

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MARKDOWN_PREFIX_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)")
_GENERIC_SECTION_RE = re.compile(r"^(?:需求|问题|任务|请求|背景)\s*[:：\-—]?\s*", re.IGNORECASE)
_COURTESY_PREFIX_RE = re.compile(
    r"^(?:"
    r"请问|请(?:你|帮我|协助我|帮忙)?|烦请(?:你|帮我)?|麻烦(?:你|帮我)?|帮我|"
    r"我想(?:请你|让你)?|能否(?:请你|帮我)?|是否可以(?:请你|帮我)?|可以(?:请你|帮我)?|"
    r"please(?:\s+help\s+me)?(?:\s+to)?|could\s+you|can\s+you|would\s+you"
    r")\s*[,，:：]?\s*",
    re.IGNORECASE,
)
_TRAILING_COURTESY_RE = re.compile(r"(?:好吗|可以吗|行吗|谢谢(?:你)?|thanks?)\s*$", re.IGNORECASE)


def summarize_conversation_title(content: str, max_length: int = AUTO_TITLE_MAX_LENGTH) -> str:
    """Turn the first user question into a compact sidebar title without another model call."""

    if max_length < 4:
        raise ValueError("max_length must be at least 4")

    text = unicodedata.normalize("NFKC", content or "")
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub("", text)
    text = _MARKDOWN_PREFIX_RE.sub("", text)
    text = text.replace("```", " ").replace("`", "")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,，。.!！?？;；:：-—")

    # Remove generic request wrappers while keeping the subject and action.
    previous = None
    while text and text != previous:
        previous = text
        text = _GENERIC_SECTION_RE.sub("", text)
        text = _COURTESY_PREFIX_RE.sub("", text)

    clauses = [item.strip() for item in re.split(r"[。！？!?；;]+", text) if item.strip()]
    title = clauses[0] if clauses else "科研问题分析"
    if len(title) < 6 and len(clauses) > 1:
        title = f"{title} {clauses[1]}"
    title = _TRAILING_COURTESY_RE.sub("", title).strip(" \t\r\n,，。.!！?？;；:：-—")
    title = title or "科研问题分析"

    if len(title) <= max_length:
        return title

    shortened = title[: max_length - 1].rstrip(" ,，、:：-—")
    # Prefer a readable word boundary for Latin text when one is reasonably close.
    boundary = max(shortened.rfind(" "), shortened.rfind("，"), shortened.rfind(","))
    if boundary >= max_length // 2:
        shortened = shortened[:boundary].rstrip()
    return f"{shortened}…"


def auto_title_for_first_message(current_title: str, content: str, *, has_messages: bool) -> str | None:
    """Return an automatic title only for an untouched, empty conversation."""

    normalized_title = unicodedata.normalize("NFKC", current_title or "").strip().casefold()
    placeholders = {item.casefold() for item in AUTO_TITLE_PLACEHOLDERS}
    if has_messages or normalized_title not in placeholders:
        return None
    return summarize_conversation_title(content)
