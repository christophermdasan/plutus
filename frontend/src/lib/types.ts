export type FilingStatus =
  | "queued" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";

export interface Filing {
  id: string;
  original_name: string;
  display_title: string;
  status: FilingStatus;
  status_label: string;
  num_pages: number | null;
  size_bytes: number | null;
  company_name: string | null;
  filing_type: string | null;
  fiscal_period: string | null;
  suggested_questions: string[];
  error: string | null;
  is_archived: boolean;
  created_at: string | null;
  /** Which viewer can display this filing's pages. */
  media_kind: "pdf" | "html";
}

export interface Considered {
  page: number;
  excerpt: string;
  score: number;
}

/** An answer as returned by /chat/ask. */
export interface AnswerResult {
  message_id: number;
  session_id: number;
  question: string;
  found: boolean;
  answer: string;
  page: number | null;
  quote: string;
  reason: string;
  considered: Considered[];
  latency_ms: number;
  model: string | null;
}

/** A stored turn as returned by history. */
export interface Message {
  id: number;
  session_id: number;
  question: string;
  answer: string;
  found: boolean;
  page: number | null;
  quote: string;
  reason: string;
  latency_ms: number;
  feedback: number | null;
  created_at: string | null;
}

export interface Session {
  id: number;
  filing_id: string;
  title: string;
  message_count: number;
  updated_at: string | null;
}

export interface User {
  id: number;
  email: string;
  display_name: string;
}

export interface AuthResult {
  token: string;
  user: User;
}

export interface LLMStatus {
  configured: boolean;
  model: string;
  base_url: string;
  ok: boolean | null;
  message: string | null;
  latency_ms: number | null;
}

/** What the source drawer needs to show a citation. */
export interface SourceRef {
  filingId: string;
  filingName: string;
  page: number;
  quote: string;
  question?: string;
  answer?: string;
}

export const ACTIVE_STATUSES: FilingStatus[] = [
  "queued", "parsing", "chunking", "embedding", "indexing",
];
