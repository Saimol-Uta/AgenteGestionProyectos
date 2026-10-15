import { useCallback, useMemo, useSyncExternalStore, type Dispatch, type SetStateAction } from "react";

const prefix = "gestionpro.phase1.v1:";
const changed = "gestionpro:demo-state";
const volatile = new Map<string, string>();

export function demoStorageKey(scope: string) {
  return `${prefix}${encodeURIComponent(scope)}`;
}

function read(key: string): string | null {
  if (volatile.has(key)) return volatile.get(key)!;
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(changed, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(changed, listener);
  };
}

/** Browser-only demonstration state; never a server authorization or execution receipt. */
export function useDemoState<T>(scope: string, initial: T, validate: (value: unknown) => value is T): [T, Dispatch<SetStateAction<T>>] {
  const key = demoStorageKey(scope);
  const snapshot = useCallback(() => read(key), [key]);
  const raw = useSyncExternalStore(subscribe, snapshot, () => null);
  const decode = useCallback((value: string | null): T => {
    if (value !== null) {
      try {
        const parsed: unknown = JSON.parse(value);
        if (validate(parsed)) return parsed;
      } catch { /* Malformed browser state is not authoritative. */ }
    }
    return initial;
  }, [initial, validate]);
  const state = useMemo(() => decode(raw), [decode, raw]);
  const setState = useCallback<Dispatch<SetStateAction<T>>>((next) => {
    const current = decode(read(key));
    const value = typeof next === "function" ? (next as (previous: T) => T)(current) : next;
    if (!validate(value)) return;
    const serialized = JSON.stringify(value);
    try {
      window.localStorage.setItem(key, serialized);
      volatile.delete(key);
    } catch { volatile.set(key, serialized); }
    window.dispatchEvent(new Event(changed));
  }, [decode, key, validate]);
  return [state, setState];
}
