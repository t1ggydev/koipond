import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";

const execution = new AsyncLocalStorage<AbortSignal>();

export function withExecutionSignal<T>(signal: AbortSignal, operation: () => Promise<T>): Promise<T> {
  return execution.run(signal, operation);
}

export const boundedFetch: typeof fetch = (input, init) => {
  const signals = [AbortSignal.timeout(8000)];
  const active = execution.getStore();
  if (active) signals.push(active);
  if (init?.signal) signals.push(init.signal);
  return fetch(input, { ...init, signal: AbortSignal.any(signals) });
};
