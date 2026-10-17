# 架构说明

## 1. 分层与依赖方向

```
api / cli
   ↓
runtime（装配层）
   ↓                    ↘
graph（DAG 装配）      agents（9 个 Agent）
   ↓                    ↘
engine（纯执行内核）     llm / tools / report
```

**硬约束**：`engine/` 不 import `llm/`、`tools/`、`agents/`。
节点需要的所有外部能力通过 `Services` 注入（结构化调用、工具调用、事件发射、预算、缓存）。
这条约束换来两个好处：引擎可以脱离 LLM 单测；更换模型或数据源不会牵动调度逻辑。

## 2. 状态通道

状态是一组**声明式通道**，每个通道有自己的 reducer。通道必须先声明后使用，
拼写错误会在 `GraphBuilder.validate()` 阶段报错，而不是运行时静默丢字段。

| 通道 | reducer | 生产者 | 说明 |
|---|---|---|---|
| `question` / `as_of` | last | 外部 | 初始输入 |
| `entity` | last | resolve_entity | 标的（名称 / 代码 / 行业） |
| `plan` | last | plan | 子问题、所需数据、启用分析师 |
| `evidence` | append_unique(id) | retrieve（3 路并发） | 证据，按内容哈希去重 |
| `evidence_gaps` | append | retrieve | 数据缺口，进入报告"待核实" |
| `research_data` | merge_map | retrieve | 按维度聚合的事实 / 指标 / 新闻 |
| `evidence_summary` | last | evidence_merge | 数量、分级分布、来源数 |
| `analyst_reports` | append | analysts（3 路并发） | 三份结构化分析 |
| `analyst_summary` | last | analyst_merge | 立场、置信度、缺口 |
| `bull_argument` / `bear_argument` | last | debate 内部 | 每轮覆盖，供下一轮互相反驳 |
| `debate_rounds` | append | round_judge | 每轮打分（含 new_info_ratio） |
| `verdict` | last | verdict | 采纳 / 驳回的结论 |
| `draft_report` | last | editor_draft | 报告草稿 |
| `citation_audit` | last | fact_check | 引用审计结果 |
| `risk_review` | last | compliance | 合规结论 |
| `final_report` | last | revise → finalize | 终稿 |

**为什么不用一个大 dict**：并行节点同时写同一字段时，结果取决于完成顺序，属于"本地跑十次都对、
线上偶发错一次"的隐性竞态。reducer 让"追加 / 覆盖 / 合并"变成显式声明，也让快照天然可序列化。

## 3. 节点类型

| 类型 | 是否调用 LLM | 职责 | 本项目的实例 |
|---|---|---|---|
| `ToolNode` | 否 | 确定性计算 / 取数 / 校验 | resolve_entity、fact_check、revise、finalize |
| `AgentNode` | 是（结构化输出） | 单次调用 → 一个 Pydantic 对象 | plan、3 分析师、editor_draft |
| `JudgeNode` | 是（temperature=0） | 打分 / 裁决 | round_judge、compliance |
| `FanOut` | — | 运行时展开 N 份并发 | retrieve、analysts |
| `Aggregate` | — | fan-in + 法定人数 | evidence_merge、analyst_merge |
| `LoopNode` | — | 有界循环 + 终止判定 | debate |

## 4. 失败传播语义

| 策略 | 语义 | 典型用途 |
|---|---|---|
| `ABORT` | 关键路径失败则整个 run 失败 | 标的识别不出来（后续全是垃圾） |
| `SKIP` | 该节点放弃，下游用已有状态继续，run 标记 `PARTIAL` | 某个维度取数失败 |
| `DEGRADE` | 使用节点自带的降级产物（合法但标注数据不足） | 分析师模型调用失败 |

`PARTIAL` 是有意义的状态：报告仍然交付，但"待核实问题"里会写明哪个维度降级了。

## 5. 事件与可观测

22 种事件（`run.*` / `node.*` / `fanout.*` / `loop.*` / `tool.*` / `budget.*` / `report.ready`），
全部落 `events` 表，`seq` 在 run 内单调。SSE 端点的 `Last-Event-ID` 直接映射为
`WHERE seq > ?` 查询，所以"前端刷新丢进度"这类问题从设计上就不存在。

前端时间线、辩论面板、预算表全部由事件派生——不为 UI 单独维护一套状态。

## 6. 关键时序（一次完整运行）

```
POST /api/runs
  └→ RunManager.start       写 runs(QUEUED)，起 asyncio.Task
       └→ GraphRunner.run   发 run.started
            └→ Scheduler    依赖计数 → 就绪队列 → 并发上限 8
                 每个节点：node.started → 执行（带超时/重试）
                        → blackboard.apply(Patch)
                        → save_node + save_snapshot
                        → node.completed（带 token / 耗时 / 降级标记）
            └→ run.completed / run.failed
  SSE 消费者：先补历史（seq > Last-Event-ID），再订阅增量
```

## 7. 为什么没有消息队列

单进程内 `dict[run_id, Task]` + 一个全局信号量就够：瓶颈是模型调用，不是调度吞吐。
引入 Celery/Redis 后，checkpoint 语义、取消语义、事件顺序都要重新设计一遍。
扩展路径：把 `RunManager` 的任务提交抽成 `JobQueue` 接口，换 DB 队列 + 独立 worker 即可，
引擎层不需要任何改动（因为快照是纯 JSON）。
