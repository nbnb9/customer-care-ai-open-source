export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export function getDemoUserId(): string {
  return localStorage.getItem("demo-user-id") ?? "user-nw-customer-01";
}

export function withDemoIdentityHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  headers.set("X-Demo-User-ID", getDemoUserId());
  return headers;
}

export type ApiTraceCorrelation = {
  requestId?: string;
  traceId?: string;
  traceparent?: string;
};

let latestApiTrace: ApiTraceCorrelation | null = null;

function randomHex(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

/**
 * Begin a W3C trace for one browser-to-API operation. The ID is correlation
 * metadata only; it never contains user text, identity data, tokens, or keys.
 */
export function newTraceparent(): string {
  return `00-${randomHex(16)}-${randomHex(8)}-01`;
}

export function getLatestApiTrace(): ApiTraceCorrelation | null {
  return latestApiTrace;
}

function rememberTrace(res: Response): ApiTraceCorrelation {
  const trace = {
    requestId: res.headers.get("x-request-id") ?? undefined,
    traceId: res.headers.get("x-trace-id") ?? undefined,
    traceparent: res.headers.get("traceparent") ?? undefined,
  };
  latestApiTrace = trace;
  return trace;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
    readonly traceId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = withDemoIdentityHeaders(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!headers.has("traceparent")) headers.set("traceparent", newTraceparent());

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  const trace = rememberTrace(res);
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    if (body && typeof body === "object") {
      const errorBody = body as {
        code?: unknown;
        detail?: unknown;
        message?: unknown;
        request_id?: unknown;
      };
      const message = errorBody.message ?? errorBody.detail;
      if (typeof message === "string") {
        throw new ApiError(
          message,
          res.status,
          typeof errorBody.code === "string" ? errorBody.code : undefined,
          typeof errorBody.request_id === "string"
            ? errorBody.request_id
            : trace.requestId,
          trace.traceId,
        );
      }
    }
    throw new ApiError(`HTTP ${res.status}`, res.status, undefined, trace.requestId, trace.traceId);
  }
  return res.json() as Promise<T>;
}
