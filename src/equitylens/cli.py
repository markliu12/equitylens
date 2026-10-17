"""命令行入口：不做任何业务逻辑，只负责参数解析与结果输出。

保留 CLI 有两个实际用途：
1. CI / 评测跑批量任务时不需要起 HTTP 服务；
2. 出问题时可以单步复现，不用在浏览器里点。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from equitylens.config import Settings, get_settings
from equitylens.report.renderer import to_markdown
from equitylens.runtime import build_runtime


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="equitylens", description="EquityLens 研镜 — 多智能体行业研究工作台"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="执行一次研究")
    run.add_argument("question", help="研究问题，例如『分析贵州茅台最近一期经营情况』")
    run.add_argument("--as-of", default="", help="基准日 YYYY-MM-DD，默认取数据源默认值")
    run.add_argument("--offline", action="store_true", help="强制离线模式（fixture + 确定性模型）")
    run.add_argument("--out", default="", help="把 Markdown 报告写入指定文件")
    run.add_argument("--json", action="store_true", help="输出结构化结果 JSON")

    doctor = sub.add_parser("doctor", help="打印当前配置与图结构")
    doctor.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings: Settings = get_settings()
    if args.offline:
        settings = settings.model_copy(update={"app_mode": "offline"})
    runtime = build_runtime(settings)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    as_of = args.as_of or "2026-09-30"
    try:
        outcome = await runtime.runner.run(run_id, question=args.question, as_of=as_of)
        report = outcome.state.get("final_report") or {}
        audit = outcome.state.get("citation_audit") or {}
        if args.out and report:
            Path(args.out).write_text(to_markdown(report, audit), encoding="utf-8")
            print(f"报告已写入 {args.out}", file=sys.stderr)

        if args.json:
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": str(outcome.status),
                        "failures": outcome.failures,
                        "usage": outcome.usage,
                        "citation_audit": audit,
                        "node_statuses": outcome.node_statuses,
                        "report": report,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"run_id   : {run_id}")
            print(f"状态      : {outcome.status}")
            coverage = audit.get("coverage", 0)
            quote_hit_rate = audit.get("quote_hit_rate", 1)
            print(f"引用覆盖率: {coverage:.1%}  引用命中率: {quote_hit_rate:.1%}")
            print(f"成本      : ¥{outcome.usage.get('cost_cny', 0):.4f}")
            print("-" * 60)
            print(to_markdown(report, audit) if report else "（未生成报告）")
        return 0 if report else 1
    finally:
        await runtime.aclose()


async def _doctor(args: argparse.Namespace) -> int:
    settings = get_settings()
    runtime = build_runtime(settings)
    payload = {
        "settings": settings.redacted(),
        "graph_version": runtime.graph_version,
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "label": node.label,
                "deps": list(node.deps),
                "writes": list(node.writes),
            }
            for node in runtime.graph.nodes.values()
        ],
        "tools": runtime.tools.manifest(),
        "schema": {
            name: channel.description or "—"
            for name, channel in runtime.graph.schema.channels.items()
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload["settings"], ensure_ascii=False, indent=2))
        print(f"\n图版本 {runtime.graph_version}，共 {len(payload['nodes'])} 个节点：")
        for node in payload["nodes"]:
            print(f"  - {node['id']:<20} {node['kind']:<10} {node['label']}")
    await runtime.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "run":
        return asyncio.run(_run(args))
    return asyncio.run(_doctor(args))


if __name__ == "__main__":
    raise SystemExit(main())
