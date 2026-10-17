"""akshare 数据源：把 akshare 的同步 DataFrame 接口适配为项目的 async Provider 协议。

为什么这一层值得存在：
1. akshare 接口名一年能改三次（Python 量化圈的共识），上层不应直接依赖它。
   这一层做适配 + 字段映射，akshare 改了只改这一个文件。
2. akshare 返回的是 pandas DataFrame，上层需要的是结构化 dict。
   转换逻辑（列名映射 / 数值格式化 / 缺失字段兜底）集中在这里，不在分析师里散落。
3. akshare 的调用是同步阻塞的，需要 asyncio.to_thread 包装，否则会卡住事件循环。

依赖策略：
- akshare 不在 requirements.txt 里（它是可选扩展，不是核心依赖）。
- 用懒加载：import akshare 只在 __init__ 时执行，离线模式完全不会触碰它。
- 装不上 akshare 的环境（CI / 断网）直接抛 ImportError，由 runtime 降级到 fixture。
"""

from __future__ import annotations

import asyncio
from typing import Any

from equitylens.tools.providers.base import EntityRef

# akshare 返回的 DataFrame 行数上限。akshare 有些接口会返回上千行历史数据，
# 但研究报告只需要最近几期。在这里统一截断，避免上层每个调用方都写 limit 逻辑。
_MAX_FINANCIAL_ROWS = 8
_MAX_NEWS_ROWS = 12
_MAX_ANNOUNCEMENT_ROWS = 10


