"""Research-session naming helpers.

The first user question becomes a stable, concise conversation title without
making a second model request.  This keeps naming fast and available even when
the configured model provider is temporarily unavailable.
"""

from __future__ import annotations

import re


DEFAULT_RESEARCH_SESSION_TITLE = "新会话"
MAX_AUTO_RESEARCH_SESSION_TITLE_CHARS = 32

_LEADING_MARKDOWN_RE = re.compile(r"^(?:#{1,6}|[-*+]|\d+[.)、])\s*")
_POLITE_PREFIX_RE = re.compile(
    r"^(?:(?:你好|您好)[，,。！!：:\s]*)?"
    r"(?:我想请你|我想让你|我想要|我想|我需要|能否|能不能|是否可以|可以|麻烦|劳烦|请问一下|请问|请你|请|帮我)+"
    r"[，,。！!：:\s]*"
)
_TASK_PREFIX_RE = re.compile(
    r"^(?:帮我|给我|为我)?"
    r"(?:分析|解释|总结|查询|查看|比较|评估|判断|介绍|说明|生成|整理|回答|研究)"
    r"(?:一下|下)?[，,。！!：:\s]*"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?；;\r\n]")


def summarize_research_session_title(
    question: str,
    *,
    max_chars: int = MAX_AUTO_RESEARCH_SESSION_TITLE_CHARS,
) -> str:
    """Build a readable title from the first user question.

    The result removes chatty prefixes and Markdown list markers, keeps the
    first semantic sentence, and caps display length.  It intentionally avoids
    an LLM call: session naming must not block or add cost to the actual answer.
    """

    original = re.sub(r"[\t\f\v ]+", " ", str(question or ""))
    original = re.sub(r"(?:\r\n?|\n)+", "\n", original).strip()
    if not original:
        return DEFAULT_RESEARCH_SESSION_TITLE

    candidate = _LEADING_MARKDOWN_RE.sub("", original).strip()
    # Prefixes can be layered (for example, "请帮我分析一下").  Two passes
    # handle that common form while keeping the transformation predictable.
    for _ in range(2):
        shortened = _POLITE_PREFIX_RE.sub("", candidate).strip()
        shortened = _TASK_PREFIX_RE.sub("", shortened).strip()
        if shortened == candidate:
            break
        candidate = shortened

    candidate = _SENTENCE_BOUNDARY_RE.split(candidate, maxsplit=1)[0].strip()
    candidate = candidate.strip(" \t\"'“”‘’《》【】[]()（）:：,，、.-")
    if not candidate:
        candidate = original.strip(" \t\"'“”‘’《》【】[]()（）:：,，、.-") or original

    if len(candidate) <= max_chars:
        return candidate
    return f"{candidate[: max_chars - 1].rstrip()}…"
