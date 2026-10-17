# 数据源说明

## 1. 默认状态：fixture（示例数据集）

`src/equitylens/fixtures/seeds/data.py` 是人工构造的示例数据，包含：

- 财务指标 4 项（营收、净利率、现金流、单季营收）
- 公司公告 3 条（分红、技改扩产、调价）
- 财经新闻 5 条（含情绪标注）
- 行业数据 3 条 + 同业对比 3 家

**所有条目的 `source` 字段都标注"示例数据集"，`url` 使用 `example.invalid` 域名**，
避免被误认为真实披露信息。

选择 fixture 作为一等公民而不是测试桩的理由：

1. CI 与单测不能依赖外网，也不能依赖模型采样；
2. 演示不能赌现场网络与 API 额度；
3. 真实数据源抖动时，它是最后一道降级——宁可报告标注"示例数据"，也不要整个功能不可用。

## 2. Provider 协议

```python
class MarketDataProvider(Protocol):
    async def resolve_entity(self, query: str) -> EntityRef | None: ...
    async def financials(self, code: str) -> dict[str, Any]: ...
    async def industry(self, code: str) -> dict[str, Any]: ...
    async def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]: ...
    async def news(self, code: str, limit: int = 10) -> list[dict[str, Any]]: ...
```

上层（分析师、证据链）只依赖这个语义契约。新增数据源 = 新增一个实现 + 一套契约测试
（断言字段结构而不是断言具体数值，否则数据一更新测试就红）。

## 3. 真实接入计划

| 数据 | 候选源 | 注意点 |
|---|---|---|
| A 股行情 / 财务 | akshare（免费） | ✅ 已实现（懒加载 + 降级 + 25 契约测试）；接口名变动频繁，改动集中在 `providers/akshare.py` |
| 公司公告 | 巨潮资讯（官方披露） | 一级证据，优先级最高；注意分页与频率 |
| 财经新闻 | 东方财富 / 新浪财经 | 三级证据，只存标题 + ≤500 字摘录 + 链接 |
| 行业数据 | 国家统计局 / 行业协会 | 口径差异大，不同来源不要强行合并 |

已预留的工程设施：

- `tools/cache.py`：按方法分级 TTL（行情 1 天 / 财报 30 天 / 新闻 6 小时 / 公告 1 天）；
- `tools/ratelimit.py`：按域名令牌桶，默认 1 QPS；
- `tools/research.py`：时点过滤（`published_at > as_of` 直接丢弃，防前视偏差）；
- `tools/evidence.py`：内容哈希去重 + 三级 tier 分级。

## 4. 合规与限流原则

1. 只访问公开页面与公开接口，遵守 `robots.txt`，不绕登录与验证码；
2. 只存储 `url + 发布时间 + ≤500 字摘录 + 抓取时间`，不镜像全文、不再分发原始数据；
3. 按域名限流（1 QPS，峰值 2），连续失败熔断当日该源，绝不并发轰炸；
4. User-Agent 标明项目与联系方式；
5. 报告强制免责声明，不输出买卖建议、目标价、收益预测。

## 5. 切换方式

```bash
pip install akshare   # 可选扩展，非核心依赖（建议 Python 3.12，3.13 轮子可能不全）
# .env
APP_MODE=dev
DATA_PROVIDER=akshare
```

`runtime.build_provider()` 是唯一的切换点：`DATA_PROVIDER=akshare` 且非 offline 时加载
`AkshareProvider`；akshare 未安装、或处于 offline 模式时，自动降级到 `FixtureProvider`
（报告会标注"示例数据"，功能不中断）。
