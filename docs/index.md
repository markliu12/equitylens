# EquityLens 研镜

面向 A 股的研报生成与引用溯源系统。输入一个研究问题与基准日期，系统编排 14 个节点、9 类 Agent
生成中文研究报告；报告中标注为事实的陈述均绑定到原始证据，可在 HTML 版本中逐条回跳至原文片段。

---

## 从哪开始

<div class="grid cards" markdown>

-   **查看示例报告**

    ---

    [打开静态示例报告](examples/maotai-2026q3/report.html){ target="_blank" }

    无需安装。示例使用仓库内置的合成数据，数值与真实财报无关。

-   **本地运行**

    ---

    [快速开始与用法](USAGE.md)

    默认 offline 模式：零外网、零费用、不需要 API Key。

-   **了解设计**

    ---

    [编排引擎设计](engine-design.md) ·
    [ADR-0001 不引入编排框架](adr/0001-no-orchestration-framework.md)

    包含"为什么不用 LangGraph"的完整论证。

</div>

---

## 系统约束

生成式模型用于研究报告时，落地的主要障碍在于可核验性：读者无法判断某一句结论来自原始披露，
还是模型自行推断。系统把可核验的部分交给确定性代码，只在需要归纳与解释的环节使用模型。

| 问题 | 处理方式 | 实现位置 |
|---|---|---|
| 数字或结论来源不明 | 标注为事实的陈述必须携带 `evidence_id`，其 `quote` 须在证据原文中逐字命中 | `report/citations.py`、`tools/evidence.py` |
| 使用了基准日期之后的信息 | 证据的 `published_at` 晚于研究基准日期时丢弃，并记入审计输出 | `tools/evidence.py` |
| 任务中断导致重复计费 | 每个节点完成后写入 checkpoint，恢复时跳过已成功节点 | `engine/checkpoint.py` |
| 成本不可控 | `BudgetGuard` 在每次 LLM 调用前校验金额、token、调用次数三项上限 | `engine/budget.py` |
| 输出涉投资建议 | 合规节点拦截买卖建议、目标价、收益承诺类表述，回修后重新审计 | `agents/risk.py` |

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

**不做投资建议。** 系统只产出研究信息整理结果，合规节点会拦截买卖建议、目标价与收益承诺类表述。
示例数据集为合成数据，不可作为真实披露信息引用。

本项目仅用于技术学习与研究信息整理，不构成任何投资建议、要约或承诺。

---

完整的安装、配置与使用说明见 [GitHub 仓库 README](https://github.com/markliu12/equitylens#readme)。
