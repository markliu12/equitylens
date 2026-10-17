# 引擎设计说明

引擎是项目的技术核心，共 10 个文件、约 1600 行。它不知道 LLM 是什么，也不知道数据从哪来。

## 1. 为什么不用 LangGraph

不是"为了造轮子"，而是三个需求它给不了（或者需要绕）：

1. **checkpoint 粒度**：需要"每节点一次、状态可读、可与业务表同库"的快照，便于直接 SQL 查询排障；
2. **降级语义**：需要一个节点失败时按角色产出"合法的降级产物"，而不是抛异常或返回 None；
3. **事件协议**：需要 22 种业务事件、run 内单调 seq、SSE 断线续传的精确语义。

用框架的话这三点都要在外面再包一层，最后代码量差不多，但排障时要同时理解两套抽象。
代价是我必须自己保证调度正确性——所以引擎部分的测试密度最高（环检测、并行度、失败传播、
取消、恢复一致性都有断言）。

## 2. 状态：Blackboard + Reducer

```python
class Blackboard:
    def view(self, names: Sequence[str] | None = None) -> StateView: ...
    def apply(self, patch: Patch, *, node_id: str, writes: Sequence[str]) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
```

- `view()` 返回**深拷贝**，节点改了也回不到板上；想写就必须返回 `Patch`。
- `apply()` 校验补丁里的通道是否在 `node.writes` 里，越权直接抛 `GraphDefinitionError`。
- reducer 是纯函数，可单测：`append` / `append_unique(key)` / `merge_map` / `last` / `sum_usage`。

一个真实踩过的坑（已固化为回归测试）：`append` 的 `old` 可能是单个 dict 而不是列表
（FanOut 首次合并时直接取用原始值），`list(dict)` 会得到键名列表——类型完全合法，
运行时却让下游拿到一堆字符串。

## 3. 调度：依赖计数

```python
remaining[node_id] = sum(1 for dep in node.deps if statuses[dep] not in SATISFIED)
# 上游进入 SUCCEEDED / SKIPPED → 下游 remaining -= 1 → 归零入 ready 队列
```

相比"拓扑排序后逐层执行"：分层执行要等整层最慢的节点，并行度被浪费；依赖计数允许跨层流水线。

- 并发控制：`max_parallel`（节点级）+ 三把信号量（LLM / 工具 / 域名）。
- `SKIPPED` 也算"依赖已满足"，否则上游可选节点一跳过，下游就永远等不到。

## 4. Checkpoint 与恢复

```python
async def _persist(run_id, bb, statuses, result):
    await store.save_node(NodeRecord(...))
    await store.save_snapshot(Snapshot(seq=++, state=bb.snapshot(), statuses=...))
```

恢复流程（`Scheduler._prepare`）：

1. 载入最近快照 → 恢复 state 与节点状态；
2. 把 `FAILED / CANCELLED` 节点**及其下游**重置为 `PENDING`
   （否则它们卡在终态：既不被调度，又让下游永远等不到依赖，整个 run 静默停摆）；
3. 其余 `SUCCEEDED / SKIPPED` 直接视为完成，不再执行。

三重幂等保证恢复不重复计费：

| 层 | 机制 |
|---|---|
| 节点 | 终态直接跳过（不重跑） |
| 工具 | 按 `(provider, method, args, as_of)` 命中缓存 |
| LLM | 离线模式走确定性模型；在线模式可扩展为 cassette 回放 |

## 5. 重试与熔断

失败类型决定重试策略，而不是"失败就重试三次"：

| FailureKind | 可重试 | 处理 |
|---|---|---|
| `TRANSIENT` / `RATE_LIMIT` / `TIMEOUT` | 是 | 指数退避 + jitter |
| `SCHEMA` | 是 | 带着字段级错误回灌重试（见第 7 节） |
| `PROVIDER_DOWN` | 否 | 换源或降级 |
| `BUDGET` | 否 | 立即中止（继续重试只会更糟） |
| `POLICY` | 否 | 合规拦截，必须人工处理 |

熔断：滑动窗口（最近 10 次）失败率 ≥ 50% 且样本 ≥ 4 → 打开 60s，期间直接跳过该模型。

## 6. 预算与上下文

```python
budget.check(estimated_tokens=estimate, node_id=role)   # 调用前
result = await client.complete(...)
budget.charge(model=..., prompt_tokens=..., completion_tokens=..., node_id=role)  # 调用后
```

上下文装配按优先级裁剪（任务说明 > 输出 schema > 必需证据 > 上游结论 > 历史摘要），
预算不足时先砍可选长文，永远保留任务和 schema——否则模型会开始编字段。

token 估算不引入 tiktoken：中文约 1.5 字/token、英文约 4 字符/token 的经验公式已经够用，
且无需额外二进制依赖；真实用量以 provider 返回的 `usage` 为准。

## 7. 结构化输出修复

```
json_object 模式 → 清洗（去 ```json 围栏 / 尾逗号）→ Pydantic 校验
   ├─ 通过 → 返回
   └─ 失败 → 把「字段路径 + 期望类型」回灌为一条 user 消息 → 重试（最多 2 次）
              └─ 仍失败 → 抛 SCHEMA，由节点策略决定降级还是中止
```

`repair_rate` 会写进指标：它是 prompt 质量最灵敏的信号，比"看起来对不对"可靠。

schema 嵌套刻意控制在 2 层以内（模型 JSON 合法率明显更高），且**不给需要校验的字段设默认值**——
Pydantic 不校验默认值，给 `sub_questions` 一个 `default_factory=list` 会让"模型根本没输出这个字段"
静默通过。

## 8. LoopNode：有界循环

```python
overlay = {}
for round in range(max_rounds):
    for node in body:
        patch = await node.run(ctx.with_overlay(overlay))
        overlay = apply_reducers(overlay, patch)      # 局部合并，不碰主状态
    if until(view.with_overlay(overlay)):
        break
return Patch(channels=overlay)                        # 循环结束才提交一次
```

外层看到的状态变化是原子的，checkpoint 不会出现"辩论跑到一半"的中间态。

辩论终止三重条件（任一满足即停，并记录是哪条触发的）：

1. 轮数达到上限；
2. `new_info_ratio < 0.2`（在复读，继续下去只烧钱）；
3. 领先方得分差两轮变化 < 0.3（已收敛）。

## 9. 测试策略

| 层次 | 关注点 | 数量 |
|---|---|---|
| 单元 | reducer、写权限、环检测、预算、结构化修复、引用审计、辩论终止 | 大部分 |
| 集成 | 并行度（耗时断言）、失败传播、取消、崩溃恢复一致性 | 关键 |
| 端到端 | 离线全链路 + 报告 schema + 引用指标 + 产物落盘 | 4 条 |

并行度用**耗时断言**而不是"检查是否用了 gather"：`4 × 0.15s` 必须 < `0.6 × 0.6s`。
恢复一致性用"重新跑一遍全流程"做对照，比较最终 state——这比断言中间状态可靠。
