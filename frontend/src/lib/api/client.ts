const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8001";
const TOKEN_KEY = "analyst_copilot_token";

/** Thrown for any non-2xx response, carrying enough to render a good message. */
export class ApiError extends Error {
  readonly status: number;
  readonly hint?: string;

  constructor(message: string, status: number, hint?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.hint = hint;
  }

  /** The provider's usage limit is exhausted - temporary, and worth saying plainly. */
  get isRateLimited(): boolean {
    return this.status === 429;
  }

  /** No API key configured server-side; the app cannot answer anything. */
  get isUnconfigured(): boolean {
    return this.status === 503;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Private browsing: auth simply won't survive a reload.
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function toError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;

  // FastAPI validation errors arrive as a list of field problems; surface
  // the messages rather than "[object Object]".
  if (Array.isArray(detail)) {
    const message = detail
      .map((d: { msg?: string }) => d?.msg?.replace(/^Value error, /, ""))
      .filter(Boolean)
      .join(", ");
    return new ApiError(message || "That request was not valid.", response.status);
  }

  return new ApiError(
    typeof detail === "string" ? detail : "Something went wrong.",
    response.status,
    body?.hint,
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(
      "Can't reach the server. Is the backend running?",
      0,
      "Start it with: uvicorn app.main:app --port 8001",
    );
  }

  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { headers: headers() }),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  patch: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PATCH",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    }),

  del: <T>(path: string) => request<T>(path, { method: "DELETE", headers: headers() }),

  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", headers: headers(), body: form });
  },
};

export function fileUrl(path: string): string {
  return `${API_BASE}${path}`;
}
