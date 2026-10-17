# ADR-0002：Blackboard + 只读视图 + 声明式写权限

- 状态：已采纳
- 日期：2026-09

## 背景

FanOut 之后有多个节点并行执行，都需要写状态。最初的做法是所有节点共享一个 dict。

## 问题

共享 dict 的写入结果取决于完成顺序。本地跑十次都对，线上偶发错一次——
这类竞态一旦发生，日志里看不出任何异常，只能靠复现。

## 决策

1. 节点拿到 `StateView`（深拷贝），改它也回不到板上；
2. 写必须返回 `Patch`；
3. `Patch` 只能写节点 `writes` 声明的通道，越权在 `apply()` 处抛错（构建期也会校验通道存在）；
4. 合并语义由通道的 reducer 显式声明：`append` / `append_unique(key)` / `merge_map` / `last` / `sum_usage`。

## 理由

- 竞态从"依赖时序"变成"依赖声明"，可静态检查；
- 快照天然可 JSON 序列化，checkpoint 无需转换层；
- reducer 是纯函数，可以单独覆盖测试。

## 代价

- 每个通道都要指定 reducer，声明成本上升；
- 深拷贝视图在状态很大时有开销（当前快照 < 200KB，可接受）。

## 陷阱记录

`append` 的 `old` 可能是单个对象而不是列表（FanOut 首次合并时直接取用原始值）。
`list(dict)` 会得到键名列表，类型完全合法但下游拿到一堆字符串。
此坑已固化为回归测试 `test_append_handles_single_object_as_first_value`。