class AkshareProvider:
    """基于 akshare 的真实 A 股数据源。

    所有方法都是 async，内部用 asyncio.to_thread 包装 akshare 的同步调用。
    返回结构与 FixtureProvider 完全一致，上层（research.py）无需感知差异。
    """

    name = "akshare"

    def __init__(self) -> None:
        # 懒加载：只在这里 import，离线模式永远不会走到这里。
        try:
            import akshare as ak
        except ImportError as exc:
            raise ImportError(
                "akshare 未安装。请运行: pip install akshare\n"
                "注意：akshare 对 Python 3.13 的轮子可能不全，建议使用 3.12。"
            ) from exc
        self._ak = ak

    # ------------------------------------------------------------------ #
    # 辅助：把同步调用丢到线程池，避免阻塞事件循环
    # ------------------------------------------------------------------ #
    async def _call(self, func_name: str, *args: Any, **kwargs: Any) -> Any:
        func = getattr(self._ak, func_name, None)
        if func is None:
            raise AttributeError(f"akshare 无此接口: {func_name}（版本可能已变更）")
        return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------ #
    # 标的解析：把用户输入（名称或代码）解析成 EntityRef
    # ------------------------------------------------------------------ #
    async def resolve_entity(self, query: str) -> EntityRef | None:
        text = (query or "").strip()
        if not text:
            return None

        # 先尝试当作股票代码直接查
        code = _extract_code(text)
        if code:
            entity = await self._try_resolve_by_code(code)
            if entity:
                return entity

        # 再按名称模糊匹配
        entity = await self._try_resolve_by_name(text)
        if entity:
            return entity

        return None

    async def _try_resolve_by_code(self, code: str) -> EntityRef | None:
        try:
            df = await self._call("stock_individual_info_em", symbol=code)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        info = _df_to_dict(df)
        name = str(info.get("股票简称") or info.get("名称") or "")
        industry = str(info.get("行业") or info.get("所属行业") or "")
        exchange = "SH" if code.startswith("6") else ("SZ" if code.startswith(("0", "3")) else "")
        if not name:
            return None
        return EntityRef(
            name=name,
            code=code,
            industry=industry,
            exchange=exchange,
            aliases=(name, code),
        )

    async def _try_resolve_by_name(self, name: str) -> EntityRef | None:
        try:
            df = await self._call("stock_zh_a_spot_em")
        except Exception:
            return None
        if df is None or df.empty:
            return None
        # 名称列可能叫 "名称" 或 "股票名称"
        name_col = "名称" if "名称" in df.columns else df.columns[1] if len(df.columns) > 1 else None
        code_col = "代码" if "代码" in df.columns else df.columns[0]
        if name_col is None:
            return None
        mask = df[name_col].astype(str).str.contains(name, na=False)
        matches = df[mask]
        if matches.empty:
            return None
        row = matches.iloc[0]
        code = str(row[code_col]).strip()
        stock_name = str(row[name_col]).strip()
        exchange = "SH" if code.startswith("6") else ("SZ" if code.startswith(("0", "3")) else "")
        # 行业需要额外查
        industry = ""
        try:
            info_df = await self._call("stock_individual_info_em", symbol=code)
            if info_df is not None and not info_df.empty:
                info = _df_to_dict(info_df)
                industry = str(info.get("行业") or "")
        except Exception:
            pass
        return EntityRef(
            name=stock_name,
            code=code,
            industry=industry,
            exchange=exchange,
            aliases=(stock_name, code),
        )

    # ------------------------------------------------------------------ #
    # 财务数据
    # ------------------------------------------------------------------ #
    async def financials(self, code: str) -> dict[str, Any]:
        """从 akshare 拉财务摘要 + 财务指标，组装成 research.py 需要的结构。"""
        items: list[dict[str, Any]] = []
        signals: dict[str, Any] = {}

        # 1. 财务摘要（营收、净利润等）
        try:
            df = await self._call("stock_financial_abstract", symbol=code)
            if df is not None and not df.empty:
                items.extend(_financial_items_from_abstract(df, code))
        except Exception:
            pass

        # 2. 财务指标（净利率等，用于 signals）
        try:
            df = await self._call("stock_financial_analysis_indicator", symbol=code)
            if df is not None and not df.empty:
                signals = _signals_from_indicators(df)
                if not items:
                    items.extend(_financial_items_from_indicators(df, code))
        except Exception:
            pass

        return {
            "signals": signals,
            "items": items[:_MAX_FINANCIAL_ROWS],
        }

    # ------------------------------------------------------------------ #
    # 行业数据
    # ------------------------------------------------------------------ #
    async def industry(self, code: str) -> dict[str, Any]:
        """从个股信息提取行业，并尝试拉取同业数据。"""
        industry_name = ""
        try:
            df = await self._call("stock_individual_info_em", symbol=code)
            if df is not None and not df.empty:
                info = _df_to_dict(df)
                industry_name = str(info.get("行业") or info.get("所属行业") or "")
        except Exception:
            pass

        items: list[dict[str, Any]] = []
        peers: list[dict[str, Any]] = []

        # 尝试拿行业板块成分股作为同业
        if industry_name:
            try:
                board_df = await self._call(
                    "stock_board_industry_cons_em", symbol=industry_name
                )
                if board_df is not None and not board_df.empty:
                    peers = _peers_from_board(board_df, exclude_code=code, limit=5)
            except Exception:
                pass

        return {
            "name": industry_name or "未知行业",
            "cycle_stage": _infer_cycle_stage(items),
            "summary": f"{industry_name}行业数据来自 akshare，景气判断基于公开信息。",
            "items": items,
            "metrics": [],
            "peers": peers,
        }

    # ------------------------------------------------------------------ #
    # 公司公告
    # ------------------------------------------------------------------ #
    async def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        limit = min(limit, _MAX_ANNOUNCEMENT_ROWS)
        # akshare 的公告接口名称在不同版本间不稳定，尝试多个候选
        for func_name in ("stock_zh_a_disclosure_report", "stock_notice_report"):
            try:
                df = await self._call(func_name, symbol=code)
                if df is not None and not df.empty:
                    return _announcements_from_df(df, code, limit)
            except Exception:
                continue
        return []

    # ------------------------------------------------------------------ #
    # 财经新闻
    # ------------------------------------------------------------------ #
    async def news(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        limit = min(limit, _MAX_NEWS_ROWS)
        # stock_news_em 需要股票名称而非代码，先查名称
        name = await self._lookup_name(code)
        if not name:
            return []
        try:
            df = await self._call("stock_news_em", symbol=name)
            if df is not None and not df.empty:
                return _news_from_df(df, limit)
        except Exception:
            pass
        return []

    async def _lookup_name(self, code: str) -> str:
        try:
            df = await self._call("stock_individual_info_em", symbol=code)
            if df is not None and not df.empty:
                info = _df_to_dict(df)
                return str(info.get("股票简称") or info.get("名称") or "")
        except Exception:
            pass
        return ""


# --------------------------------------------------------------------------- #
# 转换函数：把 akshare 的 DataFrame 行/列映射成项目期望的 dict 结构。
# 这些函数是纯函数，可以单测（不需要真实 akshare 调用）。
# --------------------------------------------------------------------------- #
def _df_to_dict(df: Any) -> dict[str, Any]:
    """把 akshare 返回的双列 DataFrame（item, value）转成 dict。"""
    if df is None or df.empty:
        return {}
    if len(df.columns) >= 2:
        key_col, val_col = df.columns[0], df.columns[1]
        return {str(row[key_col]): row[val_col] for _, row in df.iterrows()}
    return {}


def _extract_code(text: str) -> str:
    """从用户输入里提取 6 位 A 股代码。"""
    import re

    match = re.search(r"\b(6\d{5}|0\d{5}|3\d{5})\b", text)
    return match.group(1) if match else ""


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "--", "null"):
        return ""
    return s


