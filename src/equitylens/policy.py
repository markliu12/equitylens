"""合规红线与免责声明。

这些词是"研究"与"荐股"的分界线。与其在 prompt 里求模型不要说，不如在确定性层面
拦一道——prompt 是概率的，词表检查是确定的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DISCLAIMER = (
    "本报告由 AI 多智能体系统基于公开信息自动生成，仅用于信息整理与研究学习，"
    "不构成任何投资建议、要约或承诺。报告中的数据可能存在延迟、缺失或误差，"
    "引用内容以原始出处为准。读者应独立判断并自行承担相关风险。"
)

#: (类型, 正则, 修复建议)
FORBIDDEN_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("买入建议", r"(建议|应当|可以)(买入|增持|加仓|抄底)", "改为描述性表述，如『市场关注度上升』"),
    ("卖出建议", r"(建议|应当|可以)(卖出|减持|清仓|止损)", "删除操作性建议，仅保留事实与风险提示"),
    ("目标价", r"(目标价|预期价格|上看|下看)\s*[:：]?\s*\d", "删除价格预测"),
    ("收益承诺", r"(稳赚|必涨|必跌|保本|保证收益|无风险套利)", "删除确定性收益表述"),
    ("内幕信息", r"(内幕消息|内部消息|未公开信息)", "删除涉及非公开信息的表述"),
)


@dataclass(frozen=True)
class Violation:
    type: str
    quote: str
    fix: str


def scan_text(text: str) -> list[Violation]:
    hits: list[Violation] = []
    for kind, pattern, fix in FORBIDDEN_PATTERNS:
        for match in re.finditer(pattern, text):
            hits.append(Violation(type=kind, quote=match.group(0), fix=fix))
    return hits


def scan_many(texts: list[str]) -> list[Violation]:
    hits: list[Violation] = []
    for text in texts:
        hits.extend(scan_text(text))
    return hits


def redact(text: str) -> str:
    """合规回修失败时的最后一道闸：直接把风险表述替换掉。"""
    result = text
    for _kind, pattern, fix in FORBIDDEN_PATTERNS:
        result = re.sub(pattern, f"【已按合规要求调整：{fix}】", result)
    return result
