"use client";

import { useEffect, useRef } from "react";

import type { LiveEvent } from "./types";

const EVENT_TYPES = ["stage", "run_status", "batch_progress", "batch_status", "end"] as const;

/**
 * Subscribe to a server-sent event stream (docs/GUIDE.md "UI", Implementation).
 *
 * The caller loads stored state first, then passes the stream URL (for a run, with
 * `after=<last stored event id>` so nothing is replayed twice). The browser resumes a dropped
 * connection by itself, sending Last-Event-ID; if it gives up, we reopen after a pause. The
 * stream closes on the server's `end` event. Pass `url = null` to stay disconnected.
 */
export function useEventStream(url: string | null, onEvent: (event: LiveEvent) => void) {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });

  useEffect(() => {
    if (!url) return;
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let finished = false;

    const open = (streamUrl: string) => {
      source = new EventSource(streamUrl);
      for (const type of EVENT_TYPES) {
        source.addEventListener(type, (message) => {
          const event = JSON.parse((message as MessageEvent).data) as LiveEvent;
          if (event.type === "end") {
            finished = true;
            source?.close();
          }
          handler.current(event);
        });
      }
      source.onerror = () => {
        // CONNECTING = the browser is already retrying; CLOSED = it gave up, so we retry.
        if (!finished && source?.readyState === EventSource.CLOSED) {
          retry = setTimeout(() => open(streamUrl), 2000);
        }
      };
    };

    open(url);
    return () => {
      finished = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [url]);
}
