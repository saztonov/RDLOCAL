const BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? window.location.origin;

const TOKEN_KEY = "api_token";

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    super(`API ${status}: ${statusText}`);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

/**
 * Thin wrapper around `fetch` that:
 * - prepends BASE_URL to relative paths
 * - attaches the Bearer token from localStorage
 * - parses JSON responses
 * - redirects to /login on 401
 */
export async function fetchApi<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE_URL}${path}`;

  const headers = new Headers(options.headers);
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.location.href = "/login";
    throw new ApiError(401, "Unauthorized", null);
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = await response.text();
    }
    throw new ApiError(response.status, response.statusText, body);
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/** Return the WebSocket base URL derived from the current API base. */
export function getWsBaseUrl(): string {
  const url = new URL(BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.origin;
}
