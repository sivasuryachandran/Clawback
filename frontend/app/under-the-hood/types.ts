// Shapes returned by GET /debug/overview (backend/debug/overview.py). Teammate C.
export interface PanelMeta { available?: boolean; error?: string; took_ms?: number }

export interface RuleResult { rule: string | null; pass: boolean | null; why: string | null; values: Record<string, unknown> }

export interface Decision {
  audit_id: number; ts: string; decision: string; action_type: string | null; proposal_id: string | null;
  opportunity_id: number | null; item: string | null; store: string | null; pact_id: string | null;
  pact_version: number | null; proposal_hash: string | null; short_hash: string | null;
  failed_rule: string | null; rules: RuleResult[]; action_result: unknown;
}

export interface PactVersion {
  version: number; created_at: string; status_column: string | null; signature_ok: boolean | null;
  hash: string | null; revoked: boolean; pact_text: string; raw_text: string; is_latest: boolean;
}
export interface PactOwner {
  id: string; name: string | null; persona_status: string | null; persona_inquiry_id: string | null;
  verified_at: string | null; reverified_at: string | null;
}
export interface PactRow { pact_id: string; user_id: string; latest_version: number; versions: PactVersion[]; owner: PactOwner }
export interface Revocation { pact_id: string; version: number; revoked_at: string; reason: string | null; signature: string | null; signature_ok: boolean | null }

export type Row = Record<string, string | number | boolean | null | undefined>;

export interface Candidate {
  listing_id?: number; id?: number; store?: string; sku?: string; variant?: string; title?: string;
  similarity?: number; rerank_score?: number; exact?: boolean;
}

export interface SavedQuery { key: string; label: string; sql: string; needs_item: boolean }
export interface QueryResult { key: string; label: string; sql: string; params: unknown[]; rows: Row[]; took_ms?: number; error?: string; sample?: boolean }

export interface AnalyticsEvent { event: string; at: string; props: Record<string, unknown> }

export interface Overview {
  generated_at: string;
  sample: boolean;
  sample_note: string | null;
  routers: Record<string, string>;
  gate_trace: PanelMeta & { decisions?: Decision[] };
  pact: PanelMeta & { pacts?: PactRow[]; revocations?: Revocation[]; verifier?: string; verifier_found?: boolean };
  approvals_once: PanelMeta & { approvals?: Row[]; executed?: Row[]; note?: string };
  policy: PanelMeta & { checks?: Row[] };
  matching: PanelMeta & { items?: Row[]; line_item_id?: number | null; candidates?: Candidate[]; sql?: string; sql_source?: string; note?: string };
  live_queries: PanelMeta & { queries?: SavedQuery[] };
  ingest: PanelMeta & { emails?: Row[]; line_items?: Row[]; parse_events?: AnalyticsEvent[]; avg_cost_per_receipt_usd?: number | null; note?: string };
  safety: PanelMeta & {
    demo_mode?: boolean; demo_mode_raw?: string; allowed_recipients?: string[]; support_inbox?: string | null;
    messages?: Row[]; messages_error?: string | null;
  };
  analytics: PanelMeta & {
    status?: { enabled: boolean; queued: number; sent: number; failed: number; last_error: string | null; region: string };
    dashboard_url?: string | null; today_counts?: Record<string, number>; recent?: AnalyticsEvent[];
  };
  tests: PanelMeta & { ran?: boolean; passed?: number; failed?: number; skipped?: number; duration_s?: number; finished_at?: string; note?: string };
}
