/**
 * useMatchWebSocket — real-time push notification hook.
 *
 * Connects to `ws[s]://host/api/ws/matches/{matchId}?token={token}` and
 * calls `onMessage` each time the server pushes a JSON payload.
 *
 * Features:
 * - Automatic reconnect with exponential back-off (1 s → 2 s → 4 s … 30 s max)
 * - Transparent ping/pong keep-alive every 20 s
 * - Returns `{ connected: boolean }` so callers can fall back to polling
 *   when WS is unavailable
 * - Cleans up on unmount (no leaked sockets or timers)
 *
 * Usage:
 *   const { connected } = useMatchWebSocket(match.id, token, (msg) => {
 *     if (msg.type === 'ticked' || msg.type === 'state_updated') refresh();
 *   });
 */
import { useEffect, useRef, useState, useCallback } from 'react';

export interface WsMessage {
  type: string;
  [key: string]: unknown;
}

/**
 * Derive the WebSocket base URL from the current page URL.
 * http://  → ws://
 * https:// → wss://
 */
function wsBaseUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}`;
}

const PING_INTERVAL_MS = 20_000;
const INITIAL_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

export function useMatchWebSocket(
  matchId: string,
  token: string | null,
  onMessage: (msg: WsMessage) => void,
): { connected: boolean } {
  const [connected, setConnected] = useState(false);

  // Stable ref to the latest onMessage callback — avoids restarting the effect
  // when only the callback reference changes.
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  // Cancellation flag set on unmount or when matchId/token changes.
  const cancelledRef = useRef(false);

  // Ref to the active WebSocket instance so the cleanup function can close it.
  const wsRef = useRef<WebSocket | null>(null);

  // Persistent retry delay across reconnect attempts so back-off actually grows.
  const retryDelayRef = useRef(INITIAL_RETRY_MS);

  const connect = useCallback(() => {
    if (!token || cancelledRef.current) return;

    const url = `${wsBaseUrl()}/api/ws/matches/${matchId}?token=${encodeURIComponent(token)}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      // WebSocket constructor can throw in some environments (e.g. JSDOM in tests).
      return;
    }

    wsRef.current = ws;

    let pingTimer: ReturnType<typeof setInterval> | null = null;

    const cleanup = () => {
      if (pingTimer) clearInterval(pingTimer);
    };

    ws.onopen = () => {
      if (cancelledRef.current) { ws.close(); return; }
      setConnected(true);
      retryDelayRef.current = INITIAL_RETRY_MS; // reset back-off on successful connect
      // Start ping/pong keep-alive
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string) as WsMessage;
        // Skip pong messages — they are only for keep-alive
        if (msg.type !== 'pong') {
          onMessageRef.current(msg);
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = (event) => {
      cleanup();
      setConnected(false);
      if (cancelledRef.current) return;
      // Auth failure — token is invalid or expired; retrying won't help.
      if (event.code === 4401) return;
      // Exponential back-off reconnect
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 2, MAX_RETRY_MS);
      setTimeout(() => {
        if (!cancelledRef.current) connect();
      }, delay);
    };

    ws.onerror = () => {
      // onclose fires right after onerror, so reconnect is handled there.
    };
  }, [matchId, token]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!token) return;
    cancelledRef.current = false;
    retryDelayRef.current = INITIAL_RETRY_MS; // reset back-off when match/token changes
    connect();
    return () => {
      cancelledRef.current = true;
      setConnected(false);
      const ws = wsRef.current;
      if (ws && ws.readyState !== WebSocket.CLOSED) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, [matchId, token, connect]);

  return { connected };
}
