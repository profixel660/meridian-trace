"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { API_BASE } from "@/lib/api";

export interface MonitorEvent {
  ts: string;
  level: "info" | "warning" | "error" | "debug";
  event: string;
  ctx: Record<string, unknown>;
  project_slug: string | null;
}

export type EventStreamStatus =
  | "connecting"
  | "live"
  | "subscriber_limit"
  | "error";

export interface EventStreamState {
  events: MonitorEvent[];   // newest first, max 200
  status: EventStreamStatus;
  lastEventAt: number | null;
  subscriberLimit: { limit: number; active: number } | null;
}

const MAX_RETAINED = 200;
const RECONNECT_BASE_MS = 3000;
const RECONNECT_MAX_MS = 30000;

type Action =
  | { type: "connecting" }
  | { type: "live" }
  | { type: "log"; payload: MonitorEvent }
  | { type: "heartbeat"; ts: number }
  | { type: "subscriber_limit"; limit: number; active: number }
  | { type: "error" };

function reducer(s: EventStreamState, a: Action): EventStreamState {
  switch (a.type) {
    case "connecting":
      return { ...s, status: "connecting" };
    case "live":
      return { ...s, status: "live" };
    case "log":
      return {
        ...s,
        status: "live",
        lastEventAt: Date.now(),
        events: [a.payload, ...s.events].slice(0, MAX_RETAINED),
      };
    case "heartbeat":
      return { ...s, lastEventAt: a.ts };
    case "subscriber_limit":
      return {
        ...s,
        status: "subscriber_limit",
        subscriberLimit: { limit: a.limit, active: a.active },
      };
    case "error":
      return { ...s, status: "error" };
  }
}

const INITIAL: EventStreamState = {
  events: [],
  status: "connecting",
  lastEventAt: null,
  subscriberLimit: null,
};

/**
 * Consumes /api/projects/{slug}/events as text/event-stream via fetch +
 * ReadableStream. EventSource isn't used because it can't expose HTTP
 * status codes — needed for 503 subscriber-cap detection.
 */
export function useEventStream(projectSlug: string): EventStreamState & {
  retry: () => void;
} {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const retryAttempts = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortController = useRef<AbortController | null>(null);

  const connect = useCallback(async () => {
    if (!projectSlug) return;
    abortController.current?.abort();
    abortController.current = new AbortController();
    dispatch({ type: "connecting" });

    try {
      const res = await fetch(
        `${API_BASE}/projects/${encodeURIComponent(projectSlug)}/events`,
        { signal: abortController.current.signal },
      );
      if (res.status === 503) {
        const body = await res.json().catch(() => ({}));
        dispatch({
          type: "subscriber_limit",
          limit: body?.detail?.limit ?? 0,
          active: body?.detail?.active ?? 0,
        });
        return;
      }
      if (!res.ok || !res.body) {
        dispatch({ type: "error" });
        scheduleReconnect();
        return;
      }
      dispatch({ type: "live" });
      retryAttempts.current = 0;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          dispatch({ type: "error" });
          scheduleReconnect();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          parseFrame(frame, dispatch);
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      dispatch({ type: "error" });
      scheduleReconnect();
    }
  }, [projectSlug]);

  const scheduleReconnect = () => {
    retryAttempts.current += 1;
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** (retryAttempts.current - 1),
      RECONNECT_MAX_MS,
    );
    retryTimer.current = setTimeout(connect, delay);
  };

  const retry = useCallback(() => {
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryAttempts.current = 0;
    void connect();
  }, [connect]);

  useEffect(() => {
    void connect();
    return () => {
      abortController.current?.abort();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, [connect]);

  return { ...state, retry };
}

function parseFrame(frame: string, dispatch: (a: Action) => void): void {
  let event = "log";
  let dataLine = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLine = line.slice(6);
  }
  if (!dataLine) return;
  try {
    const parsed = JSON.parse(dataLine);
    if (event === "heartbeat") {
      dispatch({ type: "heartbeat", ts: Date.now() });
    } else {
      dispatch({ type: "log", payload: parsed as MonitorEvent });
    }
  } catch {
    // malformed frame — ignore
  }
}
