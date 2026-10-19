import type { WorkItem } from "./types";

export const API_URL: string = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "ops-hub-token";
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}
export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: memory copy still works for this tab */
  }
}

/** Error body shape from the API: {"error": {"code", "message", ...extras}} */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, any> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
  get isConflict(): boolean {
    return this.status === 409;
  }
  get isVersionConflict(): boolean {
    return this.status === 409 && this.code === "version_conflict";
  }
  /** Fresh server copy of the item, present on version conflicts so the client can reconcile. */
  get current(): WorkItem | undefined {
    return this.extra.current;
  }
  get currentOwner(): { id: number; name: string } | undefined {
    return this.extra.current_owner;
  }
}

export function newIdempotencyKey(): string {
  const c: any = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function parseError(res: Response): Promise<ApiError> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const err = body?.error;
  if (err && typeof err === "object") {
    const { code, message, ...extra } = err;
    return new ApiError(res.status, code ?? "error", message ?? res.statusText, extra);
  }
  if (Array.isArray(body?.detail)) {
    // FastAPI request-validation errors
    const first = body.detail[0];
    return new ApiError(res.status, "validation_error", first?.msg ?? "Invalid request", { detail: body.detail });
  }
  return new ApiError(res.status, "error", res.statusText || `HTTP ${res.status}`);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | string[] | undefined | null>;
  /** Send an Idempotency-Key. One key per user action; the same key is reused if the request is retried. */
  idempotent?: boolean;
  /** Network-level retries (safe only because of the idempotency key on mutations). */
  retries?: number;
}

export function buildQuery(query: RequestOptions["query"]): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => params.append(k, x));
    else params.append(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotent) headers["Idempotency-Key"] = newIdempotencyKey();

  const url = `${API_URL}${path}${buildQuery(opts.query)}`;
  const attempts = (opts.retries ?? (opts.idempotent ? 2 : 0)) + 1;
  let lastNetworkError: unknown;
  for (let i = 0; i < attempts; i++) {
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
    } catch (e) {
      lastNetworkError = e; // fetch rejects only on network failure; loop and retry with the SAME headers/key
      continue;
    }
    if (res.status === 401 && token) {
      setToken(null);
      window.dispatchEvent(new Event("auth:logout"));
    }
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as T;
  }
  throw new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again.", {
    cause: String(lastNetworkError),
  });
}
