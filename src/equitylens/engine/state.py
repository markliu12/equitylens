"""状态模型：Blackboard + Reducer + 只读视图。

为什么不用一个共享 dict：
1. FanOut 之后有多个节点并行跑，若都能直接写同一个 dict，结果取决于完成顺序，
   属于典型的"隐性竞态"——本地跑十次都对，线上偶发错一次。
2. 这里改成：节点拿到的是**深拷贝只读视图**（改了也回不去），写必须通过 Patch，
   且只能写自己声明的通道，合并语义由 reducer 显式声明（追加 / 覆盖 / 合并 / 求和）。

副产品：`snapshot()` 天然是可 JSON 序列化的 dict，checkpoint 不需要任何转换层。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from equitylens.engine.types import Patch, Usage
from equitylens.errors import GraphDefinitionError

Reducer = Callable[[Any, Any], Any]


# --------------------------------------------------------------------------- #
# Reducer 实现
# --------------------------------------------------------------------------- #
def last(_old: Any, new: Any) -> Any:
    """覆盖写入，用于唯一值通道（计划、结论、报告）。"""
    return new


def append(old: Any, new: Any) -> list[Any]:
    """追加写入，用于多节点产出（证据、分析报告、辩论轮次）。

    注意第一个分支：`old` 可能是单个对象而不是列表（FanOut 首次合并时就地取用原始值）。
    直接 `list(dict)` 会得到键名列表——这类错误在类型上完全合法，运行时却很难排查。
    """
    if old is None:
        base: list[Any] = []
    elif isinstance(old, list | tuple):
        base = list(old)
    else:
        base = [old]
    if isinstance(new, list | tuple):
        base.extend(new)
    else:
        base.append(new)
    return base


def append_unique(key: str | Callable[[Any], Any]) -> Reducer:
    """按 key 去重追加 —— 证据合并靠它实现跨源去重。"""
    key_fn: Callable[[Any], Any] = (
        key if callable(key) else (lambda item: item.get(key))
    )

    def _reducer(old: Any, new: Any) -> list[Any]:
        items = list(old or [])
        seen = {key_fn(item) for item in items}
        incoming = new if isinstance(new, list | tuple) else [new]
        for item in incoming:
            marker = key_fn(item)
            if marker in seen:
                continue
            seen.add(marker)
            items.append(item)
        return items

    return _reducer


def merge_map(old: Any, new: Any) -> dict[str, Any]:
    merged = dict(old or {})
    merged.update(new or {})
    return merged


def sum_usage(old: Any, new: Any) -> dict[str, Any]:
    """用量累加：接受 Usage 对象或 dict。"""
    left = old if isinstance(old, dict) else {}
    right = new.to_dict() if isinstance(new, Usage) else dict(new or {})
    keys = set(left) | set(right)
    acc: dict[str, Any] = {}
    for key in keys:
        if key == "model":
            acc[key] = right.get(key) or left.get(key) or ""
        else:
            acc[key] = (left.get(key) or 0) + (right.get(key) or 0)
    if "cost_cny" in acc:
        acc["cost_cny"] = round(acc["cost_cny"], 6)
    return acc


def max_value(old: Any, new: Any) -> Any:
    return new if old is None else max(old, new)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Channel:
    name: str
    reducer: Reducer
    default: Any
    description: str = ""


class StateSchema:
    """通道声明表。通道必须先声明后使用，拼写错误在构建期就暴露。"""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def channel(
        self,
        name: str,
        reducer: Reducer = last,
        default: Any = None,
        *,
        description: str = "",
    ) -> StateSchema:
        if name in self._channels:
            raise GraphDefinitionError(f"通道重复声明: {name}")
        self._channels[name] = Channel(name, reducer, default, description)
        return self

    @property
    def channels(self) -> Mapping[str, Channel]:
        return dict(self._channels)

    def __contains__(self, name: object) -> bool:
        return name in self._channels

    def reducer(self, name: str) -> Reducer:
        self._require(name)
        return self._channels[name].reducer

    def default_state(self) -> dict[str, Any]:
        return {name: copy.deepcopy(ch.default) for name, ch in self._channels.items()}

    def _require(self, name: str) -> None:
        if name not in self._channels:
            raise GraphDefinitionError(f"未声明的状态通道: {name}")


# --------------------------------------------------------------------------- #
# 只读视图
# --------------------------------------------------------------------------- #
class StateView:
    """节点看到的只读快照。写入无效（深拷贝），必须返回 Patch。"""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = data

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self._data[name]

    def __contains__(self, name: object) -> bool:
        return name in self._data

    def keys(self) -> Iterable[str]:
        return self._data.keys()

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    def with_overlay(self, overlay: Mapping[str, Any]) -> StateView:
        """给 LoopNode 用：在父状态之上盖一层未提交的改动。"""
        merged = copy.deepcopy(dict(self._data))
        merged.update(copy.deepcopy(dict(overlay)))
        return StateView(merged)


# --------------------------------------------------------------------------- #
# Blackboard
# --------------------------------------------------------------------------- #
class Blackboard:
    """状态的唯一持有者。apply 是唯一写入口，且在此处做权限校验。"""

    def __init__(self, schema: StateSchema, state: Mapping[str, Any] | None = None) -> None:
        self._schema = schema
        self._state: dict[str, Any] = schema.default_state()
        if state:
            for name, value in state.items():
                self._schema._require(name)
                self._state[name] = copy.deepcopy(value)
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    @property
    def schema(self) -> StateSchema:
        return self._schema

    def view(self, names: Sequence[str] | None = None) -> StateView:
        if names is None:
            return StateView(copy.deepcopy(self._state))
        picked: dict[str, Any] = {}
        for name in names:
            self._schema._require(name)
            picked[name] = copy.deepcopy(self._state[name])
        return StateView(picked)

    def read(self, name: str) -> Any:
        self._schema._require(name)
        return copy.deepcopy(self._state[name])

    def apply(self, patch: Patch, *, node_id: str, writes: Sequence[str]) -> None:
        """合并补丁。写未声明通道直接抛错——这是防止节点越权写状态的硬约束。"""
        allowed = set(writes)
        unknown = set(patch.channels) - allowed
        if unknown:
            raise GraphDefinitionError(
                f"节点 {node_id} 试图写入未声明通道 {sorted(unknown)}（已声明 {sorted(allowed)}）"
            )
        for name, value in patch.channels.items():
            reducer = self._schema.reducer(name)
            self._state[name] = reducer(self._state.get(name), value)
        self._version += 1

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._state)

    def merge_overlay(self, overlay: Mapping[str, Any], patch: Patch) -> None:
        """LoopNode 内部使用：把 patch 按 reducer 合进本地 overlay，不触碰主状态。"""
        for name, value in patch.channels.items():
            reducer = self._schema.reducer(name)
            overlay[name] = reducer(dict(overlay).get(name, self._state.get(name)), value)


def fingerprint(*parts: Any) -> str:
    """幂等键：同一输入 + 同一 prompt 版本 → 同一指纹，恢复时可直接跳过。"""
    import hashlib
    import json

    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class RunState:
    """一次运行的元信息（与业务状态分开存放）。"""

    run_id: str
    question: str
    as_of: str
    graph_version: str = "v1"
    status: str = "QUEUED"
    usage: dict[str, Any] = field(default_factory=dict)
