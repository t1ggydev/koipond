"use client";

import { useEffect } from "react";

const ACTIVE_WINDOW_MS = 5 * 60 * 1000;
const HEARTBEAT_MS = 60 * 1000;

export function ActivityHeartbeat() {
  useEffect(() => {
    let lastActivity = Date.now();
    let lastSent = 0;
    let stopped = false;
    let running = false;
    let activeRequest: AbortController | null = null;
    async function heartbeat() {
      const now = Date.now();
      if (stopped || running || document.visibilityState !== "visible" ||
          now - lastActivity > ACTIVE_WINDOW_MS || now - lastSent < HEARTBEAT_MS) return;
      running = true;
      lastSent = now;
      activeRequest = new AbortController();
      try {
        const response = await fetch("/api/activity", {
          method: "POST", credentials: "same-origin", cache: "no-store",
          signal: AbortSignal.any([activeRequest.signal, AbortSignal.timeout(5000)]),
        });
        if (response.status === 401) stopped = true;
      } catch {
        // Activity telemetry must never disrupt a giveaway or its UI.
      } finally {
        running = false;
        activeRequest = null;
      }
    }
    function onActivity() {
      lastActivity = Date.now();
      void heartbeat();
    }
    function onVisibilityChange() {
      if (document.visibilityState === "visible") onActivity();
    }
    const events = ["pointerdown", "keydown", "touchstart"] as const;
    for (const event of events) document.addEventListener(event, onActivity, { passive: true });
    document.addEventListener("visibilitychange", onVisibilityChange);
    const timer = setInterval(() => { void heartbeat(); }, HEARTBEAT_MS);
    void heartbeat();
    return () => {
      stopped = true;
      clearInterval(timer);
      activeRequest?.abort();
      for (const event of events) document.removeEventListener(event, onActivity);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);
  return null;
}
