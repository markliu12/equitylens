export type NodeStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "SKIPPED"
  | "CANCELLED";

export interface Claim {
  text: string;
  evidence_ids: string[];
  strength: "事实" | "观点";
  quote?: string | null;
}

export interface Metric {
  name: string;
  value: string;
  period?: string | null;
  evidence_ids?: string[];
}

export interface Evidence {
  id: string;
  source_name: string;
  tier: number;
  title: string;
  url: string;
  published_at?: string | null;
  snippet: string;
  kind: string;
}

export interface Section {
  title: string;
  narrative: string;
  claims: Claim[];
}

export interface Report {
  title: string;
  entity_name: string;
  entity_code: string;
  industry: string;
  as_of: string;
  summary: string;
  sections: Section[];
  key_metrics: Metric[];
  risks: string[];
  open_questions: string[];
  disclaimer: string;
  evidence: Evidence[];
  meta: Record<string, unknown>;
}

export interface Audit {
  total_claims: number;
  grounded_claims: number;
  coverage: number;
  quotes_checked: number;
  quotes_hit: number;
  quote_hit_rate: number;
  issues: { code: string; claim: string; detail: string }[];
  passed: boolean;
}

export interface Usage {
  total_tokens?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_cny?: number;
  calls?: number;
  limits?: { max_cost_cny: number; max_tokens: number; max_llm_calls: number };
}

export interface RunRecord {
  run_id: string;
  question: string;
  as_of: string;
  status: string;
  usage: Usage;
  created_at: number;
  error?: string | null;
}

export interface AnalystReport {
  role: string;
  stance: string;
  confidence: number;
  claims: Claim[];
  gaps: string[];
}

export interface RoundScore {
  round: number;
  bull: { total?: number; logic: number; evidence: number; novelty: number; rebuttal: number };
  bear: { total?: number; logic: number; evidence: number; novelty: number; rebuttal: number };
  new_info_ratio: number;
  leader: string;
  note?: string;
  unresolved?: string[];
}

export interface Argument {
  side: string;
  thesis: string;
  points: Claim[];
  rebuttals: Claim[];
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  deps: string[];
}

export interface RunDetail {
  run: RunRecord;
  node_statuses: Record<string, NodeStatus>;
  executions: {
    node_id: string;
    attempt: number;
    status: string;
    usage: Usage;
    degraded: boolean;
    started_at: number;
    finished_at: number;
    error?: string | null;
  }[];
  report: Partial<Report>;
  audit: Partial<Audit>;
  verdict: { summary?: string; confidence?: number; open_questions?: string[] };
  debate: RoundScore[];
  arguments: { bull: Partial<Argument>; bear: Partial<Argument> };
  analyst_reports: AnalystReport[];
  evidence_summary: { count?: number; by_tier?: Record<string, number> };
  risk_review: { passed?: boolean; violations?: { type: string; quote: string }[] };
  is_running: boolean;
  graph: GraphNode[];
}

export interface StreamEvent {
  seq: number;
  type: string;
  node_id?: string | null;
  [key: string]: unknown;
}
