"""工具注册表。

工具是"确定性的能力边界"：能做什么、需要什么参数，全部显式声明。
再往上（agent）只负责决定"何时调用哪个"，不负责实现细节——这条边界让
"模型幻觉调用了不存在的工具"变成一次可捕获的错误，而不是一段诡异的副作用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from equitylens.engine.types import FailureKind
from equitylens.errors import NodeError

ToolFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fn: ToolFn


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(
        self, name: str, fn: ToolFn, *, description: str = ""
    ) -> ToolRegistry:
        self._tools[name] = ToolSpec(name=name, description=description, fn=fn)
        return self

    def has(self, name: str) -> bool:
        return name in self._tools

    async def invoke(self, name: str, /, **kwargs: Any) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise NodeError(
                FailureKind.UNKNOWN,
                f"工具不存在: {name}（已注册: {sorted(self._tools)}）",
            )
        return await spec.fn(**kwargs)

    def manifest(self) -> list[dict[str, str]]:
        return [
            {"name": spec.name, "description": spec.description}
            for spec in self._tools.values()
        ]
