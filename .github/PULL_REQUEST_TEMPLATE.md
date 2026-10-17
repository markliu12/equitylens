## 这个 PR 做了什么

<!-- 一句话说清改了什么。如果标题已经说清，这里可以只写动机。 -->

## 为什么

<!-- 关联的 issue、实际遇到的问题、或你想解决的风险点。 -->

## 怎么验证

<!-- 建议给出可复现的命令或断言，而不是"我本地跑过了" -->

```bash
pytest -q
ruff check src tests evals
mypy
```

- [ ] 新增/修改的行为有对应测试
- [ ] offline 模式下可复现（不依赖网络与 API Key）
- [ ] 如果改了提示词，说明是否影响 `tests/unit/test_report_quality.py` 的断言

## 影响面

- [ ] 改了 `engine/`（注意：不得引入对 `llm/` `tools/` `agents/` 的依赖）
- [ ] 改了状态通道 / 图结构（`graph/research_graph.py`）
- [ ] 改了派生信号的阈值（`tools/signals.py` 顶部常量）
- [ ] 改了 Pydantic schema（`agents/schemas.py`）
- [ ] 仅文档 / 注释

## 补充说明

<!-- 取舍、遗留问题、被否掉的方案，都可以写在这里。-->
