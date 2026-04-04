const BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? window.location.origin;

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
 * - parses JSON responses
 */
export async function fetchApi<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE_URL}${path}`;

  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, { ...options, headers });

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = await response.text();
    }
    throw new ApiError(response.status, response.statusText, body);
  }

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
