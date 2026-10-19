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
  current_state: string | null;
}

export interface BatchDetail {
  batch: { id: number; filename: string; status: BatchStatus; row_count: number; uploaded_at: string };
  progress: BatchProgress;
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
