# 示例产出

这里放的是真实运行产物，**不是手写的宣传样例**。

## maotai-2026q3

| 文件 | 说明 |
|---|---|
| `report.html` | 渲染后的完整报告。引用编号可点击，会跳到页面底部的证据链 |
| `report.md` | Markdown 版本，便于 diff 和代码审查 |
| `citation_audit.json` | 引用审计结果：19 条结论、覆盖率 100%、命中率 100%、无前视偏差 |

生成方式：

```bash
equitylens run "分析贵州茅台最近一期经营情况与行业景气度" --offline
```

## 必须说明的一点

`--offline` 模式使用的是 `src/equitylens/fixtures/seeds/` 里的**示例数据集**，
数值为人工构造，与真实财报无关，URL 指向 `example.invalid` 不会跳转。

它存在的作用是让任何人都能在零网络、零费用的条件下检查系统的**结构与逻辑**：
引用能不能追溯、跨维度判断能不能复现、审计能不能通过。

接真实数据请设置 `DATA_PROVIDER=akshare` 并配置 `LLM_API_KEY`，见 [data-sources.md](../data-sources.md)。
