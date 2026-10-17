"""提示词加载：prompt 也是代码资产，必须可版本化。

`prompt_version` 取文件内容哈希，并写进 trace。做 A/B 时，只有能证明"两次运行的
prompt 版本不同"，指标差异才能归因到 prompt 上——否则就是玄学调参。
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from equitylens.agents.schemas import SCHEMA_HINTS

PROMPT_DIR = Path(__file__).parent / "prompts" / "zh"


@cache
def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"提示词不存在: {path}")
    return path.read_text(encoding="utf-8")


@cache
def prompt_version(name: str) -> str:
    return hashlib.sha1(load_prompt(name).encode("utf-8")).hexdigest()[:8]


def render_system_prompt(name: str, schema_name: str) -> str:
    body = load_prompt(name)
    hint = SCHEMA_HINTS.get(schema_name, "")
    parts = [body.strip()]
    if hint:
        parts.append(f"## 输出字段（JSON）\n{hint}")
    parts.append(
        "严格只输出一个 JSON 对象，不要输出 markdown 代码块、不要输出解释文字。"
        "所有文本字段使用简体中文。"
    )
    return "\n\n".join(parts)
