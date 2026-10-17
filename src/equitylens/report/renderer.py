"""报告渲染：Markdown / HTML 两种形态，引用编号可回溯。

渲染层刻意不做任何"内容加工"：它只负责把结构化报告排版，以及把 evidence_id 映射成
人类可读的编号 [1][2]。任何改写都发生在 editor 节点里，这样"渲染"和"生成"的责任是
分开的——出问题的时候能立刻判断是内容错还是排版错。
"""

from __future__ import annotations

import html
from typing import Any

from equitylens.tools.evidence import TIER_LABELS


def _evidence_order(report: dict[str, Any]) -> dict[str, int]:
    order: dict[str, int] = {}
    for index, item in enumerate(report.get("evidence") or [], start=1):
        evidence_id = str(item.get("id") or "")
        if evidence_id and evidence_id not in order:
            order[evidence_id] = index
    return order


def _refs(evidence_ids: list[Any], order: dict[str, int]) -> str:
    numbers = [order[str(i)] for i in evidence_ids if str(i) in order]
    if not numbers:
        return ""
    return " " + "".join(f"[{n}]" for n in numbers)


def to_markdown(report: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
    order = _evidence_order(report)
    lines: list[str] = [f"# {report.get('title', '研究报告')}", ""]
    meta_bits = [
        f"**标的**：{report.get('entity_name', '')}"
        + (f"（{report.get('entity_code')}）" if report.get("entity_code") else ""),
        f"**行业**：{report.get('industry') or '—'}",
        f"**基准日**：{report.get('as_of', '')}",
    ]
    if audit:
        meta_bits.append(
            f"**引用覆盖率**：{audit.get('coverage', 0):.0%}"
            f"（{audit.get('grounded_claims', 0)}/{audit.get('total_claims', 0)} 条结论有据可查）"
        )
    lines.extend(["  \n".join(meta_bits), ""])

    summary = report.get("summary")
    if summary:
        lines.extend(["## 摘要", "", str(summary), ""])

    for section in report.get("sections") or []:
        lines.append(f"## {section.get('title', '')}")
        lines.append("")
        narrative = section.get("narrative")
        if narrative:
            lines.extend([str(narrative), ""])
        for claim in section.get("claims") or []:
            refs = _refs(claim.get("evidence_ids") or [], order)
            tag = "" if str(claim.get("strength")) == "事实" else "（观点）"
            lines.append(f"- {claim.get('text', '')}{tag}{refs}")
        lines.append("")

    metrics = report.get("key_metrics") or []
    if metrics:
        lines.extend(["## 关键指标", "", "| 指标 | 数值 | 期间 |", "| --- | --- | --- |"])
        for metric in metrics:
            lines.append(
                f"| {metric.get('name', '')} | {metric.get('value', '')} | {metric.get('period') or '—'} |"
            )
        lines.append("")

    for title, key in (("风险提示", "risks"), ("待核实问题", "open_questions")):
        items = report.get(key) or []
        if items:
            lines.extend([f"## {title}", ""])
            lines.extend(f"- {item}" for item in items)
            lines.append("")

    evidence = report.get("evidence") or []
    if evidence:
        lines.extend(["## 参考来源", ""])
        for index, item in enumerate(evidence, start=1):
            tier = TIER_LABELS.get(int(item.get("tier") or 4), "其他")
            published = f"，{item.get('published_at')}" if item.get("published_at") else ""
            lines.append(
                f"{index}. 【{tier}】{item.get('title') or '（无标题）'} — "
                f"{item.get('source_name') or '未知来源'}{published}  \n   {item.get('url') or ''}"
            )
        lines.append("")

    lines.extend(["---", "", f"> {report.get('disclaimer', '')}", ""])
    return "\n".join(lines)


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f7f9; color: #1c1f23;
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.75; }
.wrap { max-width: 860px; margin: 0 auto; padding: 48px 24px 80px; }
.card { background: #fff; border: 1px solid #e6e8eb; border-radius: 14px; padding: 40px 44px; }
h1 { font-size: 26px; margin: 0 0 8px; letter-spacing: -0.2px; }
h2 { font-size: 18px; margin: 32px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #eef0f2; }
.meta { color: #5b6472; font-size: 13px; display: flex; flex-wrap: wrap; gap: 8px 20px;
  margin-bottom: 28px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px;
  background: #eef4ff; color: #1f5fd0; font-size: 12px; }
.summary { background: #f8fafc; border-left: 3px solid #2f6fe4; padding: 16px 18px;
  border-radius: 8px; margin: 16px 0 8px; }
ul.claims { margin: 8px 0 0; padding-left: 20px; }
ul.claims li { margin: 6px 0; }
.cite { color: #1f5fd0; font-size: 12px; vertical-align: super; text-decoration: none; }
.tag { font-size: 12px; color: #8a6b1f; background: #fdf5e2; padding: 1px 6px;
  border-radius: 4px; margin-left: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; margin: 8px 0 4px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #eef0f2; }
th { color: #5b6472; font-weight: 600; background: #fafbfc; }
.ev { font-size: 13px; color: #3d4553; margin: 10px 0; }
.ev .tier { color: #1f5fd0; }
.ev a { color: #5b6472; word-break: break-all; }
.disclaimer { margin-top: 30px; padding: 16px 18px; background: #fffdf5;
  border: 1px solid #f0e4c3; border-radius: 8px; font-size: 13px; color: #6b5a2e; }
""".strip()


def to_html(report: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
    order = _evidence_order(report)
    evidence_by_id = {str(item.get("id")): item for item in report.get("evidence") or []}

    def claim_html(claim: dict[str, Any]) -> str:
        refs = []
        for evidence_id in claim.get("evidence_ids") or []:
            number = order.get(str(evidence_id))
            if not number:
                continue
            item = evidence_by_id.get(str(evidence_id), {})
            tooltip = html.escape(
                f"【{TIER_LABELS.get(int(item.get('tier') or 4), '其他')}】"
                f"{item.get('title', '')}｜{item.get('source_name', '')}"
            )
            refs.append(f'<a class="cite" href="#ev-{number}" title="{tooltip}">[{number}]</a>')
        tag = "" if str(claim.get("strength")) == "事实" else '<span class="tag">观点</span>'
        return f"<li>{html.escape(str(claim.get('text') or ''))}{tag}{''.join(refs)}</li>"

    sections_html: list[str] = []
    for section in report.get("sections") or []:
        claims = "".join(claim_html(claim) for claim in section.get("claims") or [])
        narrative = (
            f"<p>{html.escape(str(section.get('narrative')))}</p>"
            if section.get("narrative")
            else ""
        )
        sections_html.append(
            f"<h2>{html.escape(str(section.get('title') or ''))}</h2>{narrative}"
            + (f'<ul class="claims">{claims}</ul>' if claims else "")
        )

    metrics_html = ""
    metrics = report.get("key_metrics") or []
    if metrics:
        rows = "".join(
            f"<tr><td>{html.escape(str(m.get('name') or ''))}</td>"
            f"<td>{html.escape(str(m.get('value') or ''))}</td>"
            f"<td>{html.escape(str(m.get('period') or '—'))}</td></tr>"
            for m in metrics
        )
        metrics_html = (
            "<h2>关键指标</h2><table><thead><tr><th>指标</th><th>数值</th><th>期间</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    def list_html(title: str, key: str) -> str:
        items = report.get(key) or []
        if not items:
            return ""
        lis = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
        return f'<h2>{title}</h2><ul class="claims">{lis}</ul>'

    evidence_html = ""
    if report.get("evidence"):
        blocks = []
        for index, item in enumerate(report["evidence"], start=1):
            tier = TIER_LABELS.get(int(item.get("tier") or 4), "其他")
            blocks.append(
                f'<div class="ev" id="ev-{index}"><b>[{index}]</b> '
                f'<span class="tier">【{tier}】</span>'
                f"{html.escape(str(item.get('title') or '（无标题）'))} · "
                f"{html.escape(str(item.get('source_name') or ''))}"
                f"{' · ' + html.escape(str(item.get('published_at'))) if item.get('published_at') else ''}"
                f'<br><a href="{html.escape(str(item.get("url") or "#"))}" target="_blank" rel="noreferrer">'
                f"{html.escape(str(item.get('url') or ''))}</a></div>"
            )
        evidence_html = f"<h2>参考来源</h2>{''.join(blocks)}"

    audit_badge = ""
    if audit:
        audit_badge = (
            f'<span class="badge">引用覆盖率 {audit.get("coverage", 0):.0%}</span>'
            f'<span class="badge">引用命中率 {audit.get("quote_hit_rate", 1):.0%}</span>'
        )

    code = html.escape(str(report.get("entity_code") or ""))
    entity = html.escape(str(report.get("entity_name") or ""))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(report.get("title") or "研究报告"))}</title>
<style>{_CSS}</style></head>
<body><div class="wrap"><div class="card">
<h1>{html.escape(str(report.get("title") or "研究报告"))}</h1>
<div class="meta">
  <span>标的：{entity}{f"（{code}）" if code else ""}</span>
  <span>行业：{html.escape(str(report.get("industry") or "—"))}</span>
  <span>基准日：{html.escape(str(report.get("as_of") or ""))}</span>
  {audit_badge}
</div>
<div class="summary">{html.escape(str(report.get("summary") or ""))}</div>
{"".join(sections_html)}
{metrics_html}
{list_html("风险提示", "risks")}
{list_html("待核实问题", "open_questions")}
{evidence_html}
<div class="disclaimer">{html.escape(str(report.get("disclaimer") or ""))}</div>
</div></div></body></html>"""
