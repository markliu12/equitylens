# EquityLens 研镜

面向 A 股的研报生成与引用溯源系统。输入一个研究问题与基准日期，系统编排 14 个节点、9 类 Agent
生成中文研究报告；报告中标注为事实的陈述均绑定到原始证据，可在 HTML 版本中逐条回跳至原文片段。

---

## 从哪开始

<div class="grid cards" markdown>

- **想先看产出**

    [打开一份静态示例报告](examples/maotai-2026q3/report.html){ target=_blank }

    不需要安装任何东西。点几下引用编号，看看证据链是什么样的。
    示例使用仓库内置的合成数据，数值与真实财报无关。

- **想跑起来**

    [五分钟跑通](USAGE.md)

    默认 offline 模式：零外网、零费用、不需要 API Key。

- **想知道为什么这么设计**

    [编排引擎设计](engine-design.md) · [架构决策记录](adr/0001-no-orchestration-framework.md)

    包括"为什么不用 LangGraph"这个问题的完整回答。

</div>

---

## 它解决什么问题

AI 写研报，最大的问题不是"写得不够好"，而是**你不敢用**。

| 你心里的顾虑 | 这里的做法 |
|---|---|
| "这个数字是它编的吗？" | 声明为"事实"的结论必须挂 `evidence_id`，`quote` 必须能在原文里逐字找到 |
| "它是不是用了当时还没发生的信息？" | 证据发布时间晚于基准日 → 丢弃并记入审计 |
| "跑一半断了，钱是不是白花了？" | 每节点写快照，恢复时已完成节点的模型调用次数为 0 |
| "会不会月底看到天价账单？" | `BudgetGuard` 在每次调用**之前**检查成本 / token / 次数上限 |
| "报告会不会给我投资建议？" | 合规节点拦截买卖建议、目标价、收益承诺，回修后重新审计 |

---

## 核心机制一览

| 机制 | 位置 | 锁住它的测试 |
|---|---|---|
| 内容哈希证据 id + 三级引用校验 | `tools/evidence.py`、`report/citations.py` | `test_citations.py` |
| 依赖计数调度 / 恢复 / 取消 | `engine/scheduler.py`、`engine/checkpoint.py` | `test_scheduler.py`、`test_resume.py` |
| 跨维度派生信号（份额 vs 景气、全年 vs 单季） | `tools/signals.py` | `test_signals.py` |
| 核心结论必须多源交叉 | `llm/offline.py`、`agents/verdict.py` | `test_report_quality.py` |
| 成本三重上限 | `engine/budget.py` | `test_budget.py` |

---

## 边界

**不做投资建议。** 系统只产出研究整理，合规节点会拦截操作性表述。
示例数据集为合成数据，请勿引用为真实披露信息。

本项目仅用于技术学习与研究信息整理，不构成任何投资建议、要约或承诺。