def _format_amount(value: Any, unit: str = "亿元") -> str:
    """把数值格式化为带单位的展示字符串。"""
    s = _safe_str(value)
    if not s:
        return ""
    return f"{s} {unit}"


def _financial_items_from_abstract(df: Any, code: str) -> list[dict[str, Any]]:
    """从 stock_financial_abstract 的返回组装财务条目。"""
    items: list[dict[str, Any]] = []
    if df is None or df.empty:
        return items

    # akshare 的财务摘要接口返回的列名可能是中文，且不同版本格式不一
    # 这里做字段映射：把已知的列名映射到项目的语义
    col_map = {
        "营业总收入": "fin_revenue",
        "营业收入": "fin_revenue",
        "净利润": "fin_profit",
        "经营现金流": "fin_cashflow",
        "经营活动产生的现金流量净额": "fin_cashflow",
        "销售净利率": "fin_margin",
        "净利率": "fin_margin",
    }

    period = ""
    if "报告期" in df.columns:
        periods = df["报告期"].dropna()
        if not periods.empty:
            period = _safe_str(periods.iloc[0])

    for col in df.columns:
        key = col_map.get(str(col))
        if key is None:
            continue
        values = df[col].dropna()
        if values.empty:
            continue
        latest = values.iloc[0]
        items.append(
            {
                "key": key,
                "name": str(col),
                "value": _safe_str(latest),
                "period": period or "最新报告期",
                "title": f"{col!s} {_safe_str(latest)}",
                "snippet": f"{period} {col!s}为 {_safe_str(latest)}。数据来源：akshare。",
                "source": "akshare · 财务摘要",
                "url": f"https://akshare.akfamily.xyz/stock/{code}",
                "published_at": period[:10] if len(period) >= 10 else "",
                "tier": 2,
            }
        )
    return items


def _financial_items_from_indicators(df: Any, code: str) -> list[dict[str, Any]]:
    """从 stock_financial_analysis_indicator 的返回组装财务条目（fallback）。"""
    items: list[dict[str, Any]] = []
    if df is None or df.empty:
        return items

    target_metrics = ["净利率", "销售净利率", "毛利率", "营业利润率"]
    period = ""
    if "日期" in df.columns:
        dates = df["日期"].dropna()
        if not dates.empty:
            period = _safe_str(dates.iloc[0])

    for metric_name in target_metrics:
        if metric_name not in df.columns:
            continue
        values = df[metric_name].dropna()
        if values.empty:
            continue
        latest = values.iloc[0]
        items.append(
            {
                "key": f"fin_{metric_name}",
                "name": metric_name,
                "value": f"{_safe_str(latest)}%",
                "period": period or "最新报告期",
                "title": f"{metric_name} {_safe_str(latest)}%",
                "snippet": f"{period} {metric_name}为 {_safe_str(latest)}%。数据来源：akshare。",
                "source": "akshare · 财务指标",
                "url": f"https://akshare.akfamily.xyz/stock/{code}",
                "published_at": period[:10] if len(period) >= 10 else "",
                "tier": 2,
            }
        )
    return items


def _signals_from_indicators(df: Any) -> dict[str, Any]:
    """从财务指标提取分析师需要的 signals（revenue_growth / net_margin）。"""
    signals: dict[str, Any] = {}
    if df is None or df.empty:
        return signals

    for col in df.columns:
        col_str = str(col)
        values = df[col].dropna()
        if values.empty:
            continue
        latest = float(values.iloc[0])

        if "净利率" in col_str or "销售净利率" in col_str:
            signals["net_margin"] = latest / 100.0
        elif "营收增长" in col_str or "营业总收入增长" in col_str:
            signals["revenue_growth"] = latest / 100.0

    return signals


