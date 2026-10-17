"""离线种子数据（示例数据集）。

**重要**：这里是人工构造的演示数据，数值与真实财报无关，`source` 一律标注
"示例数据集"，避免被误当成公开披露信息。真实接入（akshare / 巨潮）在下一阶段。
"""

from __future__ import annotations

SEEDS: dict[str, dict] = {
    "600519": {
        "entity": {
            "name": "贵州茅台",
            "code": "600519",
            "industry": "白酒（食品饮料）",
            "exchange": "SH",
        },
        "as_of": "2026-09-30",
        "financials": {
            #: 结构化信号：只放能直接参与交叉计算的原始数值，不放任何结论。
            #: 缺失的项就不要给键——派生层会跳过并记录 unavailable，而不是当成 0 猜。
            "signals": {
                "revenue_growth": 0.156,
                "net_margin": 0.524,
                "net_margin_change": 0.003,
                "net_profit": 862.3,
                "cash_flow": 912.6,
                "cash_flow_growth": 0.098,
                "q_revenue_growth": -0.012,
                "q_revenue_qoq": -0.041,
                "contract_liability_growth": 0.121,
                "dividend_payout": 0.391,
                "price_hike_pct": 0.03,
                "capacity_new_tons": 1.98,
            },
            "items": [
                {
                    "key": "fin_revenue",
                    "name": "营业总收入",
                    "value": "1,741.0 亿元",
                    "period": "2025 年报",
                    "title": "2025 年度营业总收入 1,741.0 亿元，同比增长 15.6%",
                    "snippet": (
                        "2025 年度实现营业总收入 1,741.0 亿元，同比增长 15.6%；"
                        "归属于上市公司股东的净利润 862.3 亿元，同比增长 16.2%。"
                        "（示例数据集，数值非真实财报）"
                    ),
                    "source": "示例数据集 · 年度报告摘要",
                    "url": "https://example.invalid/cninfo/600519/annual-2025",
                    "published_at": "2026-03-28",
                    "tier": 2,
                },
                {
                    "key": "fin_margin",
                    "name": "销售净利率",
                    "value": "52.4%",
                    "period": "2025 年报",
                    "title": "销售净利率 52.4%，较上年提升 0.3 个百分点",
                    "snippet": (
                        "报告期内销售净利率为 52.4%，同比提升 0.3 个百分点，"
                        "主要受产品结构上移与费用投放效率改善影响。（示例数据集）"
                    ),
                    "source": "示例数据集 · 财务指标",
                    "url": "https://example.invalid/cninfo/600519/margin-2025",
                    "published_at": "2026-03-28",
                    "tier": 2,
                },
                {
                    "key": "fin_cashflow",
                    "name": "经营活动现金流净额",
                    "value": "912.6 亿元",
                    "period": "2025 年报",
                    "title": "经营活动产生的现金流量净额 912.6 亿元",
                    "snippet": (
                        "经营活动产生的现金流量净额 912.6 亿元，同比增长 9.8%，"
                        "合同负债余额较年初增长 12.1%。（示例数据集）"
                    ),
                    "source": "示例数据集 · 现金流量表",
                    "url": "https://example.invalid/cninfo/600519/cashflow-2025",
                    "published_at": "2026-03-28",
                    "tier": 2,
                },
                {
                    "key": "fin_q2",
                    "name": "单季度营业收入",
                    "value": "421.8 亿元",
                    "period": "2026Q2",
                    "title": "2026 年第二季度营业收入 421.8 亿元，环比下降 4.1%",
                    "snippet": (
                        "2026 年第二季度实现营业收入 421.8 亿元，环比下降 4.1%，"
                        "同比下降 1.2%，为近三年首次单季同比转负。（示例数据集）"
                    ),
                    "source": "示例数据集 · 季度报告",
                    "url": "https://example.invalid/cninfo/600519/q2-2026",
                    "published_at": "2026-08-20",
                    "tier": 2,
                },
            ],
        },
        "announcements": [
            {
                "key": "ann_dividend",
                "title": "2025 年度利润分配方案公告",
                "snippet": (
                    "拟每 10 股派发现金红利 268.5 元（含税），合计派发现金红利约 337.2 亿元，"
                    "分红率约 39.1%，方案尚需股东大会审议。（示例数据集）"
                ),
                "url": "https://example.invalid/cninfo/600519/dividend-2025",
                "published_at": "2026-04-12",
                "tier": 1,
            },
            {
                "key": "ann_capacity",
                "title": "关于技改扩产项目进展的公告",
                "snippet": (
                    "公司技改扩产项目一期已进入试生产阶段，设计新增基酒产能约 1.98 万吨，"
                    "预计 2027 年逐步释放。（示例数据集）"
                ),
                "url": "https://example.invalid/cninfo/600519/capacity-2026",
                "published_at": "2026-06-05",
                "tier": 1,
            },
            {
                "key": "ann_price",
                "title": "关于部分产品出厂价格调整的公告",
                "snippet": (
                    "自 2026 年 7 月 1 日起，对部分系列产品出厂价格进行小幅上调，"
                    "平均上调幅度约 3%，公司提示价格调整对业绩的影响存在不确定性。（示例数据集）"
                ),
                "url": "https://example.invalid/cninfo/600519/price-2026",
                "published_at": "2026-06-28",
                "tier": 1,
            },
        ],
        "news": [
            {
                "key": "news_1",
                "title": "高端白酒动销环比改善，渠道库存回落至合理区间",
                "snippet": (
                    "渠道调研显示，中秋旺季前高端白酒动销环比改善，核心单品批价企稳，"
                    "经销商库存周期由 2.5 个月回落至 1.8 个月。"
                ),
                "url": "https://example.invalid/news/n1",
                "published_at": "2026-09-12",
                "tier": 3,
                "sentiment": 0.55,
            },
            {
                "key": "news_2",
                "title": "白酒行业产量连续三个季度负增长，存量竞争加剧",
                "snippet": (
                    "统计口径下规模以上白酒企业产量连续三个季度同比负增长，"
                    "行业进入存量竞争阶段，中小品牌出清速度加快。"
                ),
                "url": "https://example.invalid/news/n2",
                "published_at": "2026-09-08",
                "tier": 3,
                "sentiment": -0.45,
            },
            {
                "key": "news_3",
                "title": "机构调研密集，关注点集中在价格体系与渠道利润",
                "snippet": (
                    "近一个月共有 23 家机构参与调研，问题集中于价格体系稳定性、"
                    "渠道利润分配以及系列酒放量节奏。"
                ),
                "url": "https://example.invalid/news/n3",
                "published_at": "2026-09-15",
                "tier": 3,
                "sentiment": 0.1,
            },
            {
                "key": "news_4",
                "title": "次高端价格带促销力度加大，挤压效应显现",
                "snippet": (
                    "部分地区次高端产品终端促销力度加大，成交价下移，"
                    "对高端价格带形成一定情绪扰动。"
                ),
                "url": "https://example.invalid/news/n4",
                "published_at": "2026-09-03",
                "tier": 3,
                "sentiment": -0.3,
            },
            {
                "key": "news_5",
                "title": "食品饮料板块估值处于近五年中位数下方",
                "snippet": (
                    "板块整体市盈率回落至近五年中位数下方约 12%，"
                    "资金面呈现结构性回流迹象。"
                ),
                "url": "https://example.invalid/news/n5",
                "published_at": "2026-09-18",
                "tier": 4,
                "sentiment": 0.25,
            },
        ],
        "industry": {
            "name": "白酒",
            "cycle_stage": "放缓",
            #: 行业层面的原始增速，用于和公司增速做超额对比（百分点）
            "output_growth": -0.032,
            "output_growth_change_pp": 0.006,
            "summary": (
                "白酒行业进入存量竞争阶段：高端价格带需求韧性相对较强，"
                "次高端库存去化仍在进行，行业产量连续三季负增长。"
            ),
            "evidence_key": "ind_demand",
            "items": [
                {
                    "key": "ind_demand",
                    "title": "行业需求：高端韧性、次高端承压",
                    "snippet": (
                        "高端白酒受益于商务与礼赠场景恢复，需求韧性较强；"
                        "次高端受促销与库存影响，价格带承压。（示例数据集）"
                    ),
                    "url": "https://example.invalid/industry/baijiu-demand",
                    "published_at": "2026-09-10",
                    "tier": 3,
                },
                {
                    "key": "ind_output",
                    "title": "行业产量增速 -3.2%",
                    "snippet": (
                        "2026 年前三季度规模以上白酒企业产量同比下降 3.2%，"
                        "降幅较上半年收窄 0.6 个百分点。（示例数据集）"
                    ),
                    "url": "https://example.invalid/industry/baijiu-output",
                    "published_at": "2026-09-09",
                    "tier": 3,
                },
                {
                    "key": "ind_peers",
                    "title": "同业对比：估值与增速",
                    "snippet": (
                        "同业可比公司市盈率中位数约 19 倍，营收增速中位数约 6.5%；"
                        "头部企业估值溢价收窄。（示例数据集）"
                    ),
                    "url": "https://example.invalid/industry/baijiu-peers",
                    "published_at": "2026-09-16",
                    "tier": 3,
                },
            ],
            "metrics": [
                {
                    "name": "行业产量增速",
                    "value": "-3.2%",
                    "period": "2026Q1-Q3",
                    "evidence_key": "ind_output",
                },
                {
                    "name": "同业市盈率中位数",
                    "value": "19 倍",
                    "period": "2026-09",
                    "evidence_key": "ind_peers",
                },
            ],
            "peers": [
                {"name": "五粮液", "pe_ratio": "18.4", "revenue_growth": "7.2%"},
                {"name": "泸州老窖", "pe_ratio": "17.1", "revenue_growth": "5.8%"},
                {"name": "山西汾酒", "pe_ratio": "22.6", "revenue_growth": "11.4%"},
            ],
        },
    }
}

DEFAULT_CODE = "600519"
