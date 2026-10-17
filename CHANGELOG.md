# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
`0.x` 阶段 API 尚未稳定，次要版本之间可能存在不兼容变更。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### 计划中

- 巨潮公告接入（`DATA_PROVIDER=cninfo`）
- 公网演示部署：回放模式 + 邀请码 + 日预算（配置项已就绪，缺部署）
- 把 `engine/` 抽成独立包，并提供非金融场景的最小示例
- 技术面分析师（代码位已留，默认关闭）

---

## [0.1.0] - 2026-10-17

首个可运行版本：9 类 Agent、14 个编排节点，产出一份每条结论都能点回原文的中文研究报告。

### 新增

- **派生信号层**（`tools/signals.py`）：把公司增速、行业增速、同业中位数、现金流与净利润
  交叉成确定性判断素材，每个值附带算式（`basis`）。模型只负责解释，不负责重算。
- **交叉判断的核心结论**：终裁不再搬运分析师原话，每条核心结论至少挂 2 个独立来源。
- **内容形态门禁**（`tests/unit/test_report_quality.py`，8 项）：章节不得重复、核心结论必须
  是判断而非指标转述、事实必须挂证据、分歧必须含反驳结构。
- **派生信号门禁**（`tests/unit/test_signals.py`，10 项）：阈值边界，以及"缺数据不得填 0"。
- **文档站**：`mkdocs.yml` + GitHub Pages，含架构、ADR、数据源说明。
- **静态示例报告**：`docs/examples/maotai-2026q3/`，引用编号可点击跳转，无需安装即可验证证据链。
- **仓库入口**：英文 README、issue / PR 模板、`docker-compose.yml`、CHANGELOG。

### 修复

- 核心结论与基本面章节**结构性重复**：`_editor` 曾把分析师 claims 的前三条同时用作
  核心结论和明细，两份内容必然相同。
- 行业分析师拿不到同业对比证据：证据按全局 `(tier, 日期)` 排序后截断，导致"估值位置"
  这条结论永远挂不上证据，覆盖率虚高。
- 辩论循环读不到派生信号：`LoopNode` 的只读视图按 `reads` 裁剪而漏了 `research_data`，
  空方论点退化成"给结论加前缀"的复述。
- 前端首页硬编码节点数，已改为从 `/api/meta/graph` 动态获取。

### 变更

- 编排节点从 13 个增加到 14 个（新增 `derive_signals`，位于 `evidence_merge` 与 `analysts` 之间）。
- 首页文案从"讲工程手段"改为"讲用户收益"。

### 已知边界

- 内置示例数据为**合成数据**，数值与真实财报无关，URL 指向 `example.invalid`。
- 单进程运行，未做多租户隔离；公网部署需自行加配额与鉴权。
- 报告为中文；A 股数据源，暂不支持港股 / 美股。

---

[Unreleased]: https://github.com/markliu12/equitylens/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/markliu12/equitylens/releases/tag/v0.1.0
