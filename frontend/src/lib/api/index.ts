import { api, fileUrl } from "./client";
import type {
  AnswerResult, AuthResult, Filing, LLMStatus, Message, Session, User,
} from "../types";

export { ApiError, getToken, setToken, fileUrl } from "./client";

export const filings = {
  list: (archived = false) => api.get<Filing[]>(`/filings?archived=${archived}`),
  get: (id: string) => api.get<Filing>(`/filings/${id}`),
  upload: (file: File) => api.upload<Filing>("/filings", file),
  rename: (id: string, name: string) => api.patch<Filing>(`/filings/${id}`, { name }),
  archive: (id: string) => api.post<Filing>(`/filings/${id}/archive`),
  unarchive: (id: string) => api.post<Filing>(`/filings/${id}/unarchive`),
  remove: (id: string) => api.del<void>(`/filings/${id}`),
  restore: (id: string) => api.post<Filing>(`/filings/${id}/restore`),
  /**
   * The `#page` anchor is what makes a citation land on the right page.
   * `toolbar`/`navpanes` suppress the browser viewer's own chrome, which
   * would otherwise spend a third of a narrow drawer on a thumbnail rail
   * and a duplicate page control.
   */
  pdfUrl: (id: string, page?: number | null) =>
    fileUrl(
      `/filings/${id}/pdf#page=${page ?? 1}&toolbar=0&navpanes=0&statusbar=0&view=FitH`,
    ),
  /**
   * HTML filings have no built-in viewer to hand a page anchor to, so the
   * server renders one page at a time. That also keeps the drawer from
   * pulling a multi-megabyte document down to show a single page.
   */
  pageUrl: (id: string, page?: number | null) =>
    fileUrl(`/filings/${id}/page/${page ?? 1}`),
};

export const chat = {
  ask: (filingId: string, question: string, sessionId?: number | null) =>
    api.post<AnswerResult>("/chat/ask", {
      filing_id: filingId,
      question,
      session_id: sessionId ?? null,
    }),
  sessions: (filingId?: string) =>
    api.get<Session[]>(`/chat/sessions${filingId ? `?filing_id=${filingId}` : ""}`),
  messages: (sessionId: number) => api.get<Message[]>(`/chat/sessions/${sessionId}/messages`),
  renameSession: (sessionId: number, title: string) =>
    api.patch<Session>(`/chat/sessions/${sessionId}`, { title }),
  deleteSession: (sessionId: number) => api.del<void>(`/chat/sessions/${sessionId}`),
  feedback: (messageId: number, feedback: number | null) =>
    api.post<void>(`/chat/messages/${messageId}/feedback`, { feedback }),
  search: (q: string) => api.get<Message[]>(`/chat/search?q=${encodeURIComponent(q)}`),
};

export const auth = {
  signup: (email: string, display_name: string, password: string) =>
    api.post<AuthResult>("/auth/signup", { email, display_name, password }),
  login: (email: string, password: string) =>
    api.post<AuthResult>("/auth/login", { email, password }),
  me: () => api.get<User | null>("/auth/me"),
  updateName: (display_name: string) => api.patch<User>("/auth/me", { display_name }),
  changePassword: (current_password: string, new_password: string) =>
    api.post<void>("/auth/me/password", { current_password, new_password }),
};

export const settings = {
  llm: () => api.get<LLMStatus>("/settings/llm"),
  testLlm: () => api.post<LLMStatus>("/settings/llm/test"),
};
