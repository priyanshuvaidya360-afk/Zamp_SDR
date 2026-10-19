// Shapes returned by the backend API. Mirrors backend/app/ingest.py, routes/batches.py and
// models.py; keep them in step when the API changes.

export type RowStatus = "valid" | "invalid" | "skipped";

export type RunStatus =
  | "running"
  | "needs_input"
  | "low_signal"
  | "ready_for_review"
  | "blocked"
  | "suppressed"
  | "held"
  | "approved"
  | "rejected"
  | "failed"
  | "invalid"
  | "skipped";

export type BatchStatus = "validating" | "running" | "done";

export type Field =
  | "first_name"
  | "last_name"
  | "full_name"
  | "company_domain"
  | "linkedin_url"
  | "company"
  | "title"
  | "email"
  | "notes"
  | "public_bio";

export type Mapping = Record<Field, string | null>;

export interface PreviewRow {
  row_number: number;
  status: RowStatus;
  reason: string | null;
  warnings: string[];
  duplicate_of: number | null;
  name_split: boolean;
  data: Partial<Record<Field, string | null>>;
  raw: Record<string, string>;
}

export interface Preview {
  mapping: Mapping;
  headers: string[];
  unmapped_headers: string[];
  counts: { total: number; valid: number; invalid: number; duplicate: number };
  rows: PreviewRow[];
}

export interface BatchPreview {
  batch_id: number;
  filename: string;
  status: BatchStatus;
  preview: Preview;
}

export interface StartResponse {
  batch_id: number;
  status: BatchStatus;
  run_ids: number[];
}

export interface BatchProgress {
  done: number;
  total: number;
  counts: Partial<Record<RunStatus, number>>;
}

export interface RunSummary {
  id: number;
  batch_id: number;
  row_number: number;
  name: string;
  company: string | null;
  title: string | null;
  account_key: string | null;
  status: RunStatus;
  final_reason: string | null;
  confidence: number | null;
  cost_credits: number;
  current_stage: string | null;
  current_state: StageState | null;
  hook_type: string | null;
  send_day: number | null;
  account_plan: AccountPlan | null;
}

export interface AccountPlan {
  account_key: string;
  company: string | null;
  assignments: {
    run_id: number;
    name: string;
    title: string | null;
    persona: string | null;
    send_day: number | null;
    opener_type: string | null;
    opener_summary: string | null;
    context_summary: string | null;
    angle: string;
    solo_hook_summary: string | null;
  }[];
  checks: { name: string; passed: boolean; detail: string }[];
  counterfactual: string;
  fallback: boolean;
  attempts: number;
}

export interface BatchSimilarity {
  drafts?: number;
  similar_pairs?: { run_ids: number[]; names: string[]; similarity: number }[];
  source_cap?: number;
  rebalanced?: { run_id: number; excluded_source: string }[];
  diversity?: number | null;
}

export interface StageInfo {
  name: string;
  label: string;
}

export interface BatchDetail {
  batch: {
    id: number;
    filename: string;
    status: BatchStatus;
    row_count: number;
    uploaded_at: string;
    diversity_score: number | null;
    similarity: BatchSimilarity;
  };
  progress: BatchProgress;
  stages: StageInfo[];
  runs: RunSummary[];
  not_run: { row_number: number; status: RowStatus; reason: string | null; name: string }[];
}

export interface SellerProfile {
  company: string;
  rep: { name: string; title: string };
  offer: string;
  problem_solved: string;
  icp: { buyer_titles: string[]; company_size: string; industries: string[]; triggers: string[] };
  tone: { voice: string; max_words: number; banned_phrases: string[] };
}

// --- Live events (SSE) and the run view -----------------------------------------------------

export type StageState = "started" | "done" | "failed" | "skipped";

export interface StageEvent {
  type: "stage";
  id: number;
  run_id: number;
  batch_id: number | null;
  stage: string;
  state: StageState;
  message: string;
  payload: Record<string, unknown> & { output?: Record<string, unknown>; tokens?: number; credits?: number; stopped?: string };
  duration_ms: number | null;
  ts: string;
}

export interface RunStatusEvent {
  type: "run_status";
  run_id: number;
  batch_id: number;
  status: RunStatus;
  final_reason: string | null;
  confidence: number | null;
  cost_credits: number;
}

export interface BatchProgressEvent extends BatchProgress {
  type: "batch_progress";
  batch_id: number;
}

export type LiveEvent =
  | StageEvent
  | RunStatusEvent
  | BatchProgressEvent
  | { type: "batch_status"; batch_id: number; status: BatchStatus }
  | { type: "end"; run_id?: number; batch_id?: number; status?: RunStatus };

export interface Run {
  id: number;
  batch_id: number;
  first_name: string;
  last_name: string;
  full_name: string | null;
  company: string | null;
  title: string | null;
  domain: string | null;
  linkedin_url: string | null;
  status: RunStatus;
  final_reason: string | null;
  confidence: number | null;
  cost_credits: number;
  account_key: string | null;
  message_type: "cold" | "follow_up";
}

export interface ScoreAdjustment {
  name: string;
  delta: number;
  reason: string;
}

export interface SignalRecord {
  id: number;
  run_id: number;
  type: string;
  about: "person" | "company";
  summary: string;
  evidence: string;
  source_url: string;
  published_at: string | null;
  sensitive: boolean;
  speaker: string | null;
  is_reshare: boolean;
  time_sensitive: boolean;
  coverage: number;
  ghost_suspect: boolean;
  note: string | null;
  cluster_urls: string[];
  scores: {
    relevance?: number;
    specificity?: number;
    recency?: number;
    verifiability?: number;
    days_old?: number;
    score?: number;
    reasoning?: string;
    adjustments?: ScoreAdjustment[];
    weights?: Record<string, number>;
  };
  selected: boolean;
  rejected_reason: string | null;
}

export interface CriticCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface DraftRecord {
  id: number;
  run_id: number;
  version: number;
  subject: string;
  body: string;
  hook_signal_id: number | null;
  claims: { sentence: string; signal_id: number }[];
  critic_report: {
    mode?: "hooked" | "role_based" | "outline";
    banner?: string | null;
    checks?: CriticCheck[];
    problems?: string[];
    genericness?: { genericness: number; reason: string } | null;
    passed?: boolean;
    warnings?: string[];
    final?: boolean;
    confidence?: number;
    confidence_formula?: string;
  };
  created_at: string;
}

export interface RunDetail {
  run: Run;
  stages: StageInfo[];
  events: StageEvent[];
  signals: SignalRecord[];
  drafts: DraftRecord[];
}

export interface Candidate {
  name: string;
  title: string | null;
  company: string | null;
  company_domain: string | null;
  location: string | null;
  country: string | null;
  evidence_url: string;
  evidence: string;
  confidence: number;
}
