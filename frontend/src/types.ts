// Shapes of the internal API (docs/API_SPECIFICATION.md). Only what the UI reads.

export type Role = "submitter" | "viewer" | "reviewer" | "approver" | "admin";
export const ROLES: Role[] = ["submitter", "viewer", "reviewer", "approver", "admin"];

export interface SessionInfo {
  user: string;
  role: Role;
  csrf_token: string | null;
  expires_at?: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  document_id: string;
  kind: string;
  status: JobStatus;
  params: Record<string, unknown>;
  stages: string[];
  completed_stages: string[];
  current_stage: string | null;
  attempts: number;
  max_attempts: number;
  cancel_requested: boolean;
  error_code: string | null;
  error_message: string | null;
  submitted_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface JobEvent {
  ts: string;
  event: string;
  stage: string | null;
  attempt: number;
  detail: Record<string, unknown>;
}

export interface JobSubmitted {
  job: Job;
  duplicate: boolean;
}

export interface DocumentSummary {
  id: string;
  status: "received" | "ready" | "rejected";
  filename: string | null;
  sha256: string;
  size_bytes: number;
  format: string | null;
  page_count: number | null;
  submitted_by: string;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  latest_job: Job | null;
}

export interface PageManifest {
  page_number: number;
  source: string;
  needs_ocr: boolean;
  image: { width_px: number; height_px: number } | null;
  quality: { flags: string[] } | null;
}

export interface DocumentManifest {
  document_id: string;
  original_filename: string | null;
  page_count: number;
  pages: PageManifest[];
}

export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface OcrLine {
  text: string;
  confidence: number | null;
  bbox: BoundingBox;
}

export interface OcrPage {
  page_number: number;
  status: string;
  engine: string | null;
  lines: OcrLine[];
  text: string;
  mean_confidence: number | null;
  review?: { needs_review: boolean; reasons: string[] } | null;
}

export interface OcrRun {
  requested_engine: string;
  languages: string[];
  pages: OcrPage[];
}

export type Value = string | number | boolean | null;

export interface Evidence {
  page_number: number;
  text: string;
  confidence: number | null;
}

export interface FieldResult {
  name: string;
  type: string;
  required: boolean;
  status: "extracted" | "missing" | "ambiguous" | "conflict" | "invalid";
  value: Value;
  raw: string | null;
  confidence: number | null;
  evidence: Evidence[];
  candidates: { value: Value; raw: string }[];
  errors: string[];
  notes: string[];
  sensitive: boolean;
}

export interface CellValue {
  status: string;
  value: Value;
  raw: string;
  errors: string[];
}

export interface TableResult {
  name: string;
  status: string;
  columns: string[];
  records: { index: number; page_number: number; values: Record<string, CellValue>; errors: string[] }[];
  issues: string[];
}

export interface CheckResult {
  id: string;
  kind: string;
  status: "passed" | "failed" | "not_applicable";
  message: string;
  fields: string[];
}

export interface ExtractionResult {
  document_id: string;
  layout_run: string;
  schema_id: string | null;
  status: "complete" | "needs_review" | "unclassified";
  classification: { status: string; document_type: string | null; score: number };
  fields: FieldResult[];
  tables: TableResult[];
  validation: { valid: boolean; issues: { code: string; location: (string | number)[]; message: string }[] };
  checks: CheckResult[];
  review: { needs_review: boolean; reasons: string[]; details: string[] };
  source_notes: string[];
  created_at: string;
}

export interface ExtractionSummary {
  document_id: string;
  layout_run: string;
  schema_id: string | null;
  status: string;
  review_status: string | null;
  filename: string | null;
  submitted_by: string;
  created_at: string;
  preview: Record<string, Value>;
}

export interface SchemaSummary {
  id: string;
  title: string;
}

// ------------------------------------------------------------ review

export interface FieldDecision {
  field: string;
  required: boolean;
  status: string;
  verified: boolean;
  reasons: string[];
  details: string[];
  confidence: number | null;
  threshold: number | null;
  engine: { status: string; engine: string | null };
  checks_passed: string[];
  checks_failed: string[];
}

export interface TableDecision {
  table: string;
  status: string;
  verified: boolean;
  reasons: string[];
  cells_flagged: string[];
}

export type ReviewStatus = "pending" | "in_review" | "approved" | "rejected" | "auto_accepted";

export interface ReviewItem {
  document_id: string;
  layout_run: string;
  schema_id: string | null;
  revision: number;
  version: number;
  status: ReviewStatus;
  priority: number;
  reasons: string[];
  assessment: {
    calibration_id: string | null;
    eligible: boolean;
    reasons: string[];
    fields: FieldDecision[];
    tables: TableDecision[];
  };
  assigned_to: string | null;
  corrections: Record<string, { action: string; value: Value; reason: string; actor: string; at: string }>;
  decision: { action: string; actor: string; reason: string | null; comment: string | null } | null;
}

export interface ReviewItemSummary {
  document_id: string;
  layout_run: string;
  schema_id: string | null;
  status: ReviewStatus;
  priority: number;
  reasons: string[];
  fields_to_review: number;
  assigned_to: string | null;
  created_at: string;
}

export interface ReviewedResult {
  fields: Record<string, { value: Value; status: string; source: string; corrected_by: string | null }>;
  tables: Record<string, Record<string, Value>[]>;
  checks: CheckResult[];
  unresolved: string[];
  validation: { valid: boolean };
}

export interface HistoryEvent {
  seq: number;
  ts: string;
  kind: string;
  actor: string;
  location: string | null;
  reason: string | null;
  comment: string | null;
  previous_value: Value;
  new_value: Value;
}

export const CORRECTION_REASONS = [
  "ocr_misread",
  "wrong_value_selected",
  "missed_value",
  "value_not_present",
  "ambiguity_resolved",
  "conflict_resolved",
  "normalisation_error",
  "other",
] as const;

export const REJECT_REASONS = [
  "illegible",
  "wrong_document_type",
  "unsupported_document",
  "incomplete_document",
  "duplicate",
  "other",
] as const;

// ------------------------------------------------------------ admin

export interface Overview {
  generated_at: string;
  jobs: Record<string, number>;
  oldest_queued_s: number | null;
  running: {
    id: string;
    document_id: string;
    kind: string;
    current_stage: string | null;
    worker: string | null;
    attempts: number;
    started_at: string | null;
    lease_expires_at: string | null;
    submitted_by: string;
  }[];
  workers: Record<string, number>;
  recent_failures: Job[];
  failures_24h: Record<string, number>;
  documents: Record<string, number>;
  review: Record<string, number>;
  active_sessions: number;
}

export interface AuditEntry {
  id: number;
  ts: string;
  event: string;
  actor: string;
  request_id: string | null;
  document_id: string | null;
  fields: Record<string, unknown>;
}

export interface UserInfo {
  name: string;
  role: Role;
  disabled: boolean;
  locked_until: string | null;
  created_at: string;
  password_changed_at: string;
  last_login_at: string | null;
}
