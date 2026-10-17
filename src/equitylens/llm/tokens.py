"""token 估算。

生产环境应该用 provider 返回的 usage（真实值），估算只用于**调用前**的预算预检。
没有引入 tiktoken：它对中文的切分和国内模型并不完全一致，还要多背一个二进制依赖，
用一个按字符分类的经验公式 + 校准系数反而更好解释、更可控。
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

#: 中文约 1.5 字/token，英文约 4 字符/token（cl100k 实测的经验值）
CJK_CHARS_PER_TOKEN = 1.5
ASCII_CHARS_PER_TOKEN = 4.0
PER_MESSAGE_OVERHEAD = 6


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return int(cjk / CJK_CHARS_PER_TOKEN + other / ASCII_CHARS_PER_TOKEN) + 4


def estimate_messages(messages: list[dict[str, str]]) -> int:
    return sum(
        estimate_tokens(message.get("content", "")) + PER_MESSAGE_OVERHEAD
        for message in messages
    )


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """按估算比例粗截断，保留首尾（首部通常是结论，尾部常是限定条件）。"""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    ratio = max_tokens / max(estimate_tokens(text), 1)
    keep = max(20, int(len(text) * ratio))
    head = int(keep * 0.7)
    tail = keep - head
    return f"{text[:head]}\n…（中略）…\n{text[-tail:] if tail > 0 else ''}"
