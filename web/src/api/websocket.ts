import { getWsBaseUrl } from "./client.ts";

export interface JobWebSocketEvent {
  type: string;
  job_id?: string;
  status?: string;
  progress?: number;
  error_message?: string;
  [key: string]: unknown;
}

export interface WebSocketHandle {
  /** Gracefully close the connection (no auto-reconnect). */
  close: () => void;
}

const PING_INTERVAL_MS = 30_000;
const INITIAL_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;

/**
 * Open a WebSocket to /ws/jobs with:
 * - automatic reconnection using exponential backoff
 * - keep-alive pings every 30 seconds
 *
 * Returns a handle whose `close()` method tears everything down.
 */
export function createJobsWebSocket(
  onEvent: (event: JobWebSocketEvent) => void,
): WebSocketHandle {
  let ws: WebSocket | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectDelay = INITIAL_RECONNECT_MS;
  let disposed = false;

  function clearTimers(): void {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect(): void {
    if (disposed) return;
    reconnectTimer = setTimeout(() => {
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_MS);
  }

  function connect(): void {
    if (disposed) return;

    const base = getWsBaseUrl();
    ws = new WebSocket(`${base}/ws/jobs`);

    ws.addEventListener("open", () => {
      reconnectDelay = INITIAL_RECONNECT_MS;
      pingTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send("ping");
        }
      }, PING_INTERVAL_MS);
    });

    ws.addEventListener("message", (event: MessageEvent) => {
      if (typeof event.data !== "string") return;
      if (event.data === "pong") return;

      try {
        const parsed = JSON.parse(event.data) as JobWebSocketEvent;
        onEvent(parsed);
      } catch {
        // Non-JSON message -- ignore.
      }
    });

    ws.addEventListener("close", () => {
      clearTimers();
      scheduleReconnect();
    });

    ws.addEventListener("error", () => {
      // The close event will fire after error, triggering reconnect.
      ws?.close();
    });
  }

  connect();

  return {
    close() {
      disposed = true;
      clearTimers();
      if (ws) {
        ws.close();
        ws = null;
      }
    },
  };
}
