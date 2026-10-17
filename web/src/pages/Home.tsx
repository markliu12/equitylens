import { useEffect, useState } from "react";

import { createRun, getExamples, getModels, listRuns } from "../api/client";
import type { RunRecord } from "../api/types";
import { StatusPill } from "../components/StatusPill";

interface Example {
  question: string;
  as_of: string;
  tag: string;
}

export function Home({ navigate }: { navigate: (path: string) => void }) {
  const [question, setQuestion] = useState("分析贵州茅台最近一期经营情况与行业景气度");
  const [asOf, setAsOf] = useState("2026-09-30");
  const [examples, setExamples] = useState<Example[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [models, setModels] = useState<{ models: string[]; offline: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getExamples().then((data) => setExamples(data.examples)).catch(() => undefined);
    getModels().then(setModels).catch(() => undefined);
    listRuns().then((data) => setRuns(data.runs)).catch(() => undefined);
  }, []);

  const submit = async () => {
    if (question.trim().length < 2 || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await createRun(question.trim(), asOf);
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wrap">
      <div className="card hero">
        <span className="eyebrow">手写 DAG 编排引擎 · 9 个 Agent · 强制引用溯源</span>
        <h1>
          把一次行业研究，拆成 <span>13 个可观测、可恢复</span> 的节点
        </h1>
        <p className="lede">
          EquityLens 不使用 LangChain / LangGraph。调度、checkpoint、降级、预算全部自己实现；
          每条结论都必须挂到可核验的原文上，跑完能给出引用覆盖率与命中率。
        </p>

        <div className="stack">
          <div className="field">
            <label>研究问题</label>
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：分析某公司最近一期经营情况与所处行业景气度"
            />
          </div>
          <div className="row" style={{ gap: 12 }}>
            <div className="field" style={{ width: 190 }}>
              <label>基准日（只用该日期之前的信息）</label>
              <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
            </div>
            <div style={{ flex: 1 }} />
            <button className="btn primary" onClick={submit} disabled={busy}>
              {busy ? "提交中…" : "开始研究"}
            </button>
          </div>
          {examples.length > 0 && (
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>
                试试这些
              </div>
              <div className="chips">
                {examples.map((item) => (
                  <button
                    type="button"
                    className="chip"
                    key={item.question}
                    onClick={() => {
                      setQuestion(item.question);
                      setAsOf(item.as_of);
                    }}
                  >
                    {item.tag} · {item.question}
                  </button>
                ))}
              </div>
            </div>
          )}
          {error && <div className="notice">{error}</div>}
        </div>

        <div className="hero-stats">
          <div>
            <strong>13</strong> 编排节点
          </div>
          <div>
            <strong>3</strong> 级证据校验
          </div>
          <div>
            <strong>可恢复</strong> 节点级 checkpoint
          </div>
          <div>
            <strong>{models?.offline ? "离线 fixture" : models?.models?.[0] ?? "—"}</strong> 当前模型
          </div>
        </div>
      </div>

      <div className="card pad" style={{ marginTop: 18 }}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>最近运行</h2>
          <span className="small muted">点击查看时间线、辩论与报告</span>
        </div>
        {runs.length === 0 ? (
          <div className="empty">还没有运行记录。上面输入一个问题试试。</div>
        ) : (
          <div className="timeline">
            {runs.map((run) => (
              <div
                className="tl-item"
                key={run.run_id}
                style={{ cursor: "pointer" }}
                onClick={() => navigate(`/runs/${run.run_id}`)}
              >
                <div className="tl-mark">›</div>
                <div>
                  <div className="tl-title">{run.question}</div>
                  <div className="tl-sub mono">
                    {run.run_id} · 基准日 {run.as_of} · ¥
                    {(run.usage?.cost_cny ?? 0).toFixed(3)}
                  </div>
                </div>
                <StatusPill status={run.status} />
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="small muted" style={{ marginTop: 20 }}>
        本系统只做信息整理与研究学习，不输出任何买卖建议或价格预测。演示数据为示例数据集。
      </p>
    </div>
  );
}