def _peers_from_board(df: Any, *, exclude_code: str, limit: int = 5) -> list[dict[str, Any]]:
    """从行业板块成分股提取同业对比数据。"""
    peers: list[dict[str, Any]] = []
    if df is None or df.empty:
        return peers

    name_col = "名称" if "名称" in df.columns else None
    code_col = "代码" if "代码" in df.columns else None
    if name_col is None or code_col is None:
        return peers

    for _, row in df.iterrows():
        code = _safe_str(row[code_col])
        if code == exclude_code or not code:
            continue
        name = _safe_str(row[name_col])
        if not name:
            continue
        # 尝试提取估值字段（不同 akshare 版本列名不同）
        pe = _safe_str(row.get("市盈率-动态") or row.get("市盈率") or "")
        peers.append({"name": name, "pe_ratio": pe, "revenue_growth": ""})
        if len(peers) >= limit:
            break
    return peers


def _infer_cycle_stage(items: list[dict[str, Any]]) -> str:
    """根据可用信息粗略判断行业周期阶段。"""
    # 目前没有足够的数据做精确判断，返回中性。
    # 后续可以接入行业产量数据来推断。
    return "中性"


def _announcements_from_df(df: Any, code: str, limit: int) -> list[dict[str, Any]]:
    """从公告 DataFrame 提取结构化公告列表。"""
    result: list[dict[str, Any]] = []
    if df is None or df.empty:
        return result

    # 候选列名（akshare 不同版本列名不统一）
    title_col = _first_match(df.columns, ["公告标题", "标题", "title"])
    date_col = _first_match(df.columns, ["公告日期", "日期", "date"])
    url_col = _first_match(df.columns, ["公告链接", "链接", "url"])
    type_col = _first_match(df.columns, ["公告类型", "类型"])

    if title_col is None:
        return result

    for _, row in df.head(limit).iterrows():
        title = _safe_str(row.get(title_col))
        if not title:
            continue
        published = _safe_str(row.get(date_col)) if date_col else ""
        url = _safe_str(row.get(url_col)) if url_col else f"https://akshare.akfamily.xyz/notice/{code}"
        result.append(
            {
                "key": f"ann_{len(result)}",
                "title": title,
                "snippet": title[:200],
                "source": f"akshare · 公告 ({_safe_str(row.get(type_col)) if type_col else ''})".strip(),
                "url": url,
                "published_at": published[:10] if len(published) >= 10 else published,
                "tier": 1,
            }
        )
    return result


def _news_from_df(df: Any, limit: int) -> list[dict[str, Any]]:
    """从新闻 DataFrame 提取结构化新闻列表。"""
    result: list[dict[str, Any]] = []
    if df is None or df.empty:
        return result

    title_col = _first_match(df.columns, ["新闻标题", "标题", "title"])
    content_col = _first_match(df.columns, ["新闻内容", "内容", "content"])
    date_col = _first_match(df.columns, ["发布日期", "日期", "date", "发布时间"])
    url_col = _first_match(df.columns, ["新闻链接", "链接", "url"])

    if title_col is None:
        return result

    for _, row in df.head(limit).iterrows():
        title = _safe_str(row.get(title_col))
        if not title:
            continue
        snippet = _safe_str(row.get(content_col)) if content_col else ""
        if not snippet:
            snippet = title
        snippet = snippet[:300]
        published = _safe_str(row.get(date_col)) if date_col else ""
        url = _safe_str(row.get(url_col)) if url_col else ""
        result.append(
            {
                "key": f"news_{len(result)}",
                "title": title,
                "snippet": snippet,
                "source": "akshare · 东方财富",
                "url": url,
                "published_at": published[:10] if len(published) >= 10 else published,
                "tier": 3,
                "sentiment": _estimate_sentiment(title),
            }
        )
    return result


def _first_match(columns: Any, candidates: list[str]) -> str | None:
    """在 DataFrame 列里找第一个匹配的候选列名。"""
    col_set = set(str(c) for c in columns)
    for candidate in candidates:
        if candidate in col_set:
            return candidate
    return None


def _estimate_sentiment(text: str) -> float:
    """极简情感估计：基于关键词的正负判断。

    没有用 NLP 模型，因为：① 没有标注集校准，模型也是黑盒；
    ② 新闻情感在分析师 prompt 里只是辅助信号；
    ③ 这个函数的存在价值在于"有"一个可解释的数值，而不是"精确"。
    """
    positive_words = ["增长", "提升", "改善", "利好", "突破", "创新高", "回暖", "超预期"]
    negative_words = ["下降", "下滑", "亏损", "利空", "风险", "违规", "警示", "暴跌", "退市"]
    score = 0.0
    for word in positive_words:
        if word in text:
            score += 0.2
    for word in negative_words:
        if word in text:
            score -= 0.2
    return max(-1.0, min(1.0, score))
