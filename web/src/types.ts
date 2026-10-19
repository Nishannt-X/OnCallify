export type Status =
  | "open"
  | "diagnosing"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "resolved"
  | "self_resolved"
  | "escalated"
  | "rejected";

export interface TimelineEntry {
  ts: string;
  kind: string;
  detail: string;
}

export interface Diagnosis {
  cause: string;
  confidence: number;
  rationale: string;
  evidence_refs: string[];
  suspect_change: string | null;
  variant: string;
  latency_ms: number;
  tokens: number;
  notes: string[];
}

export interface Change {
  id: string;
  ts: string;
  source: string;
  author: string;
  summary: string;
  keys: Record<string, (string | null)[]>;
}

export interface LogTemplate {
  template: string;
  count: number;
  level: string;
  error_kind: string | null;
  exemplar: string;
}

export interface Evidence {
  window_start: string;
  window_end: string;
  logs: {
    total_lines: number;
    by_error_kind: Record<string, number>;
    recent_error_kinds: Record<string, number>;
    templates: LogTemplate[];
  };
  metrics: Record<string, [number, number][]>;
  changes: Change[];
}

export interface Proposal {
  id: string;
  action: string;
  params: Record<string, unknown>;
  params_hash: string;
  fingerprint: string;
  blast_radius: string;
  rationale: string;
  dry_run: { ok: boolean; code: string; detail: string };
  created_at: string;
  expires_at: string;
  status: string;
}

export interface Approval {
  id: string;
  action: string;
  params: Record<string, unknown>;
  approver: string;
  source: "human" | "contract";
  granted_at: string;
  used_at: string | null;
  executed_at: string | null;
  evidence_digest: string;
  contract_id: string | null;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

export interface IncidentSummary {
  id: string;
  service: string;
  status: Status;
  title: string;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  primary_rule: string;
  variant: string | null;
  diagnosis: Diagnosis | null;
}

export interface Incident extends IncidentSummary {
  evidence: Evidence | null;
  timeline: TimelineEntry[];
  proposal: Proposal | null;
  approvals: Approval[];
  verify: { ok: boolean; attempts: number; checks: Check[] } | null;
  postmortem: string | null;
}

export interface Contract {
  id: string;
  rule: string;
  cause: string;
  action: string;
  params: Record<string, unknown>;
  max_uses: number;
  uses: number;
  min_confidence: number;
  granted_by: string;
  granted_at: string;
  expires_at: string;
  status: string;
}

export interface Safety {
  kill_switch: boolean;
  allowlist: { id: string; description: string; params: string[]; contractable: boolean; disruptive: boolean }[];
  variants: string[];
  rules: string[];
  audit: { ts: string; actor: string; kind: string; incident_id: string | null; detail: Record<string, unknown> }[];
}

export interface MetricComparison {
  metric: string;
  baseline: number;
  candidate: number;
  diff: number;
  ci_low: number;
  ci_high: number;
  p_value: number;
  p_holm: number;
  test: string;
}

export interface VariantSummary {
  variant: string;
  n_scenarios: number;
  n_runs: number;
  cause_correct: number;
  action_correct: number;
  unsafe: number;
  missed: number;
  latency_ms_p50: number;
  cause_correct_by_family: Record<string, number>;
}

export interface Report {
  corpus_note: string;
  summaries: VariantSummary[];
  comparisons: {
    baseline: string;
    candidate: string;
    n_scenarios: number;
    metrics: MetricComparison[];
    power_note: { text: string };
  }[];
  validation: Record<string, number> | null;
}
